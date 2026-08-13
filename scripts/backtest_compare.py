#!/usr/bin/env python3
"""
Backtest comparison: Old (random pick) vs New (suppression + confidence scoring).

Compares two signal-selection strategies over the last 4 weeks of
signal_history.csv and outputs results to data/backtest_results.json,
then embeds them into _site/backtest.html.
"""

import csv
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from signals import should_suppress, load_sector_map
from signal_scorer import SignalScorer


# ── Helpers ────────────────────────────────────────────────────────────────

def load_history(path):
    """Load signal_history.csv, return list of dicts with typed fields."""
    rows = []
    with open(path, "r") as f:
        for row in csv.DictReader(f):
            # Only buy signals
            if row["direction"] != "buy":
                continue
            # Must have entry_price
            ep = row.get("entry_price", "").strip()
            if not ep:
                continue
            try:
                row["entry_price"] = float(ep)
            except ValueError:
                continue
            # Parse numeric fields
            for fld in ("price", "current_pct", "max_drawdown_pct"):
                try:
                    row[fld] = float(row.get(fld, 0) or 0)
                except (ValueError, TypeError):
                    row[fld] = 0.0
            for fld in ("days_to_5", "days_to_10"):
                val = row.get(fld, "").strip()
                row[fld] = int(float(val)) if val else None
            rows.append(row)
    return rows


def split_train_test(rows, test_dates):
    """Split rows into training (before test window) and test sets."""
    test_set = set(test_dates)
    train = [r for r in rows if r["date"] not in test_set]
    test = [r for r in rows if r["date"] in test_set]
    return train, test


def compute_return(signal):
    """Compute dollar return on a $100 investment using current_pct."""
    pct = signal["current_pct"]
    return 100.0 * (pct / 100.0)


