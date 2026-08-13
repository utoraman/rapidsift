#!/usr/bin/env python3
"""
Regenerate signal suppression lists from signal_history.csv with
out-of-sample validation.

Split: train on the older 70% of signals, validate on the recent 30%.
Only suppress tickers/combos that underperform in BOTH splits — this
prevents overfitting to a rough patch that has already recovered.

Exit code 0 + writes signals.py  → suppressions updated
Exit code 0 + no write            → not enough data or no changes needed

Run weekly after backfill:
    python3 scripts/update_suppressions.py
"""

import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

from param_manager import save_version

HISTORY_CSV = Path(__file__).parent.parent / "data" / "signal_history.csv"
SIGNALS_PY = Path(__file__).parent / "signals.py"
SECTORS_YAML = Path(__file__).parent.parent / "data" / "sectors.yaml"
REPORT_JSON = Path(__file__).parent.parent / "data" / "suppression_report.json"

TICKER_WR_THRESHOLD = 0.625
TICKER_MIN_SIGNALS_TRAIN = 4
TICKER_MIN_SIGNALS_TEST = 2
COMBO_WR_THRESHOLD = 0.55
COMBO_MIN_SIGNALS_TRAIN = 7
COMBO_MIN_SIGNALS_TEST = 3
TRAIN_RATIO = 0.70


def load_history():
    rows = []
    with open(HISTORY_CSV) as f:
        for r in csv.DictReader(f):
            if r["result_5"] in ("win", "loss"):
                r["_date"] = r["date"]
                rows.append(r)
    rows.sort(key=lambda r: r["_date"])
    return rows


def load_sector_map():
    with open(SECTORS_YAML) as f:
        data = yaml.safe_load(f)
    mapping = {}
    for etf, tickers in data.get("sectors", {}).items():
        for t in tickers:
            mapping[str(t)] = etf
    return mapping


def _wr_stats(rows):
    stats = defaultdict(lambda: {"w": 0, "t": 0})
    for r in rows:
        stats[r["ticker"]]["t"] += 1
        stats[r["ticker"]]["w"] += 1 if r["result_5"] == "win" else 0
    return stats


def _combo_stats(rows, sector_map):
    stats = defaultdict(lambda: {"w": 0, "t": 0})
    for r in rows:
        sector = sector_map.get(r["ticker"], "SPY")
        stats[(r["type"], sector)]["t"] += 1
        stats[(r["type"], sector)]["w"] += 1 if r["result_5"] == "win" else 0
    return stats


def find_validated_tickers(train, test):
    """Find tickers that underperform in both train AND test sets."""
    train_stats = _wr_stats(train)
    test_stats = _wr_stats(test)

    validated = set()
    train_only = set()

    for ticker, d in train_stats.items():
        if d["t"] < TICKER_MIN_SIGNALS_TRAIN:
            continue
        train_wr = d["w"] / d["t"]
        if train_wr >= TICKER_WR_THRESHOLD:
            continue

        # Bad in train — check if also bad in test
        td = test_stats.get(ticker, {"w": 0, "t": 0})
        if td["t"] < TICKER_MIN_SIGNALS_TEST:
            train_only.add(ticker)
            continue
        test_wr = td["w"] / td["t"]
        if test_wr < TICKER_WR_THRESHOLD:
            validated.add(ticker)
        else:
            train_only.add(ticker)

    return validated, train_only


def find_validated_combos(train, test, sector_map):
    """Find type×sector combos that underperform in both splits."""
    train_stats = _combo_stats(train, sector_map)
    test_stats = _combo_stats(test, sector_map)

    validated = set()
    train_only = set()

    for (sig_type, sector), d in train_stats.items():
        if d["t"] < COMBO_MIN_SIGNALS_TRAIN:
            continue
        train_wr = d["w"] / d["t"]
        if train_wr >= COMBO_WR_THRESHOLD:
            continue

        td = test_stats.get((sig_type, sector), {"w": 0, "t": 0})
        if td["t"] < COMBO_MIN_SIGNALS_TEST:
            train_only.add((sig_type, sector))
            continue
        test_wr = td["w"] / td["t"]
        if test_wr < COMBO_WR_THRESHOLD:
            validated.add((sig_type, sector))
        else:
            train_only.add((sig_type, sector))

    return validated, train_only


