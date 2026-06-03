# RapidSift — Stock Market Signal Monitor

Personal signal monitoring dashboard for US equities. Detects buy/sell signals across ~200 tickers using technical and statistical strategies, scores them with a confidence model, and delivers top alerts via Telegram.

**Live site:** https://rapidsift.vercel.app
**Repo:** https://github.com/utoraman/rapidsift

## Owner Preferences

- Ufuk is in **CET timezone** (Europe/Prague). US market hours are 15:30–22:00 CET.
- **Never asks Ufuk to write Python.** All analysis, scripting, and data work is done by Claude inline.
- Checks the dashboard primarily on **mobile** — always verify mobile layout.
- Prefers concise, data-backed explanations. Show the numbers, not vague assessments.
- Iterates fast: build → show draft → publish. Don't over-plan.

## Architecture

```
scripts/
  signals.py              # Shared signal detection (single source of truth)
  signal_scorer.py        # Confidence scoring (logistic + Bayesian + survival)
  generate_signals_json.py  # Fetches prices, detects signals, scores, builds JSON
  notify_telegram.py      # Telegram bot — sends top 5 signals by confidence
  backfill_history.py     # Historical backfill for signal_history.csv
_site/
  index.html              # Dashboard (desktop + mobile, single file)
  mobile.css              # Mobile-specific styles
  strategies.html         # Strategy documentation page
  data/signals.json       # Generated signal data (committed by GH Actions)
data/
  signal_history.csv      # 2000+ historical signals for model training
  sectors.yaml            # Ticker → sector ETF mapping (200 tickers, 12 sectors)
.github/workflows/
  signal-alerts.yml       # Cron: 6x/day during market hours + manual trigger
  backfill.yml            # Manual: re-backfill history with --months/--clear
```

## Signal Types (active)

| Type | Direction | Description |
|------|-----------|-------------|
| adjusted_drop | buy | Stock drops 5%+ more than SPY (market-adjusted) |
| sector_drop | buy | Stock drops 5%+ more than its sector ETF |
| gap_and_go | buy | Gaps up >3%, holds above open on volume |
| rel_strength | buy | Outperforms SPY by 8%+ over 20 days on rising volume |
| earnings_drift | buy | Post-catalyst drift (gap + sustained momentum) |
| macd | buy | MACD bullish crossover |
| volume_spike | alert | 3x average volume spike |
| confluence | buy | 2+ strong signals on same ticker same day |

Disabled: RSI (no edge), MA crossover (below random baseline).

## Confidence Scoring

Three models blended (40% / 30% / 30%):
- **Logistic regression** — trained on signal features (type, sector, momentum, volume, volatility)
- **Bayesian Beta-Binomial** — prior from signal type WR, updated with ticker track record
- **Survival analysis** — Kaplan-Meier P(hit +5% by day 14)

Trained on `data/signal_history.csv`. Scores 0–100.

## Key Conventions

- **Real repo path:** `/Users/ufuk/market-monitor` (may also be accessed from a worktree)
- Signal detection is in `signals.py` — both `generate_signals_json.py` and `backfill_history.py` import from it. Keep them in sync.
- `generate_signals_json.py` also includes intraday overlay during market hours (fetches 5min bars)
- Dashboard is a single HTML file with inline JS. Desktop table + mobile card renderer are separate code blocks.
- Sector labels map: `data/sectors.yaml` → 12 ETFs (XLK, XLC, XLY, XLF, XLE, XLV, XLI, XLB, XLP, XLU, XLRE, BITO) + SPY fallback.
- 5-day cooldown per (ticker, type) to avoid duplicate signals.
- Random baseline: ~62.5% WR for +5% in 14 days — signals must beat this to have edge.

## Deploy Flow

1. Edit files
2. `git add` + `git commit` (from `/Users/ufuk/market-monitor`)
3. `git pull --rebase origin main` (GH Actions bot pushes data commits)
4. `git push origin main`
5. `gh workflow run "Signal Alerts"` to regenerate data with new code
6. Vercel auto-deploys from push

Use `/deploy` command to do steps 2–6 in one shot.

## Cron Schedule (signal-alerts.yml)

| UTC | ET | CET | Note |
|-----|------|------|------|
| 13:45 | 9:45 AM | 15:45 | 15 min after open |
| 14:30 | 10:30 AM | 16:30 | Mid-morning |
| 16:00 | 12:00 PM | 18:00 | Midday |
| 17:30 | 1:30 PM | 19:30 | Afternoon |
| 19:00 | 3:00 PM | 21:00 | Late afternoon |
| 20:30 | 4:30 PM | 22:30 | After close (final EOD) |

## Common Issues

- **Git push rejected:** GH Actions bot pushes `[skip ci]` commits. Always `git pull --rebase` before push.
- **No signals for today:** If workflow ran before market open, there's no intraday data. Wait for scheduled run or trigger manually during market hours.
- **Preview tool unreliable:** Claude Preview doesn't work well for this project. Use `open http://localhost:8051` with a local `python3 -m http.server 8051 --directory _site` instead, or deploy and check live.
