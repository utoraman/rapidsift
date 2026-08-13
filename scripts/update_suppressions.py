#!/usr/bin/env python3
"""
Regenerate signal suppression lists from signal_history.csv.

Analyzes historical signal performance and updates the SUPPRESSED_TICKERS
and SUPPRESSED_COMBOS constants in signals.py.

Run after a backfill to keep suppression lists current:
    python3 scripts/update_suppressions.py

Uses a 62.5% WR random baseline threshold for tickers (min 5 signals)
and 55% WR threshold for type×sector combos (min 10 signals).
"""

import csv
import re
from collections import defaultdict
from pathlib import Path

import yaml

HISTORY_CSV = Path(__file__).parent.parent / "data" / "signal_history.csv"
SIGNALS_PY = Path(__file__).parent / "signals.py"
SECTORS_YAML = Path(__file__).parent.parent / "data" / "sectors.yaml"

TICKER_WR_THRESHOLD = 0.625  # random baseline
TICKER_MIN_SIGNALS = 5
COMBO_WR_THRESHOLD = 0.55
COMBO_MIN_SIGNALS = 10


def load_history():
    rows = []
    with open(HISTORY_CSV) as f:
        for r in csv.DictReader(f):
            if r["result_5"] in ("win", "loss"):
                rows.append(r)
    return rows


def load_sector_map():
    with open(SECTORS_YAML) as f:
        data = yaml.safe_load(f)
    mapping = {}
    for etf, tickers in data.get("sectors", {}).items():
        for t in tickers:
            mapping[str(t)] = etf
    return mapping


def find_bad_tickers(rows):
    stats = defaultdict(lambda: {"w": 0, "t": 0})
    for r in rows:
        stats[r["ticker"]]["t"] += 1
        stats[r["ticker"]]["w"] += 1 if r["result_5"] == "win" else 0

    bad = set()
    for ticker, d in stats.items():
        if d["t"] >= TICKER_MIN_SIGNALS:
            wr = d["w"] / d["t"]
            if wr < TICKER_WR_THRESHOLD:
                bad.add(ticker)
    return bad


def find_bad_combos(rows, sector_map):
    stats = defaultdict(lambda: {"w": 0, "t": 0})
    for r in rows:
        sector = sector_map.get(r["ticker"], "SPY")
        stats[(r["type"], sector)]["t"] += 1
        stats[(r["type"], sector)]["w"] += 1 if r["result_5"] == "win" else 0

    bad = set()
    for (sig_type, sector), d in stats.items():
        if d["t"] >= COMBO_MIN_SIGNALS:
            wr = d["w"] / d["t"]
            if wr < COMBO_WR_THRESHOLD:
                bad.add((sig_type, sector))
    return bad


def format_ticker_set(tickers):
    """Format a set of tickers as a Python set literal, wrapped at ~80 chars."""
    sorted_tickers = sorted(tickers)
    lines = ["SUPPRESSED_TICKERS = {"]
    line = "    "
    for i, t in enumerate(sorted_tickers):
        entry = f'"{t}"'
        if i < len(sorted_tickers) - 1:
            entry += ", "
        if len(line) + len(entry) > 85:
            lines.append(line)
            line = "    "
        line += entry
    lines.append(line)
    lines.append("}")
    return "\n".join(lines)


def format_combo_set(combos):
    """Format a set of (type, sector) tuples as a Python set literal."""
    sorted_combos = sorted(combos)
    lines = ["SUPPRESSED_COMBOS = {"]
    for sig_type, sector in sorted_combos:
        lines.append(f'    ("{sig_type}", "{sector}"),')
    lines.append("}")
    return "\n".join(lines)


def update_signals_py(bad_tickers, bad_combos):
    """Replace SUPPRESSED_TICKERS and SUPPRESSED_COMBOS in signals.py."""
    content = SIGNALS_PY.read_text()

    # Replace SUPPRESSED_TICKERS block
    new_tickers = format_ticker_set(bad_tickers)
    content = re.sub(
        r'SUPPRESSED_TICKERS = \{[^}]+\}',
        new_tickers,
        content,
        flags=re.DOTALL,
    )

    # Replace SUPPRESSED_COMBOS block
    new_combos = format_combo_set(bad_combos)
    content = re.sub(
        r'SUPPRESSED_COMBOS = \{[^}]+\}',
        new_combos,
        content,
        flags=re.DOTALL,
    )

    SIGNALS_PY.write_text(content)


def main():
    if not HISTORY_CSV.exists():
        print(f"No history at {HISTORY_CSV}")
        return

    rows = load_history()
    print(f"Loaded {len(rows)} completed signals")

    sector_map = load_sector_map()

    bad_tickers = find_bad_tickers(rows)
    bad_combos = find_bad_combos(rows, sector_map)

    # Report
    total_tickers = len(set(r["ticker"] for r in rows if
                           sum(1 for r2 in rows if r2["ticker"] == r["ticker"]) >= TICKER_MIN_SIGNALS))

    print(f"\nSuppressed tickers: {len(bad_tickers)} (WR < {TICKER_WR_THRESHOLD*100:.1f}%, min {TICKER_MIN_SIGNALS} signals)")
    for t in sorted(bad_tickers):
        stats = {"w": 0, "t": 0}
        for r in rows:
            if r["ticker"] == t:
                stats["t"] += 1
                stats["w"] += 1 if r["result_5"] == "win" else 0
        wr = stats["w"] / stats["t"] * 100
        print(f"  {t:8s}  n={stats['t']:3d}  WR={wr:5.1f}%")

    print(f"\nSuppressed combos: {len(bad_combos)} (WR < {COMBO_WR_THRESHOLD*100:.1f}%, min {COMBO_MIN_SIGNALS} signals)")
    for sig_type, sector in sorted(bad_combos):
        stats = {"w": 0, "t": 0}
        for r in rows:
            s = sector_map.get(r["ticker"], "SPY")
            if r["type"] == sig_type and s == sector:
                stats["t"] += 1
                stats["w"] += 1 if r["result_5"] == "win" else 0
        wr = stats["w"] / stats["t"] * 100
        print(f"  {sig_type:18s} + {sector:5s}  n={stats['t']:3d}  WR={wr:5.1f}%")

    # Estimate impact
    before_wins = sum(1 for r in rows if r["result_5"] == "win")
    before_total = len(rows)
    filtered = [r for r in rows
                if r["ticker"] not in bad_tickers
                and (r["type"], sector_map.get(r["ticker"], "SPY")) not in bad_combos]
    after_wins = sum(1 for r in filtered if r["result_5"] == "win")
    after_total = len(filtered)

    print(f"\nImpact:")
    print(f"  Before: {before_total} signals, {before_wins/before_total*100:.1f}% WR")
    print(f"  After:  {after_total} signals, {after_wins/after_total*100:.1f}% WR")
    print(f"  Removed: {before_total - after_total} signals ({(before_total-after_total)/before_total*100:.1f}%)")
    print(f"  WR lift: {after_wins/after_total*100 - before_wins/before_total*100:+.1f}pp")

    update_signals_py(bad_tickers, bad_combos)
    print(f"\nUpdated {SIGNALS_PY}")


if __name__ == "__main__":
    main()
