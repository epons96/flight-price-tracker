"""Analyzes the prices stored in data/ and writes report/REPORT.md with its charts."""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, MaxNLocator

import settings

ROOT = Path(__file__).parent
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}
SYMBOL = "€"  # report() sets it to the configured currency
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAYS_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
KEY = ["airline", "outbound_date", "return_date"]
# A change seen after a longer gap (missed runs) cannot be pinned to a specific hour.
MAX_GAP = pd.Timedelta(hours=3)
# Below these, the report shows the data but draws no conclusions.
MIN_CHANGES = 20
MIN_DAYS_BY_HOUR = 3
MIN_DAYS_BY_WEEKDAY = 14  # every weekday seen at least twice
MIN_DAYS_LEAD_TIME = 14

THEMES = {
    "light": dict(surface="#fcfcfb", text="#0b0b0b", secondary="#52514e", muted="#898781",
                  grid="#e1e0d9", axis="#c3c2b7", down="#2a78d6", up="#e34948",
                  series=["#2a78d6", "#eb6834"]),
    "dark": dict(surface="#1a1a19", text="#f0efec", secondary="#c3c2b7", muted="#898781",
                 grid="#2c2c2a", axis="#383835", down="#3987e5", up="#e66767",
                 series=["#3987e5", "#d95926"]),
}


def money(v):
    return f"{'-' if v < 0 else ''}{SYMBOL}{abs(v):,.0f}"


def pct(v):
    return f"{v:+.1f}%"


def fmt_date(d):
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d:%y}"


def table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(x) for x in row) + " |" for row in rows]
    return "\n".join(lines)


# --- Data ------------------------------------------------------------------------------------

def load(data):
    files = sorted(data.glob("prices-*.csv"))
    if not files:
        return None
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["checked"] = pd.to_datetime(df["checked_local"])
    df["outbound_date"] = pd.to_datetime(df["outbound_date"])
    df["return_date"] = pd.to_datetime(df["return_date"])
    return df.sort_values("checked", kind="stable").reset_index(drop=True)


def detect_changes(df):
    g = df.groupby(KEY)
    df = df.assign(previous=g["price"].shift(), previous_check=g["checked"].shift())
    c = df[df["previous"].notna() & (df["price"] != df["previous"])].copy()
    c["delta"] = c["price"] - c["previous"]
    c["pct"] = c["delta"] / c["previous"] * 100
    c["precise"] = (c["checked"] - c["previous_check"]) <= MAX_GAP
    return c


def level_by_hour(df):
    """Mean deviation (%) of the price from the median of the same itinerary on the same day.
    Comparing against its own day isolates the hour effect from the trend over time."""
    d = df.assign(day=df["checked"].dt.date, hour=df["checked"].dt.hour)
    group = KEY + ["day"]
    d = d[d.groupby(group)["price"].transform("size") >= 6]
    if d.empty:
        return None, 0
    d = d.assign(dev=d["price"] / d.groupby(group)["price"].transform("median") * 100 - 100)
    return d.groupby("hour")["dev"].mean().reindex(range(24)), d["day"].nunique()


def lead_time_curve(df):
    """How far above its own observed minimum each itinerary is, by days left before departure."""
    d = df.assign(days=(df["outbound_date"] - df["checked"].dt.normalize()).dt.days)
    d["over_min"] = d["price"] / d.groupby(KEY)["price"].transform("min") * 100 - 100
    # Each lead time comes from different flights; a weekly rolling mean removes that noise.
    return d.groupby("days")["over_min"].median().rolling(7, center=True, min_periods=3).mean()


# --- Charts ----------------------------------------------------------------------------------

