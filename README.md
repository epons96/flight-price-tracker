# Flight price tracker

Checks Google Flights every hour for the round-trip price of a route on one or more airlines,
for every departure date over the coming months, stores each price and builds a report that
answers:

- **At what time of day and on which weekday do prices drop or rise?**
- **Which travel dates are cheapest?**
- **How far ahead should you buy?**

👉 **The report lives in [report/REPORT.md](report/REPORT.md)** and is refreshed daily.

## How it works

1. GitHub Actions runs [track.py](track.py) every hour (at minute 17). For each airline and
   departure date it looks up the round trip and stores the cheapest fare in
   `data/prices-YYYY-MM.csv`.
2. Once a day [analyze.py](analyze.py) reads the whole history and rebuilds `report/`, and
   [notify.py](notify.py) sends a digest to Telegram if it is set up.
3. Every run is logged in `data/runs.csv` (prices found, errors…). If a run gets no price at all
   it fails and GitHub sends an email.

To trigger a run by hand: **Actions → Track prices → Run workflow**.

## Configuration

The settings are a JSON file like [config.example.json](config.example.json). To keep the route
out of the public repository, on GitHub it is stored as a repository secret named `TRACKER_CONFIG`:

```sh
gh secret set TRACKER_CONFIG < config.json
```

Locally, copy `config.example.json` to `config.json` (it is git-ignored).

| Field | Meaning |
|---|---|
| `title` | Report title (optional) |
| `origin`, `destination` | Airports (IATA codes) |
| `airlines` | Airline IATA codes. Each one is searched separately |
| `stay_days` | Days between outbound and return. Several are allowed, e.g. `[7, 14, 21]` (multiplies the queries) |
| `window_days` | How many days ahead to look for departures. Dates the airline doesn't sell yet show up as "no flight" and are picked up once sales open |
| `adults`, `currency`, `country` | Passengers, currency and the country the search is made from |
| `timezone` | Timezone used to analyze the hours (e.g. `Europe/London`) |

## Telegram digest (optional)

Every morning, after the report, a Telegram message with the cheapest departure dates, what
changed since the day before and when prices move, plus the price-by-date chart.

1. In Telegram, open **@BotFather**, send `/newbot` and follow the steps. It gives you a token.
2. Send your new bot any message (bots can't start a chat).
3. With [gh](https://cli.github.com) logged in, run `python setup_telegram.py` and paste the
   token. It finds your chat, stores `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` as repository
   secrets and sends a test message.

The message names the route, so it is never printed in the Actions logs. To preview it locally:
`python notify.py --dry-run`.

## Running locally

```sh
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt -r requirements-report.txt
python track.py                   # one check
python analyze.py                 # rebuilds report/
```

## Things to know

- **Source:** Google Flights, because airline websites usually block automated access. It is
  the cheapest fare Google shows for that airline, which normally matches the airline's own site,
  although it may be a fare without checked baggage.
- **Airlines without a price:** some show up on Google Flights with their flights but no fare.
  The report flags them under "Tracking status"; those cannot be tracked with this tool.
- **Round trip on a single ticket:** usually far cheaper than two one-way tickets, and only
  possible with the same airline or partner airlines.
- **Conclusions take time:** prices don't change every hour. After 2-3 weeks of data the
  hour and weekday patterns start to be reliable.
- **GitHub Actions:** scheduled runs can start a few minutes late (or be skipped at busy times).
  Each price stores the actual time of the check. In a public repository it uses no minutes; in a
  private one it takes 750-1,500 of the 2,000 free minutes a month (each run takes about a minute,
  and GitHub rounds every job up to the minute).
- **Google throttling:** from GitHub's servers Google randomly returns empty results for some
  dates, so `track.py` retries empty dates in up to three passes, each slower than the last.
- If Google changes its page format, `track.py` stops getting prices and the workflow fails;
  `fast-flights` or the parsing will need updating.
