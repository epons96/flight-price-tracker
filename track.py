"""Looks up on Google Flights the round-trip price of the configured route for every airline and
departure date in the window, and appends it to the month's CSV in data/."""

import csv
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fast_flights import FlightQuery, Passengers, create_query
from primp import Client
from selectolax.lexbor import LexborHTMLParser

import settings

ROOT = Path(__file__).parent
DATA = ROOT / "data"
URL = "https://www.google.com/travel/flights"
# Without this cookie Google answers EU visitors with the consent wall instead of the flights.
COOKIES = {"SOCS": "CAI"}
PASSES = (6, 2, 2)  # threads per pass; later passes only retry the dates that came back empty
RETRIES = 2

PRICE_FIELDS = ["checked_utc", "checked_local", "airline", "airline_name", "outbound_date", "return_date",
                "stay", "price", "currency", "outbound_stops", "options"]
RUN_FIELDS = ["checked_utc", "checked_local", "airline", "queries", "priced", "unpriced",
              "no_flight", "errors", "seconds"]


class UnexpectedResponse(Exception):
    """Google returned something other than the results page (captcha, block, format change)."""


def search(cfg, airline, outbound_date, return_date):
    """Outbound options with the total round-trip price (None when Google has no fare).
    Empty list when the airline has no flight on those dates."""
    query = create_query(
        flights=[
            FlightQuery(date=outbound_date.isoformat(), from_airport=cfg["origin"],
                        to_airport=cfg["destination"], airlines=[airline]),
            FlightQuery(date=return_date.isoformat(), from_airport=cfg["destination"],
                        to_airport=cfg["origin"], airlines=[airline]),
        ],
        trip="round-trip",
        passengers=Passengers(adults=cfg["adults"]),
        language="en",
        currency=cfg["currency"],
    )
    client = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True)
    resp = client.get(URL, params={**query.params(), "gl": cfg["country"]}, cookies=COOKIES)
    script = LexborHTMLParser(resp.text).css_first(r"script.ds\:1")
    if script is None:
        # No query string: Actions logs are public and the query encodes the route.
        where = urlsplit(resp.url)
        raise UnexpectedResponse(f"HTTP {resp.status_code} at {where.netloc}{where.path}")
    payload = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if payload.endswith("errorHasStatus: true"):
        return []
    results = json.loads(payload)[3]
    if not results or not results[0]:
        return []

    # Format reverse-engineered by fast-flights; parsed by hand because its parser crashes on
    # unpriced options, which some airlines have on Google Flights.
    options = []
    for flight, fare in (r[:2] for r in results[0]):
        options.append({
            "price": fare[0][1] if fare and fare[0] and len(fare[0]) > 1 else None,
            "airline_name": ", ".join(flight[1] or []) or airline,
            "outbound_stops": len(flight[2]) - 1,
        })
    return options


def observe(cfg, airline, outbound_date, stay):
    return_date = outbound_date + dt.timedelta(days=stay)
    for attempt in range(RETRIES + 1):
        try:
            options = search(cfg, airline, outbound_date, return_date)
            break
        except Exception as e:
            error = e
            time.sleep(2 * (attempt + 1))
    else:
        return "error", f"{airline} {outbound_date} +{stay}d: {type(error).__name__}: {error}"

    if not options:
        return "no_flight", None
    priced = [o for o in options if o["price"] is not None]
    if not priced:
        return "unpriced", None
    best = min(priced, key=lambda o: o["price"])
    return "ok", {
        "airline": airline,
        "outbound_date": outbound_date.isoformat(),
        "return_date": return_date.isoformat(),
        "stay": stay,
        "currency": cfg["currency"],
        "options": len(options),
        **best,
    }


def append_csv(path, fields, rows):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def main():
    cfg = settings.load()
    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    now_local = now.astimezone(ZoneInfo(cfg["timezone"]))
    stamp = {"checked_utc": now.strftime("%Y-%m-%dT%H:%MZ"),
             "checked_local": now_local.strftime("%Y-%m-%d %H:%M")}

    tasks = [(a, now_local.date() + dt.timedelta(days=d), s)
             for a in cfg["airlines"]
             for d in range(1, cfg["window_days"] + 1)
             for s in cfg["stay_days"]]
    start = time.monotonic()
    # From GitHub's servers Google randomly returns the empty results page for dates that do have
    # flights (more often the faster you ask), so empty dates are retried more slowly.
    results = [None] * len(tasks)
    pending = list(range(len(tasks)))
    empty_after_pass = []
    for threads in PASSES:
        with ThreadPoolExecutor(threads) as ex:
            for i, r in zip(pending, ex.map(lambda i: observe(cfg, *tasks[i]), pending)):
                if results[i] is None or r[0] != "no_flight":
                    results[i] = r
        pending = [i for i in pending if results[i][0] == "no_flight"]
        empty_after_pass.append(len(pending))
    seconds = round(time.monotonic() - start, 1)

    rows, summary, errors = [], [], []
    for airline in cfg["airlines"]:
        own = [r for t, r in zip(tasks, results) if t[0] == airline]
        states = [state for state, _ in own]
        rows += [{**stamp, **r} for state, r in own if state == "ok"]
        errors += [r for state, r in own if state == "error"]
        summary.append({**stamp, "airline": airline, "queries": len(own),
                        "priced": states.count("ok"), "unpriced": states.count("unpriced"),
                        "no_flight": states.count("no_flight"), "errors": states.count("error"),
                        "seconds": seconds})

    DATA.mkdir(exist_ok=True)
    append_csv(DATA / f"prices-{now_local:%Y-%m}.csv", PRICE_FIELDS, rows)
    append_csv(DATA / "runs.csv", RUN_FIELDS, summary)

    print(f"{stamp['checked_local']} (local time), {len(tasks)} queries in {seconds}s; "
          f"empty after each pass: {' -> '.join(map(str, empty_after_pass))}")
    for s in summary:
        print(f"  {s['airline']}: {s['priced']} priced, {s['unpriced']} unpriced, "
              f"{s['no_flight']} no flight, {s['errors']} errors")
    if rows:
        cheapest = min(rows, key=lambda r: r["price"])
        print(f"  Cheapest: {cheapest['airline']} {cheapest['outbound_date']} -> {cheapest['return_date']} "
              f"at {cheapest['price']} {cheapest['currency']}")
    for e in errors[:5]:
        print("  error:", e)
    # The window always has some priced flight: none at all means Google is blocking us or changed its format.
    if not rows:
        sys.exit(1)


if __name__ == "__main__":
    main()