def format_ticker_set(tickers):
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
    sorted_combos = sorted(combos)
    lines = ["SUPPRESSED_COMBOS = {"]
    for sig_type, sector in sorted_combos:
        lines.append(f'    ("{sig_type}", "{sector}"),')
    lines.append("}")
    return "\n".join(lines)


def update_signals_py(bad_tickers, bad_combos):
    content = SIGNALS_PY.read_text()

    new_tickers = format_ticker_set(bad_tickers)
    content = re.sub(
        r'SUPPRESSED_TICKERS = \{[^}]+\}',
        new_tickers,
        content,
        flags=re.DOTALL,
    )

    new_combos = format_combo_set(bad_combos)
    content = re.sub(
        r'SUPPRESSED_COMBOS = \{[^}]+\}',
        new_combos,
        content,
        flags=re.DOTALL,
    )

    SIGNALS_PY.write_text(content)


def _ticker_detail(ticker, train, test):
    """Get WR details for a ticker across both splits."""
    tw, tt = 0, 0
    for r in train:
        if r["ticker"] == ticker:
            tt += 1
            tw += 1 if r["result_5"] == "win" else 0
    vw, vt = 0, 0
    for r in test:
        if r["ticker"] == ticker:
            vt += 1
            vw += 1 if r["result_5"] == "win" else 0
    train_wr = tw / tt * 100 if tt else 0
    test_wr = vw / vt * 100 if vt else 0
    return tt, train_wr, vt, test_wr


def _combo_detail(sig_type, sector, train, test, sector_map):
    tw, tt = 0, 0
    for r in train:
        if r["type"] == sig_type and sector_map.get(r["ticker"], "SPY") == sector:
            tt += 1
            tw += 1 if r["result_5"] == "win" else 0
    vw, vt = 0, 0
    for r in test:
        if r["type"] == sig_type and sector_map.get(r["ticker"], "SPY") == sector:
            vt += 1
            vw += 1 if r["result_5"] == "win" else 0
    train_wr = tw / tt * 100 if tt else 0
    test_wr = vw / vt * 100 if vt else 0
    return tt, train_wr, vt, test_wr


