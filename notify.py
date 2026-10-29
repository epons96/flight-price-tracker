"""Sends a digest of the report to a Telegram chat: cheapest dates, the last day's changes and when
prices move. Needs TELEGRAM_TOKEN and TELEGRAM_CHAT_ID (setup_telegram.py stores them as GitHub
secrets); without them it does nothing."""

import argparse
import html
import json
import os
import sys

import pandas as pd

import analyze
import settings
from analyze import MONTHS, fmt_date, money
from telegram_api import TelegramError, call

LEVELS = 3  # cheapest prices listed with their dates
MAX_RANGES = 8  # date ranges shown per price
LEAD_BINS = [0, 30, 90, 10_000]
LEAD_LABELS = ["up to 30 days ahead", "31-90 days", "over 90 days"]


def short_date(d):
    return f"{d.day} {MONTHS[d.month - 1]}"


def date_ranges(dates, departures):
    """Joins dates into ranges of consecutive departures: days without a flight don't break a range."""
    position = {d: i for i, d in enumerate(departures)}
    ranges = []
    for d in sorted(dates):
        if ranges and position[d] == position[ranges[-1][1]] + 1:
            ranges[-1][1] = d
        else:
            ranges.append([d, d])
    return [short_date(a) if a == b else f"{a.day}–{short_date(b)}" if a.month == b.month
            else f"{short_date(a)} – {short_date(b)}" for a, b in ranges]


def days_ahead(df):
    return pd.cut((df["outbound_date"] - df["checked"].dt.normalize()).dt.days, LEAD_BINS, labels=LEAD_LABELS)


def digest(cfg, df, report_url):
    names = df.groupby("airline")["airline_name"].last().to_dict()
    latest = df["checked"].max()
    current = analyze.current_prices(df)
    stays = ", ".join(str(s) for s in cfg["stay_days"])
    lines = [f"<b>✈️ {html.escape(cfg['origin'])} ⇄ {html.escape(cfg['destination'])}</b> · round trip, "
             f"{stays}-day stay · {fmt_date(latest)}, {latest:%H:%M}", ""]
    if current.empty:
        return "\n".join(lines + ["No upcoming prices in the last check."])

    best = current.loc[current.groupby("outbound_date")["price"].idxmin()]  # cheapest airline on each date
    top = best.loc[best["price"].idxmin()]
    floor = df["price"].min()
    note = "the lowest seen so far" if top["price"] <= floor else f"lowest ever seen: {money(floor)}"
    lines.append(f"Cheapest now: <b>{money(top['price'])}</b> with "
                 f"{html.escape(names.get(top['airline'], top['airline']))} ({note})")

    lines += ["", "<b>Cheapest departures</b>"]
    departures = list(best["outbound_date"])
    for price in sorted(best["price"].unique())[:LEVELS]:
        dates = best.loc[best["price"] == price, "outbound_date"]
        ranges = date_ranges(dates, departures)
        more = f" and {len(ranges) - MAX_RANGES} more" if len(ranges) > MAX_RANGES else ""
        lines.append(f"<b>{money(price)}</b> · {len(dates)} dates: {', '.join(ranges[:MAX_RANGES])}{more}")

    # Against the price a day earlier, not each change: near dates can go up and back down in a day.
    earlier = df[df["checked"] <= latest - pd.Timedelta(hours=23)]
    if not earlier.empty:
        then = earlier.groupby(analyze.KEY)["price"].last().rename("then")
        moved = current.join(then, on=analyze.KEY).dropna(subset=["then"])
        moved = moved.assign(delta=moved["price"] - moved["then"])
        drops, rises = moved[moved["delta"] < 0], moved[moved["delta"] > 0]
        lines += ["", f"<b>Since yesterday:</b> {len(drops)} dates cheaper, {len(rises)} dearer"]
        for arrow, rows in (("▼", drops.nsmallest(3, "delta")), ("▲", rises.nlargest(3, "delta"))):
            lines += [f"{arrow} {fmt_date(r.outbound_date)}: {money(r.then)} → {money(r.price)}"
                      for r in rows.itertuples()]

    lines += ["", "<b>When prices move</b>"]
    level, days_level = analyze.level_by_hour(df)
    if level is not None and days_level >= analyze.MIN_DAYS_BY_HOUR:
        h = int(level.idxmin())
        lines.append(f"Cheapest hour to check: {h:02}:00 ({analyze.pct(level[h])} against that day's median)")
    changes = analyze.detect_changes(df)
    precise = changes[changes["precise"]]
    by_hour = {sign: precise[precise["delta"] * sign > 0].groupby(precise["checked"].dt.hour).size()
               for sign in (-1, 1)}
    if len(precise) >= analyze.MIN_CHANGES and len(by_hour[-1]) and len(by_hour[1]):
        lines.append(f"Most drops are detected at {by_hour[-1].idxmax():02}:00 and most rises at "
                     f"{by_hour[1].idxmax():02}:00 (local time)")
    else:
        lines.append(f"Not enough changes yet to tell the hours ({len(precise)} of {analyze.MIN_CHANGES}).")
    # Far-off dates hardly move, so the hour of the check only matters for the near ones.
    week = changes[(changes["checked"] > latest - pd.Timedelta(days=7))
                   & (changes["outbound_date"] > latest.normalize())]
    per_change, per_date = days_ahead(week).value_counts(), days_ahead(current).value_counts()
    rates = [f"{label} {per_change[label] / per_date[label]:.1f}" for label in LEAD_LABELS if per_date[label]]
    lines.append("Changes per departure date in the last 7 days: " + " · ".join(rates))

    if report_url:
        lines += ["", f'<a href="{report_url}">Full report</a>']
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="print the message instead of sending it")
    args = p.parse_args()
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not args.dry_run and not (token and chat):
        print("Telegram is not set up (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID): nothing sent.")
        return

    cfg = settings.load()
    analyze.SYMBOL = analyze.SYMBOLS.get(cfg["currency"], cfg["currency"] + " ")
    df = analyze.load(analyze.ROOT / "data")
    if df is None:
        print("No data yet: nothing sent.")
        return
    server, repo, branch = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_REF_NAME"))
    report_url = f"{server}/{repo}/blob/{branch}/report/REPORT.md" if server and repo and branch else None
    text = digest(cfg, df, report_url)
    chart = analyze.ROOT / "report" / "charts" / "calendar-light.png"

    # The message is never printed in GitHub Actions: its logs are public and it names the route.
    if args.dry_run:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
        return
    try:
        # Chart first and silent, so the notification shows the text.
        if chart.exists():
            call(token, "sendPhoto", {"chat_id": chat, "caption": "Current price by departure date",
                                      "disable_notification": "true"}, files={"photo": chart})
        call(token, "sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML",
                                    "link_preview_options": json.dumps({"is_disabled": True})})
    except TelegramError as e:
        sys.exit(f"Telegram: {e}")
    print("Digest sent to Telegram.")


if __name__ == "__main__":
    main()