def style(fig, ax, c):
    fig.set_facecolor(c["surface"])
    ax.set_facecolor(c["surface"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(c["axis"])
    ax.tick_params(colors=c["muted"], labelcolor=c["secondary"], length=0, labelsize=9)
    ax.grid(axis="y", color=c["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.margins(y=0.12)  # room for the labels on the extremes
    ax.yaxis.label.set_color(c["secondary"])
    ax.xaxis.label.set_color(c["secondary"])


def legend(ax, c, handles):
    ax.legend(handles=handles, frameon=False, labelcolor=c["secondary"], fontsize=9,
              loc="lower left", bbox_to_anchor=(0, 1.0), ncol=len(handles), handlelength=1.2)


def label(ax, c, text, x, y, below=False):
    ax.annotate(text, (x, y), xytext=(0, -13 if below else 4), textcoords="offset points",
                ha="center", color=c["text"], fontsize=9)


def figure(folder, name, alt, draw):
    """Draws the chart in light and dark theme and returns the <picture> that picks one by GitHub's theme."""
    for theme, c in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 3.4), dpi=150)
        style(fig, ax, c)
        draw(ax, c)
        fig.tight_layout()
        fig.savefig(folder / f"{name}-{theme}.png", facecolor=c["surface"])
        plt.close(fig)
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="charts/{name}-dark.png">'
            f'<img alt="{alt}" src="charts/{name}-light.png"></picture>')


def draw_level(series):
    def f(ax, c):
        vals = series.fillna(0)
        ax.bar(vals.index, vals, width=0.6, color=[c["down"] if v < 0 else c["up"] for v in vals])
        ax.axhline(0, color=c["axis"], linewidth=1)
        ax.set_xticks(range(0, 24, 2), [f"{h:02}:00" for h in range(0, 24, 2)])
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: pct(v)))
        if vals.min() < 0:
            label(ax, c, pct(vals.min()), vals.idxmin(), vals.min(), below=True)
        legend(ax, c, [Patch(color=c["down"], label="cheaper than the day's median"),
                       Patch(color=c["up"], label="more expensive")])
    return f


def draw_changes(rises, drops, labels, step=1):
    def f(ax, c):
        x = range(len(labels))
        ax.bar(x, rises, width=0.6, color=c["up"])
        ax.bar(x, [-v for v in drops], width=0.6, color=c["down"])
        ax.axhline(0, color=c["axis"], linewidth=1)
        ax.set_xticks(list(x)[::step], labels[::step])
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{abs(v):.0f}"))
        ax.set_ylabel("number of changes", fontsize=9)
        if max(drops) > 0:
            i = max(range(len(drops)), key=lambda k: drops[k])
            label(ax, c, str(drops[i]), i, -drops[i], below=True)
        legend(ax, c, [Patch(color=c["up"], label="rises"), Patch(color=c["down"], label="drops")])
    return f


def draw_calendar(current, airlines, names):
    def f(ax, c):
        present = [a for a in airlines if a in set(current["airline"])]
        width = 0.8 / len(present)
        handles = []
        for i, a in enumerate(present):
            d = current[current["airline"] == a]
            x = mdates.date2num(d["outbound_date"]) + (i - (len(present) - 1) / 2) * width
            color = c["series"][airlines.index(a) % len(c["series"])]
            ax.bar(x, d["price"], width=width * 0.85, color=color)
            # Only where it used to be cheaper: a tick on top of the bar adds nothing.
            before = (d["lowest"] < d["price"]).to_numpy()
            ax.scatter(x[before], d["lowest"][before], marker="_", s=40, color=c["text"], linewidths=1.4, zorder=3)
            handles.append(Patch(color=color, label=f"{names.get(a, a)}: current price"))
        handles.append(Line2D([], [], color=c["text"], marker="_", linestyle="none", markersize=9,
                              markeredgewidth=1.4, label="lowest seen, if it was cheaper"))
        if len(current) <= 60:  # with more bars the label collides with its neighbours; the table has the minimum
            cheapest = current.loc[current["price"].idxmin()]
            label(ax, c, money(cheapest["price"]), mdates.date2num(cheapest["outbound_date"]), cheapest["price"])
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{MONTHS[mdates.num2date(v).month - 1]} "
                                                                f"{mdates.num2date(v):%y}"))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: money(v)))
        ax.set_xlabel("departure date", fontsize=9)
        legend(ax, c, handles)
    return f


def draw_lead_time(curve):
    def f(ax, c):
        ax.plot(curve.index, curve.values, color=c["series"][0], linewidth=2,
                solid_joinstyle="round", solid_capstyle="round")
        ax.set_xlim(curve.index.max(), 0)  # from most to least lead time, as time goes by
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: pct(v)))
        ax.set_xlabel("days left before departure", fontsize=9)
        ax.set_ylabel("above its minimum", fontsize=9)
        i = curve.idxmin()
        label(ax, c, f"{i} days", i, curve[i], below=True)
    return f


# --- Report ----------------------------------------------------------------------------------