def sharpe_like(daily_returns):
    """Avg daily return / std of daily returns (annualized-ish)."""
    if len(daily_returns) < 2:
        return 0.0
    avg = sum(daily_returns) / len(daily_returns)
    var = sum((r - avg) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std = math.sqrt(var) if var > 0 else 0.001
    return avg / std


def run_strategy(name, daily_signals, pick_fn, max_per_day=5):
    """
    Run a strategy over daily signals.

    Args:
        name: strategy name
        daily_signals: dict date -> list of signal dicts
        pick_fn: function(signals, date) -> list of selected signals
        max_per_day: max signals per day

    Returns:
        dict with strategy results
    """
    trades = []
    daily_pnl = []
    total_invested = 0.0
    total_value = 0.0

    for date in sorted(daily_signals.keys()):
        signals = daily_signals[date]
        if not signals:
            daily_pnl.append({"date": date, "pnl": 0.0, "n_trades": 0})
            continue

        selected = pick_fn(signals, date)[:max_per_day]

        day_invested = 0.0
        day_pnl = 0.0

        for sig in selected:
            ret_dollar = compute_return(sig)
            invested = 100.0
            final_val = invested + ret_dollar

            day_invested += invested
            day_pnl += ret_dollar

            trades.append({
                "date": sig["date"],
                "ticker": sig["ticker"],
                "type": sig["type"],
                "detail": sig.get("detail", ""),
                "entry_price": sig["entry_price"],
                "current_pct": sig["current_pct"],
                "result_5": sig["result_5"],
                "max_drawdown_pct": sig["max_drawdown_pct"],
                "days_to_5": sig["days_to_5"],
                "confidence": sig.get("_confidence", None),
                "invested": invested,
                "return_dollar": round(ret_dollar, 2),
                "final_value": round(final_val, 2),
            })

        total_invested += day_invested
        total_value += day_invested + day_pnl
        daily_pnl.append({
            "date": date,
            "pnl": round(day_pnl, 2),
            "n_trades": len(selected),
        })

    # Compute metrics
    wins = sum(1 for t in trades if t["result_5"] == "win")
    losses = sum(1 for t in trades if t["result_5"] == "loss")
    win_rate = (wins / len(trades) * 100) if trades else 0.0

    returns = [t["current_pct"] for t in trades]
    avg_return = sum(returns) / len(returns) if returns else 0.0
    max_return = max(returns) if returns else 0.0
    min_return = min(returns) if returns else 0.0

    daily_ret_pcts = []
    for d in daily_pnl:
        if d["n_trades"] > 0:
            # Return as % of that day's investment
            day_inv = d["n_trades"] * 100.0
            daily_ret_pcts.append(d["pnl"] / day_inv * 100.0)

    sharpe = sharpe_like(daily_ret_pcts)

    # Cumulative returns
    cum = 0.0
    cumulative = []
    for d in daily_pnl:
        cum += d["pnl"]
        cumulative.append({"date": d["date"], "cumulative": round(cum, 2)})

    pnl_total = total_value - total_invested

    # Best and worst trades
    sorted_trades = sorted(trades, key=lambda t: t["current_pct"])
    worst_3 = sorted_trades[:3] if len(sorted_trades) >= 3 else sorted_trades
    best_3 = sorted_trades[-3:][::-1] if len(sorted_trades) >= 3 else sorted_trades[::-1]

    return {
        "name": name,
        "total_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "total_invested": round(total_invested, 2),
        "total_value": round(total_value, 2),
        "pnl": round(pnl_total, 2),
        "pnl_pct": round(pnl_total / total_invested * 100, 2) if total_invested else 0.0,
        "avg_return": round(avg_return, 2),
        "max_return": round(max_return, 2),
        "min_return": round(min_return, 2),
        "sharpe": round(sharpe, 3),
        "daily_pnl": daily_pnl,
        "cumulative": cumulative,
        "trades": trades,
        "best_trades": best_3,
        "worst_trades": worst_3,
    }


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    history_path = ROOT / "data" / "signal_history.csv"
    output_json = ROOT / "data" / "backtest_results.json"
    output_html = ROOT / "_site" / "backtest.html"

    print("Loading signal history...")
    all_rows = load_history(history_path)
    print(f"  {len(all_rows)} buy signals loaded")

    # Determine test window: last 20 trading days (~4 weeks)
    all_dates = sorted(set(r["date"] for r in all_rows))
    test_dates = all_dates[-20:]
    train_dates = all_dates[:-20]

    print(f"  Test window: {test_dates[0]} to {test_dates[-1]} ({len(test_dates)} days)")
    print(f"  Training data: {train_dates[0]} to {train_dates[-1]} ({len(train_dates)} days)")

    train_rows, test_rows = split_train_test(all_rows, test_dates)
    print(f"  Training signals: {len(train_rows)}")
    print(f"  Test signals: {len(test_rows)}")

    # Group test signals by date
    daily_signals = defaultdict(list)
    for r in test_rows:
        daily_signals[r["date"]].append(r)

    # ── Train scorer on training data only (no lookahead) ──
    print("\nTraining confidence scorer (no lookahead)...")
    # Write a temporary training CSV
    import tempfile
    tmp_train = Path(tempfile.mktemp(suffix=".csv"))
    with open(history_path) as f_in:
        reader = csv.DictReader(f_in)
        fieldnames = reader.fieldnames
    with open(tmp_train, "w", newline="") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()
        test_date_set = set(test_dates)
        with open(history_path) as f_in:
            for row in csv.DictReader(f_in):
                if row["date"] not in test_date_set:
                    writer.writerow(row)

    scorer = SignalScorer()
    scorer.train(str(tmp_train))
    tmp_train.unlink()

    # Load sector map for suppression
    sector_map = load_sector_map()

    # ── Score all test signals ──
    print("\nScoring test signals...")
    for sig in test_rows:
        sector = sector_map.get(sig["ticker"], "SPY")
        scores = scorer.score(
            signal_type=sig["type"],
            ticker=sig["ticker"],
            sector=sector,
            stock_momentum=sig["current_pct"],
            market_regime=1,  # approximate
        )
        sig["_confidence"] = scores["confidence"]
        sig["_suppress"] = should_suppress(sig["ticker"], sig["type"], sector_map)

    # ── Strategy A: Old (random, no filtering) ──
    print("\nRunning Strategy A (Old - random pick, no filtering)...")
    rng = random.Random(42)

    def pick_old(signals, date):
        pool = list(signals)
        rng.shuffle(pool)
        return pool[:5]

    result_old = run_strategy("Old (Random)", daily_signals, pick_old)

    # ── Strategy B: New (suppression + confidence ranking) ──
    print("Running Strategy B (New - suppression + confidence ranking)...")

    def pick_new(signals, date):
        # Filter: remove suppressed
        pool = [s for s in signals if not s.get("_suppress")]
        # Sort by confidence descending
        pool.sort(key=lambda s: s.get("_confidence", 0), reverse=True)
        return pool[:5]

    result_new = run_strategy("New (Filtered + Ranked)", daily_signals, pick_new)

    # ── Build output ──
    results = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "test_window": {
            "start": test_dates[0],
            "end": test_dates[-1],
            "trading_days": len(test_dates),
        },
        "training_window": {
            "start": train_dates[0],
            "end": train_dates[-1],
            "trading_days": len(train_dates),
        },
        "strategies": {
            "old": result_old,
            "new": result_new,
        },
        "improvement": {
            "win_rate_delta": round(result_new["win_rate"] - result_old["win_rate"], 1),
            "pnl_delta": round(result_new["pnl"] - result_old["pnl"], 2),
            "avg_return_delta": round(result_new["avg_return"] - result_old["avg_return"], 2),
            "sharpe_delta": round(result_new["sharpe"] - result_old["sharpe"], 3),
        },
    }

    # Save JSON
    with open(output_json, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_json}")

    # ── Print summary ──
    print("\n" + "=" * 70)
    print("BACKTEST RESULTS SUMMARY")
    print("=" * 70)
    print(f"Test window: {test_dates[0]} to {test_dates[-1]} ({len(test_dates)} trading days)")
    print()

    for label, strat in [("OLD (Random)", result_old), ("NEW (Filtered+Ranked)", result_new)]:
        print(f"  {label}:")
        print(f"    Trades:     {strat['total_trades']}")
        print(f"    Win Rate:   {strat['win_rate']}%")
        print(f"    Invested:   ${strat['total_invested']:,.0f}")
        print(f"    Final:      ${strat['total_value']:,.0f}")
        print(f"    P&L:        ${strat['pnl']:+,.2f} ({strat['pnl_pct']:+.2f}%)")
        print(f"    Avg Return: {strat['avg_return']:+.2f}%")
        print(f"    Sharpe:     {strat['sharpe']:.3f}")
        print(f"    Best Trade: {strat['best_trades'][0]['ticker']} {strat['best_trades'][0]['current_pct']:+.2f}%")
        print(f"    Worst Trade:{strat['worst_trades'][0]['ticker']} {strat['worst_trades'][0]['current_pct']:+.2f}%")
        print()

    imp = results["improvement"]
    print(f"  IMPROVEMENT (New vs Old):")
    print(f"    Win Rate:   {imp['win_rate_delta']:+.1f}pp")
    print(f"    P&L:        ${imp['pnl_delta']:+,.2f}")
    print(f"    Avg Return: {imp['avg_return_delta']:+.2f}pp")
    print(f"    Sharpe:     {imp['sharpe_delta']:+.3f}")
    print("=" * 70)

    # ── Generate HTML dashboard ──
    print("\nGenerating HTML dashboard...")
    generate_html(results, output_html)
    print(f"Dashboard saved to {output_html}")


