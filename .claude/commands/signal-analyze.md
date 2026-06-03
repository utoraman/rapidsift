# Signal Performance Analysis

You are a quantitative analyst with the signal history data from RapidSift. Your job is to find which signals actually work, which don't, and why.

## Data

Read **`data/signal_history.csv`**. Columns:

| Column | Description |
|--------|-------------|
| `date` | Signal fire date (YYYY-MM-DD) |
| `ticker` | Stock ticker |
| `type` | Signal type: macd, volume_spike, gap_and_go, rel_strength, earnings_drift, adjusted_drop, sector_drop, confluence |
| `direction` | Always "buy" (only buy signals are tracked) |
| `detail` | Human-readable signal description |
| `price` | Close price on signal day |
| `entry_price` | Next day's open (actual entry) |
| `result_5` | "win" if hit +5% in 14 trading days, else "loss" |
| `result_10` | "win" if hit +10% in 14 trading days, else "loss" |
| `current_pct` | Return at end of 14-day window |
| `max_drawdown_pct` | Worst drawdown from entry during the 14-day window (negative number) |
| `days_to_5` | Trading days to hit +5% (null if didn't hit) |
| `days_to_10` | Trading days to hit +10% (null if didn't hit) |

Use Python (via Bash tool) with pandas to do the analysis. Write inline scripts — don't create files unless the user asks.

## Analysis to Run

Run ALL of the following. Use `python3 -c "..."` or write a temp script for longer analyses.

### 1. Overview
- Total rows, date range, signals per type
- Overall win rate for +5% and +10%

### 2. Win Rate by Signal Type
For each signal type, compute:
- Count, win rate +5%, win rate +10%
- Average days to +5% (for winners)
- Average max drawdown
- Sort by win rate +5% descending

Flag types with fewer than 30 signals as "low sample size".

### 3. Edge Over Random
If a baseline exists in `_site/data/signals.json` (check the `baseline` key), compare each signal type's win rate to the baseline. Compute edge = signal_wr - baseline_wr. Negative edge = signal is worse than random.

### 4. Risk-Adjusted Performance
For each signal type, compute:
- Profit factor: avg gain of winners / avg loss of losers (using current_pct)
- Sharpe-like ratio: mean(current_pct) / std(current_pct)
- Win rate × avg win - loss rate × avg loss = expected value per trade

### 5. Temporal Analysis
- Win rate by month — is performance consistent or was it a one-month fluke?
- Win rate by day of week — any day-of-week effect?

### 6. Ticker Analysis
- Top 10 tickers by win rate (min 5 signals)
- Bottom 10 tickers by win rate (min 5 signals)
- Are certain tickers consistently good/bad across signal types?

### 7. Confluence Analysis
- Does confluence (2+ signals on same day) actually outperform individual signals?
- Which signal combinations appear most often in confluence?

### 8. Drawdown Analysis
- Signals that "won" but had >15% drawdown first — would you actually hold through that?
- Average drawdown for winners vs losers
- Is there a drawdown threshold that predicts losses?

### 9. Actionable Recommendations
Based on ALL the above, give specific, data-backed recommendations:
- Which signal types to keep, tighten, or disable
- Which tickers to exclude from certain signals
- Suggested threshold changes with supporting data
- Expected improvement if recommendations are implemented

## Output Format

Use tables (aligned text) for all breakdowns. Include the actual numbers — don't just say "good" or "bad". End with a clear ranked list of signal types from best to worst with the key metric for each.

If the history CSV doesn't exist or has too few rows (<50), say so and suggest running: `python3 scripts/backfill_history.py --months 6 --clear`
