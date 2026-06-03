# Weekly Signal Performance Report

Run a concise weekly analysis and send results to Telegram.

## Steps

1. Read `data/signal_history.csv` and compute:
   - Signals fired this week vs last week
   - Win rate by signal type (last 30 days vs all-time) — flag any degradation >5pp
   - Top 5 winning signals this week (ticker, type, gain)
   - Top 5 losing signals this week (ticker, type, loss)
   - Confidence model accuracy: for signals scored >65, was actual WR higher than those <50?
   - Any signal types with <50% WR over last 30 days (candidates for review)

2. Format as a compact Telegram message (HTML parse mode, max ~4000 chars).

3. Send via the Telegram bot:
   ```python
   import requests, os
   BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
   CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
   requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
       json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})
   ```

   If env vars aren't set, just print the report to stdout instead.

## Output Format

```
📈 RAPIDSIFT WEEKLY REPORT — {date range}

This Week: {N} signals fired, {WR}% win rate (+5%)
vs Last Week: {N} signals, {WR}%

BY TYPE (last 30d):
  Sector Drop    78% (n=45) ↑
  Adj Drop       74% (n=52) →
  Confluence     71% (n=38) ↓ was 76%
  ...

TOP WINS: NVDA +12.3%, AAPL +8.1%, ...
TOP LOSSES: MARA -11.2%, PLUG -9.8%, ...

CONFIDENCE MODEL:
  High (>65): {WR}% actual (n={N})
  Low (<50):  {WR}% actual (n={N})

⚠️ WATCHLIST: {types with declining WR}
```

Keep it actionable. Don't include signal types with <10 signals in the period.