def generate_html(results, output_path):
    """Generate the backtest comparison HTML dashboard."""
    json_data = json.dumps(results)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RapidSift — Backtest Comparison</title>
<style>
:root {{
    --bg: #0a0a0f;
    --bg-card: #12121a;
    --bg-card-hover: #1a1a25;
    --border: #1e1e2e;
    --border-accent: #2a2a3a;
    --text: #e0e0e8;
    --text-dim: #8888a0;
    --text-mute: #555570;
    --green: #00ff88;
    --green-dim: #00cc6a;
    --green-bg: rgba(0, 255, 136, 0.08);
    --red: #ff4444;
    --red-dim: #cc3333;
    --red-bg: rgba(255, 68, 68, 0.08);
    --gray: #666680;
    --gray-line: #444460;
    --blue: #4488ff;
    --yellow: #ffaa00;
    --mono: 'SF Mono', 'Fira Code', 'JetBrains Mono', ui-monospace, monospace;
    --sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
    font-family: var(--mono);
    font-size: 12px;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
    padding: 0;
}}

.container {{
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px;
}}

/* Header */
.header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 0;
    border-bottom: 1px solid var(--border);
    margin-bottom: 24px;
    flex-wrap: wrap;
    gap: 12px;
}}
.header h1 {{
    font-size: 16px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--text-dim);
}}
.header h1 span {{ color: var(--green); }}
.header-meta {{
    font-size: 11px;
    color: var(--text-mute);
}}

/* KPI Row */
.kpi-row {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
}}
.kpi-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px;
}}
.kpi-card h3 {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-mute);
    margin-bottom: 12px;
}}
.kpi-pair {{
    display: flex;
    gap: 20px;
}}
.kpi-item {{
    flex: 1;
}}
.kpi-label {{
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-mute);
    margin-bottom: 2px;
}}
.kpi-value {{
    font-size: 22px;
    font-weight: 600;
}}
.kpi-sub {{
    font-size: 10px;
    color: var(--text-dim);
    margin-top: 2px;
}}
.positive {{ color: var(--green); }}
.negative {{ color: var(--red); }}
.neutral {{ color: var(--gray); }}

/* Delta badge */
.delta {{
    display: inline-block;
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 3px;
    margin-left: 6px;
    font-weight: 500;
}}
.delta.up {{ background: var(--green-bg); color: var(--green); }}
.delta.down {{ background: var(--red-bg); color: var(--red); }}

/* Charts section */
.chart-row {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 24px;
}}
@media (max-width: 900px) {{
    .chart-row {{ grid-template-columns: 1fr; }}
}}
.chart-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px;
}}
.chart-card h3 {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-mute);
    margin-bottom: 12px;
}}
.chart-card svg {{
    width: 100%;
    display: block;
}}
.chart-legend {{
    display: flex;
    gap: 16px;
    margin-top: 8px;
    font-size: 10px;
    color: var(--text-dim);
}}
.legend-dot {{
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 4px;
    vertical-align: middle;
}}

