#!/usr/bin/env python3
"""
RapidSift Weekly Performance Report

Analyzes signal_history.csv and sends a summary to Telegram.
Designed to run as a GitHub Actions cron job every Monday.

Requires env vars:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SITE_URL = "https://rapidsift.vercel.app"

HISTORY_CSV = Path(__file__).parent.parent / "data" / "signal_history.csv"

SIGNAL_LABELS = {
    "macd": "MACD", "volume_spike": "Vol Spike",
    "adjusted_drop": "Adj Drop", "adjusted_surge": "Adj Surge",
    "sector_drop": "Sector Drop", "sector_surge": "Sector Surge",
    "gap_and_go": "Gap & Go", "rel_strength": "Rel Strength",
    "earnings_drift": "Earn Drift", "confluence": "Confluence",
}


def send_message(text):
    if BOT_TOKEN and CHAT_ID:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
        )
        print(f"Telegram: {r.status_code}")
    else:
        print("No Telegram credentials. Report:\n")
        print(text)


def load_history():
    rows = []
    if not HISTORY_CSV.exists():
        return rows
    with open(HISTORY_CSV) as f:
        for row in csv.DictReader(f):
            if row.get("result_5") in ("win", "loss"):
                try:
                    row["_date"] = datetime.strptime(row["date"], "%Y-%m-%d").date()
                    row["_pct"] = float(row.get("current_pct", 0))
                    row["_dd"] = float(row.get("max_drawdown_pct", 0))
                except (ValueError, TypeError):
                    continue
                rows.append(row)
    return rows


def analyze(rows):
    today = datetime.utcnow().date()
    week_ago = today - timedelta(days=7)
    two_weeks_ago = today - timedelta(days=14)
    month_ago = today - timedelta(days=30)

    this_week = [r for r in rows if r["_date"] >= week_ago]
    last_week = [r for r in rows if two_weeks_ago <= r["_date"] < week_ago]
    last_30d = [r for r in rows if r["_date"] >= month_ago]

    def wr(signals):
        if not signals:
            return 0, 0
        wins = sum(1 for s in signals if s["result_5"] == "win")
        return round(wins / len(signals) * 100, 1), len(signals)

    # Overall
    tw_wr, tw_n = wr(this_week)
    lw_wr, lw_n = wr(last_week)
    all_wr, all_n = wr(rows)

    # By type (last 30 days)
    type_30d = defaultdict(list)
    type_all = defaultdict(list)
    for r in last_30d:
        type_30d[r["type"]].append(r)
    for r in rows:
        type_all[r["type"]].append(r)

    type_lines = []
    all_types = sorted(set(type_30d.keys()) | set(type_all.keys()))
    for t in all_types:
        wr_30, n_30 = wr(type_30d.get(t, []))
        wr_all, n_all = wr(type_all.get(t, []))
        if n_30 < 5:
            continue
        label = SIGNAL_LABELS.get(t, t)
        # Trend arrow
        diff = wr_30 - wr_all
        arrow = "↑" if diff > 3 else ("↓" if diff < -3 else "→")
        warn = " ⚠️" if wr_30 < 50 else ""
        type_lines.append((wr_30, f"  {label:14s} {wr_30:5.1f}% (n={n_30:3d}) {arrow}{warn}"))

    type_lines.sort(key=lambda x: -x[0])
    type_block = "\n".join(line for _, line in type_lines) if type_lines else "  Insufficient data"

    # Top wins and losses this week
    this_week_sorted = sorted(this_week, key=lambda r: r["_pct"], reverse=True)
    top_wins = this_week_sorted[:5]
    top_losses = this_week_sorted[-5:][::-1] if len(this_week_sorted) >= 5 else []

    def fmt_signal(s):
        pct = s["_pct"]
        sign = "+" if pct >= 0 else ""
        return f"{s['ticker']} {sign}{pct:.1f}%"

    wins_str = ", ".join(fmt_signal(s) for s in top_wins) if top_wins else "—"
    losses_str = ", ".join(fmt_signal(s) for s in top_losses) if top_losses else "—"

    # Degrading types (30d WR < all-time WR by >5pp AND >10 signals)
    warnings = []
    for t in all_types:
        wr_30, n_30 = wr(type_30d.get(t, []))
        wr_all, n_all = wr(type_all.get(t, []))
        if n_30 >= 10 and (wr_all - wr_30) > 5:
            label = SIGNAL_LABELS.get(t, t)
            warnings.append(f"{label} {wr_30:.0f}% (was {wr_all:.0f}%)")

    warn_block = "\n".join(f"  ⚠️ {w}" for w in warnings) if warnings else "  None — all types healthy"

    # Average drawdown
    all_dd = [r["_dd"] for r in rows if r["_dd"] != 0]
    avg_dd = sum(all_dd) / len(all_dd) if all_dd else 0
    worst_dd = min(all_dd) if all_dd else 0

    date_range = f"{week_ago.strftime('%b %d')} — {today.strftime('%b %d, %Y')}"

    msg = (
        f"<b>📈 RapidSift Weekly Report</b>\n"
        f"<i>{date_range}</i>\n\n"
        f"<b>This Week:</b> {tw_n} signals, {tw_wr}% WR (+5%)\n"
        f"<b>Last Week:</b> {lw_n} signals, {lw_wr}% WR\n"
        f"<b>All Time:</b>  {all_n} signals, {all_wr}% WR\n\n"
        f"<b>By Type (30d):</b>\n<pre>{type_block}</pre>\n\n"
        f"<b>Top Wins:</b> {wins_str}\n"
        f"<b>Top Losses:</b> {losses_str}\n\n"
        f"<b>Risk:</b> avg drawdown {avg_dd:.1f}%, worst {worst_dd:.1f}%\n\n"
        f"<b>Watchlist:</b>\n{warn_block}\n\n"
        f"<a href=\"{SITE_URL}\">Open Dashboard →</a>"
    )

    return msg


def main():
    rows = load_history()
    if len(rows) < 20:
        print(f"Only {len(rows)} completed signals in history — not enough for a report.")
        if BOT_TOKEN and CHAT_ID:
            send_message(
                "📈 <b>RapidSift Weekly Report</b>\n\n"
                f"Only {len(rows)} completed signals — need more data for a meaningful report.\n"
                f"<a href=\"{SITE_URL}\">Open Dashboard →</a>"
            )
        return

    print(f"Analyzing {len(rows)} historical signals...")
    msg = analyze(rows)
    send_message(msg)
    print("Done.")


if __name__ == "__main__":
    main()
