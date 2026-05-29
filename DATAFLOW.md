# RapidSift - Data Flow & Processing

## Architecture Overview

RapidSift is a stock signal monitoring dashboard that detects technical buy/sell signals across ~200 tickers, validates them with backtesting, and displays results on a static site hosted on Vercel. Data is refreshed via GitHub Actions on a schedule (or manually via the dashboard's Refresh button).

```
config.yaml (watchlist)
       |
       v
GitHub Actions (signal-alerts.yml)
  |-- scripts/notify_telegram.py    (signal detection + Telegram alerts)
  |-- scripts/generate_signals_json.py  (JSON for dashboard)
       |
       v
_site/data/signals.json  (committed to repo)
       |
       v
Vercel auto-deploys from main branch
       |
       v
Dashboard HTML pages fetch /api/signals or /data/signals.json
```

---

## 1. Data Sources

### Watchlist (`config.yaml`)
- ~200 tickers defined under `watchlist:` key
- Covers US equities across sectors: tech, financials, energy, biotech, etc.
- Used by all scripts to know which tickers to fetch and analyze

### Price Data (yfinance)
- Historical OHLCV data fetched via `yfinance` Python library
- `scripts/generate_signals_json.py` does batch downloads in groups of 50 tickers using `yf.download()` with `threads=True` for performance
- Typically fetches 6 months of daily bars per ticker
- SPY is fetched separately as the market benchmark

---

## 2. Signal Detection

### Where it runs
- **Live (GitHub Actions):** `scripts/generate_signals_json.py` runs end-to-end: fetch prices, detect signals, validate, build JSON
- **Backfill (local):** `scripts/full_backfill.py` replays historical data through the same detection logic, writing results to DuckDB
- **Dashboard engine (legacy):** `signals/engine.py` + `data/db.py` for the DuckDB-backed Streamlit dashboard

### Signal Types

All signal detectors live in `signals/`:

| Signal | File | Logic |
|--------|------|-------|
| **RSI Oversold** | `technical.py` | RSI(14) crosses below 30 (transition day only, not every day it stays below) |
| **MACD Crossover** | `technical.py` | MACD line crosses above the signal line |
| **MA Crossover** | `technical.py` | Short MA (e.g. 20-day) crosses above long MA (e.g. 50-day) |
| **Volume Spike** | `volume.py` | Volume exceeds 2x the 20-day average; direction set by price action that day |
| **Percent Change** | `price.py` | Volatility-normalized: daily move exceeds 2 sigma of trailing 20-day returns. Down = buy, Up = sell |
| **Adjusted Drop** | `adjusted.py` | Stock drops significantly more than SPY (market-adjusted); mean-reversion buy signal |
| **Adjusted Surge** | `adjusted.py` | Stock surges significantly more than SPY; potential sell signal |
| **Confluence** | Generated in `generate_signals_json.py` | 2+ buy signals fire on the same ticker on the same day |

### Key Design Decisions
- **Entry price:** Signals use next-day open as the entry price (not signal-day close) to avoid look-ahead bias
- **Daily returns:** Calculated close-to-close (not open-to-close) to capture overnight gaps
- **Signal cooldown:** Planned 5-day cooldown per signal type per ticker to prevent duplicate signals
- **Market benchmark:** SPY used as market return instead of equal-weighted watchlist average

---

## 3. Signal Validation (Backtesting)

### Where it runs
- `backtest/validator.py` (DuckDB version for local dashboard)
- Inline in `generate_signals_json.py` for JSON output

### How it works
1. For each historical signal, look forward N trading days (default: 14)
2. Entry price = next trading day's open after the signal fires
3. Check if the stock hit:
   - **+5% gain** within the lookahead window → win at 5% target
   - **+10% gain** within the lookahead window → win at 10% target
4. Track `days_to_5` and `days_to_10` (how many days it took to hit each target)
5. Compute win rate = (signals that hit target) / (total validated signals)

### Baseline Comparison
- A "random entry" baseline is computed: for every trading day (not just signal days), check if a random buy would have hit +5%/+10% in 14 days
- This provides context: a 60% signal win rate means nothing if the baseline is 55%

### Output: Reliability Data
Each strategy gets a reliability record:
```json
{
  "strategy": "rsi_oversold_buy",
  "total_signals": 142,
  "validated": 120,
  "win_5": 72,
  "win_10": 38,
  "win_rate_5": 60.0,
  "win_rate_10": 31.7,
  "avg_days_to_5": 4.2,
  "avg_days_to_10": 8.1
}
```

---

## 4. JSON Generation (`scripts/generate_signals_json.py`)

This is the main pipeline script that runs in GitHub Actions. It:

1. **Loads watchlist** from `config.yaml`
2. **Batch-fetches prices** via yfinance (groups of 50, with retry for failures)
3. **Fetches SPY** separately as market benchmark
4. **Runs signal detection** on each ticker's price history
5. **Validates signals** against forward-looking price data (backtesting)
6. **Computes reliability** stats per strategy
7. **Computes baseline** random-entry win rates
8. **Builds JSON** with structure:

```json
{
  "generated": "2026-05-29T13:30:00Z",
  "signals": [
    {
      "ticker": "NVDA",
      "strategy": "rsi_oversold_buy",
      "direction": "buy",
      "date": "2026-05-28",
      "time": "13:30",
      "price": 225.32,
      "pct_change": -4.42,
      "win_rate": 62.5,
      "details": "RSI: 28.4"
    }
  ],
  "reliability": [...],
  "baseline": {
    "win_rate_5": 48.2,
    "win_rate_10": 22.1
  }
}
```

9. **Writes** to `_site/data/signals.json`

---

## 5. GitHub Actions Workflow (`.github/workflows/signal-alerts.yml`)

### Schedule
- Runs on weekdays at 13:30, 15:30, 17:30 UTC (covers US market hours)
- Also triggered manually via `workflow_dispatch`

### Steps
1. Checkout repo
2. Install Python 3.12 + dependencies (yfinance, pandas, pyyaml, cairosvg, requests)
3. Run `scripts/notify_telegram.py` — detects signals and sends Telegram alerts
4. Run `scripts/generate_signals_json.py` — generates dashboard JSON
5. Commit `_site/data/signals.json` and `.signal_state.json` to repo
6. Push to main → triggers Vercel auto-deploy

### Manual Trigger
- The dashboard's Refresh button calls `POST /api/refresh`
- `api/refresh.py` (Vercel serverless function) dispatches the GitHub Actions workflow via GitHub API
- Requires `GITHUB_PAT` environment variable on Vercel (repo + workflow scope)
- Full cycle takes ~2 minutes: GH Actions runs → commits JSON → Vercel redeploys

---

## 6. Vercel Deployment

### Configuration
- Static site served from `_site/` directory
- Serverless Python functions in `api/` directory
- Auto-deploys on every push to `main`

### API Endpoints (Vercel Serverless)

| Endpoint | File | Purpose |
|----------|------|---------|
| `GET/POST /api/signals` | `api/signals.py` | Serves `_site/data/signals.json` with ETag caching and CORS headers |
| `POST /api/refresh` | `api/refresh.py` | Triggers GitHub Actions workflow dispatch |
| `GET /api/market-status` | `api/market-status.py` | Returns NYSE/NASDAQ open/closed status |
| `GET /api/chart` | `api/chart.py` | Generates SVG charts for individual tickers |

### Static Files
- `_site/data/signals.json` — live signal data (committed by GH Actions)
- `_site/charts/` — pre-rendered chart HTML files (ticker_type_days.html)

---

## 7. Frontend Dashboard

### Pages

| Page | File | Description |
|------|------|-------------|
| **Charts** | `index.html` | Default tab. Interactive chart viewer with ticker/type/period selectors |
| **Signals** | `buy-signals.html` | Live buy signals table with filtering by strategy, ticker, result. Mobile: card-based layout |
| **Reliability** | `reliability.html` | Strategy win rates table with baseline comparison. Loaded live from signals JSON |
| **Strategies** | `strategies.html` | Strategy descriptions and methodology |

### Data Loading (Client-side)
1. Page loads with static/placeholder content
2. JavaScript fetches `/api/signals` (with fallback to `/data/signals.json` for local dev)
3. Parses JSON and populates:
   - KPI strip (signal count, win rates, etc.)
   - Signal table (desktop) / signal cards (mobile)
   - Reliability table (on reliability tab)
4. Filters are applied client-side (no server round-trip)

### Clocks
- Dual timezone display: ET (New York) and CET (Prague)
- Updated every second via `setInterval`
- Market status (NYSE/NASDAQ open/closed) polled every 30 seconds

### Refresh Flow
1. User clicks Refresh button
2. Button shows "Triggering..." state
3. `POST /api/refresh` dispatches GitHub Actions workflow
4. Button shows "Triggered!" for 3 seconds
5. After ~2 minutes, GH Actions completes, commits new JSON, Vercel redeploys
6. On the signals tab, `loadLiveSignals()` is called after 2 minutes to pick up new data

---

## 8. Telegram Notifications (`scripts/notify_telegram.py`)

- Runs as part of the GitHub Actions workflow
- Detects new signals (not previously seen)
- Sends formatted messages to a Telegram channel
- Uses `.signal_state.json` to track which signals have already been notified
- Requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets in GitHub

---

## 9. Local Development

### Running the dashboard locally
```bash
cd _site
python3 -m http.server 8052
# Open http://localhost:8052
```

### Regenerating signals JSON locally
```bash
python3 scripts/generate_signals_json.py
# Output: _site/data/signals.json
```

### Running the legacy Streamlit dashboard
```bash
python3 main.py
# Requires DuckDB database with backfilled data
```

### Full backfill (populate DuckDB with historical signals)
```bash
python3 scripts/full_backfill.py
```

---

## 10. Environment Variables

| Variable | Where | Purpose |
|----------|-------|---------|
| `GITHUB_PAT` | Vercel env vars | GitHub Personal Access Token (repo + workflow scope) for triggering Actions |
| `TELEGRAM_BOT_TOKEN` | GitHub Secrets | Telegram bot token for sending alerts |
| `TELEGRAM_CHAT_ID` | GitHub Secrets | Telegram chat/channel ID for alerts |

---

## 11. File Tree (key files)

```
market-monitor/
  config.yaml                          # Watchlist (200 tickers)
  main.py                              # Legacy Streamlit dashboard entry
  requirements.txt                     # Python dependencies

  .github/workflows/
    signal-alerts.yml                  # Scheduled + manual signal detection

  signals/
    engine.py                          # Signal orchestrator (DuckDB version)
    technical.py                       # RSI, MACD, MA crossover
    price.py                           # Percent change (volatility-normalized)
    volume.py                          # Volume spike
    adjusted.py                        # Market-adjusted drop/surge (SPY benchmark)

  scripts/
    generate_signals_json.py           # Main pipeline: fetch → detect → validate → JSON
    full_backfill.py                   # Historical backfill into DuckDB
    notify_telegram.py                 # Telegram alert sender
    export_static.py                   # Static chart HTML generator

  backtest/
    validator.py                       # Signal validation / backtesting

  data/
    db.py                              # DuckDB database operations
    fetcher.py                         # yfinance data fetcher

  api/
    signals.py                         # Serve signals JSON (ETag + CORS)
    refresh.py                         # Trigger GitHub Actions workflow
    market-status.py                   # NYSE/NASDAQ market hours check
    chart.py                           # On-demand SVG chart generation

  _site/
    index.html                         # Charts tab (default)
    buy-signals.html                   # Signals tab
    reliability.html                   # Strategy reliability tab
    strategies.html                    # Strategy descriptions tab
    mobile.css                         # Mobile responsive overrides
    data/signals.json                  # Live signal data (auto-committed by GH Actions)
    charts/                            # Pre-rendered chart HTML files
```
