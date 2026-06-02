# Signal Health & Reliability Audit

You are a quantitative analyst auditing RapidSift, a personal stock market signal monitoring system. Run a comprehensive health check and produce an actionable report. Be blunt about what's working and what isn't.

## Data Sources

Read and analyze ALL of these:

1. **`_site/data/signals.json`** — the live dashboard data. Structure:
   - `generated_at`: when the data was last built
   - `kpi`: `{buy_signals, sell_signals, unique_tickers, avg_win_rate_5}`
   - `signals[]`: each has `{ticker, date, time, type, detail, price, latest_price, current_pct, result_5, result_10, max_drawdown_pct, wr5, wr10, fired, entry_price?, days_to_5?, days_to_10?}`
   - `reliability[]`: per (ticker, type) win rates `{ticker, type, fired, wr5, wr10}`
   - `baseline`: `{total, wr5, wr10}` — random-entry win rates for context

2. **`.signal_state.json`** — Telegram notification state. Has `last_run` timestamp and `last_signals` array of `ticker_type_date` keys.

3. **GitHub Actions history** — run `gh api repos/utoraman/rapidsift/actions/runs --jq '.workflow_runs[:20] | .[] | [.id, .created_at, .conclusion, .name] | @tsv'` to get recent workflow runs. Check for failures, gaps, reliability.

4. **Git log** — run `git log --oneline -20` to see recent deployment activity.

## Audit Sections

### 1. Pipeline Health
- When was data last generated? Is it stale (>24h on a weekday)?
- How many of the last 20 workflow runs succeeded vs failed? What's the failure rate?
- Are there gaps in the schedule? (Expected: 5 runs per trading day at ~14:30, 16:00, 17:30, 19:00, 20:30 UTC, Mon-Fri)
- Is the Telegram state file in sync (last_run within a few hours of signals.json generation)?

### 2. Signal Volume & Distribution
- Total buy signals in the current window. Is this reasonable for ~200 tickers over 14 days?
- Signals per day — any days with 0 signals (missed runs)? Any days with abnormally high counts?
- Distribution by signal type. Flag any type with 0 signals (broken detector?) or a type that dominates >40% of total (noisy detector?).
- Distribution by ticker. Flag any ticker appearing >5 times (over-signaling) or tickers that never appear.

### 3. Win Rate Analysis
- Overall win rate for +5% and +10% targets.
- Win rate **per signal type** — only count completed signals (result != "pending"). Flag types with <10 completed signals as "insufficient data".
- **Edge over baseline**: For each signal type, compute `signal_wr - baseline_wr`. A negative edge means the signal is WORSE than random. Flag these prominently.
- Flag any 100% win rates — likely an artifact of insufficient completed signals or look-ahead window not expired yet.
- Identify the top 3 and bottom 3 performing signal types.

### 4. Drawdown Risk
- Average and worst-case `max_drawdown_pct` across all signals.
- Drawdown by signal type — which types expose you to the deepest drawdowns?
- Flag any signals where drawdown exceeded -15% before hitting the gain target.
- Calculate a risk-adjusted score: `edge / avg_drawdown` per signal type.

### 5. Confluence Quality
- How many confluence signals fired? What types most commonly combine?
- Is confluence outperforming individual signals? (It should if the strategy is working.)

### 6. Freshness & Timing
- Check the `time` field on recent signals. Are they firing during market hours (13:30-20:00 UTC) or only after close?
- Check if intraday overlay is working by looking for signals with times during market hours.

### 7. Actionable Recommendations
Based on ALL the above, produce a prioritized list of specific actions. Examples:
- "Disable X signal type — negative edge, adds noise"
- "Investigate Y — 0 signals in 14 days, detector may be broken"
- "Tighten Z threshold — fires too often, 45% of all signals"
- "Pipeline has 30% failure rate — fix workflow before next trading day"

## Output Format

Structure your report with clear headers, tables where possible, and a traffic-light summary at the top:

```
SIGNAL HEALTH REPORT — {date}
================================
Pipeline:    {GREEN/YELLOW/RED}  {one-line summary}
Signal Edge: {GREEN/YELLOW/RED}  {one-line summary}  
Risk:        {GREEN/YELLOW/RED}  {one-line summary}
Data:        {GREEN/YELLOW/RED}  {one-line summary}
```

Then the detailed sections. Use tables for per-type breakdowns. End with the prioritized action list.

Keep the report concise but complete. No fluff — this is for a trader who needs to know what's working and what to fix.