/* Win rate bars */
.wr-compare {{
    display: flex;
    gap: 20px;
    align-items: flex-end;
    height: 120px;
    padding-top: 10px;
}}
.wr-bar-group {{
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    height: 100%;
    justify-content: flex-end;
}}
.wr-bar {{
    width: 60px;
    border-radius: 3px 3px 0 0;
    transition: height 0.3s;
}}
.wr-bar-label {{
    font-size: 10px;
    color: var(--text-dim);
    margin-top: 6px;
}}
.wr-bar-value {{
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 4px;
}}

/* Summary stats */
.stats-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 24px;
}}
@media (max-width: 900px) {{
    .stats-grid {{ grid-template-columns: 1fr; }}
}}
.stats-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px;
}}
.stats-card h3 {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-mute);
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 6px;
}}
.stats-card h3 .dot {{
    width: 6px; height: 6px; border-radius: 50%;
}}
.stats-row {{
    display: flex;
    justify-content: space-between;
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
    font-size: 11px;
}}
.stats-row:last-child {{ border-bottom: none; }}
.stats-row .label {{ color: var(--text-dim); }}

/* Trade table */
.table-section {{
    margin-bottom: 24px;
}}
.table-section h3 {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-mute);
    margin-bottom: 12px;
}}
.table-controls {{
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
    flex-wrap: wrap;
}}
.filter-btn {{
    appearance: none;
    border: 1px solid var(--border);
    background: var(--bg-card);
    color: var(--text-dim);
    padding: 4px 12px;
    font-size: 10px;
    border-radius: 3px;
    cursor: pointer;
    font-family: var(--mono);
    text-transform: uppercase;
    letter-spacing: 0.04em;
}}
.filter-btn.active {{
    border-color: var(--green);
    color: var(--green);
    background: var(--green-bg);
}}
.table-wrap {{
    overflow-x: auto;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
    white-space: nowrap;
}}
th {{
    text-align: left;
    padding: 8px 12px;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-mute);
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    user-select: none;
    position: sticky;
    top: 0;
    background: var(--bg-card);
}}
th:hover {{ color: var(--text-dim); }}
th .sort-arrow {{ margin-left: 4px; opacity: 0.3; }}
th.sorted .sort-arrow {{ opacity: 1; color: var(--green); }}
td {{
    padding: 6px 12px;
    border-bottom: 1px solid var(--border);
    color: var(--text-dim);
}}
tr:hover td {{ background: var(--bg-card-hover); }}
.tag {{
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}}
.tag-win {{ background: var(--green-bg); color: var(--green); }}
.tag-loss {{ background: var(--red-bg); color: var(--red); }}
.tag-old {{ background: rgba(102, 102, 128, 0.15); color: var(--gray); }}
.tag-new {{ background: var(--green-bg); color: var(--green); }}
.conf-bar {{
    display: inline-block;
    height: 4px;
    border-radius: 2px;
    background: var(--green-dim);
    vertical-align: middle;
    margin-right: 4px;
}}

/* Footer */
.footer {{
    text-align: center;
    padding: 20px 0;
    font-size: 10px;
    color: var(--text-mute);
    border-top: 1px solid var(--border);
}}
</style>
</head>
<body>
<div class="container" id="app"></div>

<script>
const DATA = {json_data};