def report(data, out):
    global SYMBOL
    cfg = settings.load()
    SYMBOL = SYMBOLS.get(cfg["currency"], cfg["currency"] + " ")
    airlines = cfg["airlines"]
    df = load(data)
    charts = out / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    stays = ", ".join(str(s) for s in cfg["stay_days"])
    adults = f"{cfg['adults']} adult" + ("s" if cfg["adults"] > 1 else "")
    # No route in the title on purpose: the report may live in a public repository.
    md = [f"# {cfg.get('title', 'Flight prices')}",
          f"Round trip, {stays}-day stay, {adults}, economy. Prices from Google Flights.", ""]
    if df is None:
        md.append("No data yet. The tracker stores its first check on its next run.")
        return "\n".join(md) + "\n"

    latest = df["checked"].max()
    today = latest.normalize()
    days_tracked = df["checked"].dt.date.nunique()
    changes = detect_changes(df)
    precise = changes[changes["precise"]]
    names = df.groupby("airline")["airline_name"].last().to_dict()
    md[2:2] = ["", f"_Updated {latest:%Y-%m-%d %H:%M} (local time) · {df['checked'].nunique()} checks "
               f"since {df['checked'].min():%Y-%m-%d} · {len(changes)} price changes detected._"]

    # Current price: whatever each airline's latest check returned.
    current = df[df["checked"] == df.groupby("airline")["checked"].transform("max")]
    current = current[current["outbound_date"] > today]
    history = df.groupby(KEY)["price"].agg(lowest="min", highest="max").reset_index()
    current = current.merge(history, on=KEY).sort_values("outbound_date")

    level, days_level = level_by_hour(df)
    rises_h = precise[precise["delta"] > 0].groupby(precise["checked"].dt.hour).size()
    drops_h = precise[precise["delta"] < 0].groupby(precise["checked"].dt.hour).size()
    rises_h, drops_h = (s.reindex(range(24), fill_value=0) for s in (rises_h, drops_h))
    rises_d = precise[precise["delta"] > 0].groupby(precise["checked"].dt.weekday).size()
    drops_d = precise[precise["delta"] < 0].groupby(precise["checked"].dt.weekday).size()
    rises_d, drops_d = (s.reindex(range(7), fill_value=0) for s in (rises_d, drops_d))

    md += ["## Summary", ""]
    if not current.empty:
        b = current.loc[current["price"].idxmin()]
        md.append(f"- **Cheapest right now: {money(b['price'])}** with {names.get(b['airline'], b['airline'])}, "
                  f"{fmt_date(b['outbound_date'])} → {fmt_date(b['return_date'])}.")
    if level is not None and days_level >= MIN_DAYS_BY_HOUR:
        h = int(level.idxmin())
        md.append(f"- **Cheapest hour to check:** {h:02}:00, on average {pct(level[h])} "
                  f"against that day's median price ({days_level} days of data).")
    if len(precise) >= MIN_CHANGES and drops_h.sum() > 0:
        line = (f"- **When prices change:** most drops are detected at {int(drops_h.idxmax()):02}:00 "
                f"and most rises at {int(rises_h.idxmax()):02}:00")
        if days_tracked >= MIN_DAYS_BY_WEEKDAY:
            line += (f"; by weekday, drops peak on {DAYS_LONG[int(drops_d.idxmax())]}s and rises on "
                     f"{DAYS_LONG[int(rises_d.idxmax())]}s")
        md.append(line + ".")
    md.append(f"- **Changes with a reliable time:** {int((precise['delta'] > 0).sum())} rises and "
              f"{int((precise['delta'] < 0).sum())} drops over {days_tracked} days of tracking.")
    if len(precise) < MIN_CHANGES:
        md.append(f"- Not enough changes yet to draw conclusions about hours and days (at least "
                  f"{MIN_CHANGES} needed). After 2-3 weeks of data this report becomes reliable.")
    elif days_tracked < MIN_DAYS_BY_WEEKDAY:
        md.append(f"- Only {days_tracked} days of tracking: treat these patterns as a first signal. "
                  f"Weekday comparisons start after {MIN_DAYS_BY_WEEKDAY} days.")
    md.append("")

    if not current.empty:
        md += ["## Cheapest dates right now", "",
               table(["Departure", "Return", "Airline", "Price", "Lowest seen", "Highest seen"],
                     [[fmt_date(r.outbound_date), fmt_date(r.return_date), names.get(r.airline, r.airline),
                       f"**{money(r.price)}**", money(r.lowest), money(r.highest)]
                      for r in current.nsmallest(10, "price").itertuples()]), "",
               "## Price by departure date", "",
               figure(charts, "calendar", "Current round-trip price by departure date",
                      draw_calendar(current, airlines, names)), ""]
        by_day = current.groupby([current["outbound_date"].dt.weekday, "airline"])["price"].agg(["mean", "min", "size"])
        md += ["### By day of the week you leave", "",
               table(["Departure day", "Airline", "Average", "Cheapest", "Departures"],
                     [[DAYS_LONG[d], names.get(a, a), money(r["mean"]), money(r["min"]), int(r["size"])]
                      for (d, a), r in by_day.iterrows()]), "",
               "_Averages mix seasons: a day that only has flights in low season looks cheaper on "
               "average even if it isn't within the same week._", ""]

    md += ["## What time is it cheapest?", "",
           "Each bar compares the price at that hour with the median of the same flight on the same day. "
           "Blue bars are hours when, on average, the flight is cheaper.", ""]
    if level is not None:
        md += [figure(charts, "level-by-hour", "Mean price deviation by hour of the check",
                      draw_level(level)), ""]
        if days_level < MIN_DAYS_BY_HOUR:
            md += [f"_Only {days_level} day(s) with enough checks so far; wait for at least "
                   f"{MIN_DAYS_BY_HOUR}._", ""]
    else:
        md += ["_No full day of hourly checks yet._", ""]

    md += ["## When do prices change?", "",
           "When each rise or drop was detected (local time). Only changes seen less than 3 hours "
           "after the previous check count.", ""]
    if len(precise):
        md += [figure(charts, "changes-by-hour", "Price rises and drops by hour of the day",
                      draw_changes(list(rises_h), list(drops_h), [f"{h:02}:00" for h in range(24)], step=2)), "",
               figure(charts, "changes-by-day", "Price rises and drops by day of the week",
                      draw_changes(list(rises_d), list(drops_d), DAYS)), ""]
        if days_tracked < MIN_DAYS_BY_WEEKDAY:
            md += [f"_Only {days_tracked} days tracked: each weekday has to show up at least twice "
                   f"({MIN_DAYS_BY_WEEKDAY} days) before they can be compared._", ""]
    else:
        md += ["_No price change detected yet._", ""]

    md += ["## How far ahead should you buy?", "",
           "Median of how far above its own minimum each flight is, by days left before departure. "
           "The lowest point of the curve is the lead time at which prices tend to bottom out.", ""]
    if days_tracked >= MIN_DAYS_LEAD_TIME:
        md += [figure(charts, "lead-time", "Price above its minimum by lead time",
                      draw_lead_time(lead_time_curve(df))), ""]
    else:
        md += [f"_At least {MIN_DAYS_LEAD_TIME} days of tracking needed (so far {days_tracked})._", ""]

    recent = changes[changes["checked"] >= latest - pd.Timedelta(days=7)]
    for title, rows in (("Biggest drops", recent[recent["delta"] < 0].nsmallest(10, "delta")),
                        ("Biggest rises", recent[recent["delta"] > 0].nlargest(10, "delta"))):
        if rows.empty:
            continue
        md += [f"## {title} in the last 7 days", "",
               table(["Detected", "Departure", "Return", "Airline", "Before", "Now", "Change"],
                     [[f"{r.checked:%Y-%m-%d %H:%M}", fmt_date(r.outbound_date), fmt_date(r.return_date),
                       names.get(r.airline, r.airline), money(r.previous), money(r.price),
                       f"{money(r.delta)} ({pct(r.pct)})"] for r in rows.itertuples()]), ""]

    md += ["## Tracking status", ""]
    runs_file = data / "runs.csv"
    if runs_file.exists():
        runs = pd.read_csv(runs_file)
        runs["checked"] = pd.to_datetime(runs["checked_local"])
        last_day = runs[runs["checked"] > runs["checked"].max() - pd.Timedelta(hours=24)]
        totals = last_day.groupby("airline")[["queries", "priced", "unpriced", "errors"]].sum()
        for a, r in totals.reindex([a for a in airlines if a in totals.index]).iterrows():
            md.append(f"- {names.get(a, a)}, last 24 h: {last_day['checked'][last_day['airline'] == a].nunique()} "
                      f"runs, {r['priced']} prices, {r['errors']} errors.")
            if r["priced"] == 0 and r["unpriced"] > 0:
                md.append("  Google Flights lists its flights but **without a price**, so it can't be tracked "
                          "here; if fares show up they will be picked up automatically.")
    return "\n".join(md) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=ROOT / "data")
    p.add_argument("--out", type=Path, default=ROOT / "report")
    args = p.parse_args()
    text = report(args.data, args.out)
    (args.out / "REPORT.md").write_text(text, encoding="utf-8")
    print(f"Report written to {args.out / 'REPORT.md'}")


if __name__ == "__main__":
    main()