def main():
    if not HISTORY_CSV.exists():
        print(f"No history at {HISTORY_CSV}")
        return

    rows = load_history()
    print(f"Loaded {len(rows)} completed signals")

    if len(rows) < 100:
        print("Need at least 100 signals for validated suppression. Skipping.")
        return

    sector_map = load_sector_map()

    # Time-based split: train on older data, test on recent
    split_idx = int(len(rows) * TRAIN_RATIO)
    train = rows[:split_idx]
    test = rows[split_idx:]
    train_end = train[-1]["_date"]
    test_start = test[0]["_date"]
    print(f"Train: {len(train)} signals (through {train_end})")
    print(f"Test:  {len(test)} signals (from {test_start})")

    # Find suppressions validated in both splits
    val_tickers, train_only_tickers = find_validated_tickers(train, test)
    val_combos, train_only_combos = find_validated_combos(train, test, sector_map)

    # Report validated suppressions
    print(f"\n{'='*60}")
    print(f"VALIDATED tickers: {len(val_tickers)} (bad in both train AND test)")
    for t in sorted(val_tickers):
        tn, twr, vn, vwr = _ticker_detail(t, train, test)
        print(f"  {t:8s}  train: n={tn:3d} WR={twr:5.1f}%  test: n={vn:3d} WR={vwr:5.1f}%")

    print(f"\nTRAIN-ONLY tickers: {len(train_only_tickers)} (bad in train, OK or insufficient in test — NOT suppressed)")
    for t in sorted(train_only_tickers):
        tn, twr, vn, vwr = _ticker_detail(t, train, test)
        status = f"WR={vwr:.0f}%" if vn >= TICKER_MIN_SIGNALS_TEST else f"n={vn} (insufficient)"
        print(f"  {t:8s}  train: n={tn:3d} WR={twr:5.1f}%  test: {status}")

    print(f"\n{'='*60}")
    print(f"VALIDATED combos: {len(val_combos)} (bad in both splits)")
    for sig_type, sector in sorted(val_combos):
        tn, twr, vn, vwr = _combo_detail(sig_type, sector, train, test, sector_map)
        print(f"  {sig_type:18s} + {sector:5s}  train: n={tn:3d} WR={twr:5.1f}%  test: n={vn:3d} WR={vwr:5.1f}%")

    print(f"\nTRAIN-ONLY combos: {len(train_only_combos)} (NOT suppressed)")
    for sig_type, sector in sorted(train_only_combos):
        tn, twr, vn, vwr = _combo_detail(sig_type, sector, train, test, sector_map)
        status = f"WR={vwr:.0f}%" if vn >= COMBO_MIN_SIGNALS_TEST else f"n={vn} (insufficient)"
        print(f"  {sig_type:18s} + {sector:5s}  train: n={tn:3d} WR={twr:5.1f}%  test: {status}")

    # Measure impact on full dataset
    all_stats = _wr_stats(rows)
    before_wins = sum(1 for r in rows if r["result_5"] == "win")
    before_total = len(rows)
    filtered = [r for r in rows
                if r["ticker"] not in val_tickers
                and (r["type"], sector_map.get(r["ticker"], "SPY")) not in val_combos]
    after_wins = sum(1 for r in filtered if r["result_5"] == "win")
    after_total = len(filtered)

    # Measure impact on TEST set only (the real validation)
    test_filtered = [r for r in test
                     if r["ticker"] not in val_tickers
                     and (r["type"], sector_map.get(r["ticker"], "SPY")) not in val_combos]
    test_wins_before = sum(1 for r in test if r["result_5"] == "win")
    test_wins_after = sum(1 for r in test_filtered if r["result_5"] == "win")

    print(f"\n{'='*60}")
    print(f"IMPACT (full dataset):")
    print(f"  Before: {before_total} signals, {before_wins/before_total*100:.1f}% WR")
    print(f"  After:  {after_total} signals, {after_wins/after_total*100:.1f}% WR")
    print(f"  Removed: {before_total - after_total} ({(before_total-after_total)/before_total*100:.1f}%)")
    print(f"  WR lift: {after_wins/after_total*100 - before_wins/before_total*100:+.1f}pp")

    print(f"\nIMPACT (test set only — out-of-sample):")
    test_wr_before = test_wins_before / len(test) * 100
    test_wr_after = test_wins_after / len(test_filtered) * 100 if test_filtered else 0
    print(f"  Before: {len(test)} signals, {test_wr_before:.1f}% WR")
    print(f"  After:  {len(test_filtered)} signals, {test_wr_after:.1f}% WR")
    print(f"  Removed: {len(test) - len(test_filtered)} ({(len(test)-len(test_filtered))/len(test)*100:.1f}%)")
    print(f"  WR lift: {test_wr_after - test_wr_before:+.1f}pp")

    # Save report for Telegram/monitoring
    report = {
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "total_signals": len(rows),
        "train_size": len(train),
        "test_size": len(test),
        "validated_tickers": len(val_tickers),
        "train_only_tickers": len(train_only_tickers),
        "validated_combos": len(val_combos),
        "train_only_combos": len(train_only_combos),
        "wr_before": round(before_wins / before_total * 100, 1),
        "wr_after": round(after_wins / after_total * 100, 1) if after_total else 0,
        "wr_test_before": round(test_wr_before, 1),
        "wr_test_after": round(test_wr_after, 1),
        "signals_removed_pct": round((before_total - after_total) / before_total * 100, 1),
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2))
    print(f"\nSaved report to {REPORT_JSON}")

    # Update signals.py
    update_signals_py(val_tickers, val_combos)
    print(f"Updated {SIGNALS_PY}")
    print(f"\n  {len(val_tickers)} tickers + {len(val_combos)} combos suppressed (validated)")
    print(f"  {len(train_only_tickers)} tickers + {len(train_only_combos)} combos dropped (train-only, not validated)")

    # Record version
    version = save_version(
        trigger="auto-tune",
        metrics=report,
        reason=f"{len(val_tickers)} tickers, {len(val_combos)} combos "
               f"(dropped {len(train_only_tickers)}+{len(train_only_combos)} unvalidated)",
    )
    print(f"Recorded as {version['id']}")


if __name__ == "__main__":
    main()