function fmt(n, dec=2) {{ return n != null ? n.toFixed(dec) : '-'; }}
function fmtD(n) {{ return n >= 0 ? '+' + fmt(n) : fmt(n); }}
function fmtPct(n) {{ return n >= 0 ? '+' + fmt(n) + '%' : fmt(n) + '%'; }}
function fmtMoney(n) {{ return (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(2); }}
function cls(n) {{ return n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral'; }}

const old = DATA.strategies.old;
const nw = DATA.strategies.new;
const imp = DATA.improvement;
const tw = DATA.test_window;

function deltaHtml(val, suffix='', invert=false) {{
    const up = invert ? val < 0 : val > 0;
    const c = up ? 'up' : 'down';
    const sign = val >= 0 ? '+' : '';
    return `<span class="delta ${{c}}">${{sign}}${{fmt(val)}}${{suffix}}</span>`;
}}

// ── Build the page ──

let html = '';

// Header
html += `
<div class="header">
    <h1>RapidSift <span>/</span> Backtest Comparison</h1>
    <div class="header-meta">
        ${{tw.start}} &mdash; ${{tw.end}} &middot; ${{tw.trading_days}} trading days &middot;
        generated ${{DATA.generated_at}}
    </div>
</div>`;

// KPI Row
html += `<div class="kpi-row">`;

// P&L
html += `<div class="kpi-card"><h3>Total P&amp;L</h3><div class="kpi-pair">
    <div class="kpi-item">
        <div class="kpi-label">Old</div>
        <div class="kpi-value ${{cls(old.pnl)}}">${{fmtMoney(old.pnl)}}</div>
        <div class="kpi-sub">${{fmtPct(old.pnl_pct)}} return</div>
    </div>
    <div class="kpi-item">
        <div class="kpi-label">New</div>
        <div class="kpi-value ${{cls(nw.pnl)}}">${{fmtMoney(nw.pnl)}}</div>
        <div class="kpi-sub">${{fmtPct(nw.pnl_pct)}} return ${{deltaHtml(imp.pnl_delta)}}</div>
    </div>
</div></div>`;

// Win Rate
html += `<div class="kpi-card"><h3>Win Rate</h3><div class="kpi-pair">
    <div class="kpi-item">
        <div class="kpi-label">Old</div>
        <div class="kpi-value ${{cls(old.win_rate - 50)}}">${{fmt(old.win_rate, 1)}}%</div>
        <div class="kpi-sub">${{old.wins}}W / ${{old.losses}}L</div>
    </div>
    <div class="kpi-item">
        <div class="kpi-label">New</div>
        <div class="kpi-value ${{cls(nw.win_rate - 50)}}">${{fmt(nw.win_rate, 1)}}%</div>
        <div class="kpi-sub">${{nw.wins}}W / ${{nw.losses}}L ${{deltaHtml(imp.win_rate_delta, 'pp')}}</div>
    </div>
</div></div>`;

// Sharpe & Avg Return
html += `<div class="kpi-card"><h3>Risk-Adjusted</h3><div class="kpi-pair">
    <div class="kpi-item">
        <div class="kpi-label">Sharpe (Old / New)</div>
        <div class="kpi-value">${{fmt(old.sharpe, 3)}} <span style="color:var(--text-mute)">/</span> <span class="${{cls(nw.sharpe)}}">${{fmt(nw.sharpe, 3)}}</span></div>
        <div class="kpi-sub">${{deltaHtml(imp.sharpe_delta)}}</div>
    </div>
    <div class="kpi-item">
        <div class="kpi-label">Avg Return (Old / New)</div>
        <div class="kpi-value">${{fmtPct(old.avg_return)}} <span style="color:var(--text-mute)">/</span> <span class="${{cls(nw.avg_return)}}">${{fmtPct(nw.avg_return)}}</span></div>
        <div class="kpi-sub">${{deltaHtml(imp.avg_return_delta, 'pp')}}</div>
    </div>
</div></div>`;

html += `</div>`; // end kpi-row

// ── Charts ──
html += `<div class="chart-row">`;

// Cumulative return chart
html += `<div class="chart-card">
    <h3>Cumulative P&amp;L ($)</h3>
    <div id="cum-chart"></div>
    <div class="chart-legend">
        <span><span class="legend-dot" style="background:var(--gray)"></span> Old (Random)</span>
        <span><span class="legend-dot" style="background:var(--green)"></span> New (Filtered)</span>
    </div>
</div>`;

// Daily returns chart
html += `<div class="chart-card">
    <h3>Daily P&amp;L ($)</h3>
    <div id="daily-chart"></div>
    <div class="chart-legend">
        <span><span class="legend-dot" style="background:var(--gray)"></span> Old</span>
        <span><span class="legend-dot" style="background:var(--green)"></span> New</span>
    </div>
</div>`;

html += `</div>`; // end chart-row

// Win rate visual + stats
html += `<div class="chart-row">`;
html += `<div class="chart-card">
    <h3>Win Rate Comparison</h3>
    <div id="wr-chart"></div>
</div>`;

// Improvement summary
html += `<div class="chart-card">
    <h3>Improvement Summary</h3>
    <div id="imp-summary"></div>
</div>`;
html += `</div>`;

// Stats cards
html += `<div class="stats-grid">`;
for (const [strat, label, dotColor] of [[old, 'Old (Random)', 'var(--gray)'], [nw, 'New (Filtered+Ranked)', 'var(--green)']]) {{
    html += `<div class="stats-card">
        <h3><span class="dot" style="background:${{dotColor}}"></span> ${{label}}</h3>`;
    const rows = [
        ['Total Trades', strat.total_trades],
        ['Win / Loss', strat.wins + ' / ' + strat.losses],
        ['Win Rate', fmt(strat.win_rate, 1) + '%'],
        ['Total Invested', '$' + strat.total_invested.toLocaleString()],
        ['Final Value', '$' + strat.total_value.toLocaleString()],
        ['P&L', fmtMoney(strat.pnl)],
        ['P&L %', fmtPct(strat.pnl_pct)],
        ['Avg Return', fmtPct(strat.avg_return)],
        ['Best Trade', strat.best_trades[0].ticker + ' ' + fmtPct(strat.best_trades[0].current_pct)],
        ['Worst Trade', strat.worst_trades[0].ticker + ' ' + fmtPct(strat.worst_trades[0].current_pct)],
        ['Max Drawdown', fmtPct(strat.min_return)],
        ['Sharpe', fmt(strat.sharpe, 3)],
    ];
    for (const [k, v] of rows) {{
        const valCls = typeof v === 'string' && v.startsWith('+') ? 'positive' : typeof v === 'string' && v.startsWith('-') ? 'negative' : '';
        html += `<div class="stats-row"><span class="label">${{k}}</span><span class="${{valCls}}">${{v}}</span></div>`;
    }}
    html += `</div>`;
}}
html += `</div>`;

// Trade table
const allTrades = [];
old.trades.forEach(t => allTrades.push({{...t, strategy: 'old'}}));
nw.trades.forEach(t => allTrades.push({{...t, strategy: 'new'}}));

html += `<div class="table-section">
    <h3>All Trades</h3>
    <div class="table-controls">
        <button class="filter-btn active" data-filter="all">All (${{allTrades.length}})</button>
        <button class="filter-btn" data-filter="old">Old (${{old.trades.length}})</button>
        <button class="filter-btn" data-filter="new">New (${{nw.trades.length}})</button>
        <button class="filter-btn" data-filter="win">Wins</button>
        <button class="filter-btn" data-filter="loss">Losses</button>
    </div>
    <div class="table-wrap">
        <table id="trade-table">
            <thead><tr>
                <th data-col="date">Date <span class="sort-arrow">&#9650;</span></th>
                <th data-col="ticker">Ticker <span class="sort-arrow">&#9650;</span></th>
                <th data-col="type">Type <span class="sort-arrow">&#9650;</span></th>
                <th data-col="strategy">Strategy <span class="sort-arrow">&#9650;</span></th>
                <th data-col="confidence">Conf <span class="sort-arrow">&#9650;</span></th>
                <th data-col="entry_price">Entry <span class="sort-arrow">&#9650;</span></th>
                <th data-col="current_pct">Return% <span class="sort-arrow">&#9650;</span></th>
                <th data-col="result_5">Result <span class="sort-arrow">&#9650;</span></th>
                <th data-col="return_dollar">P&amp;L <span class="sort-arrow">&#9650;</span></th>
            </tr></thead>
            <tbody id="trade-body"></tbody>
        </table>
    </div>
</div>`;

// Footer
html += `<div class="footer">
    RapidSift Backtest &middot; ${{tw.start}} to ${{tw.end}} &middot;
    Training: ${{DATA.training_window.start}} to ${{DATA.training_window.end}} (${{DATA.training_window.trading_days}} days)
</div>`;

document.getElementById('app').innerHTML = html;

// ── Render cumulative chart (SVG) ──
function renderCumChart() {{
    const W = 600, H = 250, P = 40;
    const oldData = old.cumulative;
    const newData = nw.cumulative;
    const allVals = [...oldData.map(d=>d.cumulative), ...newData.map(d=>d.cumulative), 0];
    const minV = Math.min(...allVals);
    const maxV = Math.max(...allVals);
    const range = maxV - minV || 1;

    const n = oldData.length;
    const xStep = (W - 2*P) / Math.max(n - 1, 1);

    function y(v) {{ return H - P - ((v - minV) / range) * (H - 2*P); }}
    function x(i) {{ return P + i * xStep; }}

    let svg = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg">`;

    // Grid
    const nGrid = 5;
    for (let i = 0; i <= nGrid; i++) {{
        const val = minV + (range * i / nGrid);
        const yy = y(val);
        svg += `<line x1="${{P}}" y1="${{yy}}" x2="${{W-P}}" y2="${{yy}}" stroke="var(--border)" stroke-width="0.5"/>`;
        svg += `<text x="${{P-4}}" y="${{yy+3}}" text-anchor="end" font-size="8" fill="var(--text-mute)" font-family="var(--mono)">$${{Math.round(val)}}</text>`;
    }}

    // Zero line
    if (minV < 0 && maxV > 0) {{
        svg += `<line x1="${{P}}" y1="${{y(0)}}" x2="${{W-P}}" y2="${{y(0)}}" stroke="var(--text-mute)" stroke-width="0.5" stroke-dasharray="4,4"/>`;
    }}

    // X labels (every 4th date)
    for (let i = 0; i < n; i += 4) {{
        const label = oldData[i].date.slice(5); // MM-DD
        svg += `<text x="${{x(i)}}" y="${{H-P+14}}" text-anchor="middle" font-size="7" fill="var(--text-mute)" font-family="var(--mono)">${{label}}</text>`;
    }}

    // Old line
    let path = oldData.map((d, i) => `${{i===0?'M':'L'}}${{x(i).toFixed(1)}},${{y(d.cumulative).toFixed(1)}}`).join(' ');
    svg += `<path d="${{path}}" fill="none" stroke="var(--gray)" stroke-width="1.5" opacity="0.7"/>`;

    // New line
    path = newData.map((d, i) => `${{i===0?'M':'L'}}${{x(i).toFixed(1)}},${{y(d.cumulative).toFixed(1)}}`).join(' ');
    svg += `<path d="${{path}}" fill="none" stroke="var(--green)" stroke-width="2"/>`;

    // Area under new line
    let area = newData.map((d, i) => `${{i===0?'M':'L'}}${{x(i).toFixed(1)}},${{y(d.cumulative).toFixed(1)}}`).join(' ');
    area += ` L${{x(n-1).toFixed(1)}},${{y(0).toFixed(1)}} L${{x(0).toFixed(1)}},${{y(0).toFixed(1)}} Z`;
    svg += `<path d="${{area}}" fill="var(--green)" opacity="0.05"/>`;

    // Dots on endpoints
    svg += `<circle cx="${{x(n-1)}}" cy="${{y(oldData[n-1].cumulative)}}" r="3" fill="var(--gray)"/>`;
    svg += `<circle cx="${{x(n-1)}}" cy="${{y(newData[n-1].cumulative)}}" r="3" fill="var(--green)"/>`;

    // End labels
    svg += `<text x="${{x(n-1)+6}}" y="${{y(oldData[n-1].cumulative)+3}}" font-size="9" fill="var(--gray)" font-family="var(--mono)">$${{Math.round(oldData[n-1].cumulative)}}</text>`;
    svg += `<text x="${{x(n-1)+6}}" y="${{y(newData[n-1].cumulative)+3}}" font-size="9" fill="var(--green)" font-family="var(--mono)">$${{Math.round(newData[n-1].cumulative)}}</text>`;

    svg += `</svg>`;
    document.getElementById('cum-chart').innerHTML = svg;
}}

// ── Render daily P&L chart ──
function renderDailyChart() {{
    const W = 600, H = 250, P = 40;
    const oldData = old.daily_pnl;
    const newData = nw.daily_pnl;
    const allVals = [...oldData.map(d=>d.pnl), ...newData.map(d=>d.pnl), 0];
    const minV = Math.min(...allVals);
    const maxV = Math.max(...allVals);
    const range = maxV - minV || 1;

    const n = oldData.length;
    const groupW = (W - 2*P) / n;
    const barW = groupW * 0.35;

    function y(v) {{ return H - P - ((v - minV) / range) * (H - 2*P); }}
    const y0 = y(0);

    let svg = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg">`;

    // Zero line
    svg += `<line x1="${{P}}" y1="${{y0}}" x2="${{W-P}}" y2="${{y0}}" stroke="var(--text-mute)" stroke-width="0.5"/>`;

    // Grid
    for (let i = 0; i <= 4; i++) {{
        const val = minV + (range * i / 4);
        const yy = y(val);
        svg += `<line x1="${{P}}" y1="${{yy}}" x2="${{W-P}}" y2="${{yy}}" stroke="var(--border)" stroke-width="0.5"/>`;
        svg += `<text x="${{P-4}}" y="${{yy+3}}" text-anchor="end" font-size="8" fill="var(--text-mute)" font-family="var(--mono)">$${{Math.round(val)}}</text>`;
    }}

    for (let i = 0; i < n; i++) {{
        const cx = P + groupW * i + groupW / 2;

        // Old bar
        const oh = oldData[i].pnl;
        const oldY = y(oh);
        const oldColor = oh >= 0 ? 'var(--gray)' : 'var(--gray)';
        svg += `<rect x="${{cx - barW - 1}}" y="${{Math.min(y0, oldY)}}" width="${{barW}}" height="${{Math.abs(oldY - y0)}}" fill="${{oldColor}}" opacity="0.5" rx="1"/>`;

        // New bar
        const nh = newData[i].pnl;
        const newY = y(nh);
        const newColor = nh >= 0 ? 'var(--green)' : 'var(--red)';
        svg += `<rect x="${{cx + 1}}" y="${{Math.min(y0, newY)}}" width="${{barW}}" height="${{Math.abs(newY - y0)}}" fill="${{newColor}}" opacity="0.7" rx="1"/>`;

        // X label
        if (i % 4 === 0) {{
            svg += `<text x="${{cx}}" y="${{H-P+14}}" text-anchor="middle" font-size="7" fill="var(--text-mute)" font-family="var(--mono)">${{oldData[i].date.slice(5)}}</text>`;
        }}
    }}

    svg += `</svg>`;
    document.getElementById('daily-chart').innerHTML = svg;
}}

// ── Win rate comparison ──
function renderWRChart() {{
    const maxWR = 100;
    const oldH = (old.win_rate / maxWR) * 100;
    const newH = (nw.win_rate / maxWR) * 100;

    let h = `<div class="wr-compare">
        <div class="wr-bar-group">
            <div class="wr-bar-value neutral">${{fmt(old.win_rate, 1)}}%</div>
            <div class="wr-bar" style="height:${{oldH}}%;background:var(--gray);opacity:0.5"></div>
            <div class="wr-bar-label">Old</div>
        </div>
        <div class="wr-bar-group">
            <div class="wr-bar-value ${{cls(nw.win_rate - old.win_rate)}}">${{fmt(nw.win_rate, 1)}}%</div>
            <div class="wr-bar" style="height:${{newH}}%;background:var(--green)"></div>
            <div class="wr-bar-label">New</div>
        </div>
        <div class="wr-bar-group" style="flex:0.5">
            <div class="wr-bar-value" style="font-size:11px;color:var(--text-mute)">${{fmtPct(imp.win_rate_delta)}}pp</div>
            <div class="wr-bar-label">Delta</div>
        </div>
    </div>`;
    document.getElementById('wr-chart').innerHTML = h;
}}

// ── Improvement summary ──
function renderImpSummary() {{
    const metrics = [
        ['Win Rate', imp.win_rate_delta, 'pp', false],
        ['P&L', imp.pnl_delta, '', false],
        ['Avg Return', imp.avg_return_delta, 'pp', false],
        ['Sharpe', imp.sharpe_delta, '', false],
    ];
    let h = '';
    for (const [name, val, suffix, invert] of metrics) {{
        const up = invert ? val < 0 : val > 0;
        const color = up ? 'var(--green)' : val === 0 ? 'var(--gray)' : 'var(--red)';
        const sign = val >= 0 ? '+' : '';
        const barW = Math.min(Math.abs(val) * 3, 100);
        h += `<div style="margin-bottom:16px">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px">
                <span style="color:var(--text-dim);font-size:10px;text-transform:uppercase">${{name}}</span>
                <span style="color:${{color}};font-weight:600">${{sign}}${{fmt(val)}}${{suffix}}</span>
            </div>
            <div style="background:var(--border);height:6px;border-radius:3px;overflow:hidden">
                <div style="width:${{barW}}%;height:100%;background:${{color}};border-radius:3px;transition:width 0.3s"></div>
            </div>
        </div>`;
    }}
    document.getElementById('imp-summary').innerHTML = h;
}}

// ── Trade table ──
let currentFilter = 'all';
let sortCol = 'date';
let sortAsc = false;

function renderTradeTable() {{
    let filtered = allTrades;
    if (currentFilter === 'old') filtered = filtered.filter(t => t.strategy === 'old');
    if (currentFilter === 'new') filtered = filtered.filter(t => t.strategy === 'new');
    if (currentFilter === 'win') filtered = filtered.filter(t => t.result_5 === 'win');
    if (currentFilter === 'loss') filtered = filtered.filter(t => t.result_5 === 'loss');

    filtered.sort((a, b) => {{
        let va = a[sortCol], vb = b[sortCol];
        if (typeof va === 'number' && typeof vb === 'number') return sortAsc ? va - vb : vb - va;
        if (va == null) va = '';
        if (vb == null) vb = '';
        return sortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
    }});

    let rows = '';
    for (const t of filtered) {{
        const retCls = t.current_pct >= 0 ? 'positive' : 'negative';
        const resCls = t.result_5 === 'win' ? 'tag-win' : 'tag-loss';
        const stratCls = t.strategy === 'new' ? 'tag-new' : 'tag-old';
        const confHtml = t.confidence != null
            ? `<span class="conf-bar" style="width:${{t.confidence * 0.4}}px"></span>${{t.confidence}}`
            : '<span style="color:var(--text-mute)">-</span>';
        const pnlCls = t.return_dollar >= 0 ? 'positive' : 'negative';

        rows += `<tr>
            <td>${{t.date}}</td>
            <td style="color:var(--text);font-weight:500">${{t.ticker}}</td>
            <td>${{t.type}}</td>
            <td><span class="tag ${{stratCls}}">${{t.strategy}}</span></td>
            <td>${{confHtml}}</td>
            <td>$${{fmt(t.entry_price)}}</td>
            <td class="${{retCls}}">${{fmtPct(t.current_pct)}}</td>
            <td><span class="tag ${{resCls}}">${{t.result_5}}</span></td>
            <td class="${{pnlCls}}">${{fmtMoney(t.return_dollar)}}</td>
        </tr>`;
    }}
    document.getElementById('trade-body').innerHTML = rows;

    // Update sort arrows
    document.querySelectorAll('#trade-table th').forEach(th => {{
        th.classList.toggle('sorted', th.dataset.col === sortCol);
        const arrow = th.querySelector('.sort-arrow');
        if (arrow) arrow.innerHTML = (th.dataset.col === sortCol && sortAsc) ? '&#9650;' : '&#9660;';
    }});
}}

// Event listeners
document.querySelectorAll('.filter-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentFilter = btn.dataset.filter;
        renderTradeTable();
    }});
}});

document.querySelectorAll('#trade-table th').forEach(th => {{
    th.addEventListener('click', () => {{
        if (sortCol === th.dataset.col) {{
            sortAsc = !sortAsc;
        }} else {{
            sortCol = th.dataset.col;
            sortAsc = true;
        }}
        renderTradeTable();
    }});
}});

// Render all
renderCumChart();
renderDailyChart();
renderWRChart();
renderImpSummary();
renderTradeTable();
</script>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html)


if __name__ == "__main__":
    main()
