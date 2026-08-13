#!/usr/bin/env python3
"""
Signal parameter version manager.

Tracks changes to suppression lists and scoring thresholds in an
append-only changelog. Supports listing history, diffing versions,
and rolling back to any previous state.

Usage:
    python3 scripts/param_manager.py                  # show current + last 5 versions
    python3 scripts/param_manager.py list              # list all versions
    python3 scripts/param_manager.py show v3           # show version 3 details
    python3 scripts/param_manager.py diff v2 v3        # diff two versions
    python3 scripts/param_manager.py rollback v3       # restore version 3 to signals.py
    python3 scripts/param_manager.py snapshot           # save current state as new version

Called programmatically:
    from param_manager import save_version, get_current, get_version, rollback_to
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

VERSIONS_FILE = Path(__file__).parent.parent / "data" / "param_versions.json"
SIGNALS_PY = Path(__file__).parent / "signals.py"


def _load_versions():
    if VERSIONS_FILE.exists():
        return json.loads(VERSIONS_FILE.read_text())
    return {"versions": []}


def _save_versions(data):
    VERSIONS_FILE.write_text(json.dumps(data, indent=2))


def _read_current_params():
    """Read current suppression params from signals.py."""
    content = SIGNALS_PY.read_text()

    tickers_match = re.search(r'SUPPRESSED_TICKERS = \{([^}]*)\}', content, re.DOTALL)
    tickers = set()
    if tickers_match:
        for m in re.finditer(r'"([^"]+)"', tickers_match.group(1)):
            tickers.add(m.group(1))

    combos_match = re.search(r'SUPPRESSED_COMBOS = \{([^}]*)\}', content, re.DOTALL)
    combos = []
    if combos_match:
        for m in re.finditer(r'\("([^"]+)",\s*"([^"]+)"\)', combos_match.group(1)):
            combos.append([m.group(1), m.group(2)])

    # Read thresholds from update_suppressions.py
    thresholds = {}
    update_script = Path(__file__).parent / "update_suppressions.py"
    if update_script.exists():
        uc = update_script.read_text()
        for name in ["TICKER_WR_THRESHOLD", "TICKER_MIN_SIGNALS_TRAIN",
                      "TICKER_MIN_SIGNALS_TEST", "COMBO_WR_THRESHOLD",
                      "COMBO_MIN_SIGNALS_TRAIN", "COMBO_MIN_SIGNALS_TEST",
                      "TRAIN_RATIO"]:
            m = re.search(rf'{name}\s*=\s*([\d.]+)', uc)
            if m:
                thresholds[name] = float(m.group(1))

    return {
        "suppressed_tickers": sorted(tickers),
        "suppressed_combos": sorted(combos),
        "thresholds": thresholds,
    }


def get_current():
    return _read_current_params()


def get_version(version_id):
    data = _load_versions()
    for v in data["versions"]:
        if v["id"] == version_id:
            return v
    return None


def save_version(trigger="manual", metrics=None, reason=""):
    """Save current signal parameters as a new version."""
    data = _load_versions()
    versions = data["versions"]

    next_num = len(versions) + 1
    version_id = f"v{next_num}"

    params = _read_current_params()

    entry = {
        "id": version_id,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trigger": trigger,
        "reason": reason,
        "params": params,
    }
    if metrics:
        entry["metrics"] = metrics

    # Compute diff from previous version
    if versions:
        prev = versions[-1]["params"]
        diff = _compute_diff(prev, params)
        entry["diff"] = diff

    versions.append(entry)
    _save_versions(data)
    return entry


def _compute_diff(old, new):
    """Compute what changed between two parameter snapshots."""
    diff = {}

    old_tickers = set(old.get("suppressed_tickers", []))
    new_tickers = set(new.get("suppressed_tickers", []))
    added_t = sorted(new_tickers - old_tickers)
    removed_t = sorted(old_tickers - new_tickers)
    if added_t or removed_t:
        diff["tickers"] = {}
        if added_t:
            diff["tickers"]["added"] = added_t
        if removed_t:
            diff["tickers"]["removed"] = removed_t

    old_combos = set(tuple(c) for c in old.get("suppressed_combos", []))
    new_combos = set(tuple(c) for c in new.get("suppressed_combos", []))
    added_c = sorted(new_combos - old_combos)
    removed_c = sorted(old_combos - new_combos)
    if added_c or removed_c:
        diff["combos"] = {}
        if added_c:
            diff["combos"]["added"] = [list(c) for c in added_c]
        if removed_c:
            diff["combos"]["removed"] = [list(c) for c in removed_c]

    old_th = old.get("thresholds", {})
    new_th = new.get("thresholds", {})
    th_changes = {}
    for k in set(list(old_th.keys()) + list(new_th.keys())):
        if old_th.get(k) != new_th.get(k):
            th_changes[k] = {"from": old_th.get(k), "to": new_th.get(k)}
    if th_changes:
        diff["thresholds"] = th_changes

    return diff


def rollback_to(version_id):
    """Restore signals.py to a previous version's parameters."""
    v = get_version(version_id)
    if not v:
        print(f"Version {version_id} not found")
        return False

    params = v["params"]
    content = SIGNALS_PY.read_text()

    # Restore tickers
    tickers = set(params.get("suppressed_tickers", []))
    from update_suppressions import format_ticker_set, format_combo_set
    new_tickers = format_ticker_set(tickers)
    content = re.sub(
        r'SUPPRESSED_TICKERS = \{[^}]+\}',
        new_tickers,
        content,
        flags=re.DOTALL,
    )

    # Restore combos
    combos = set(tuple(c) for c in params.get("suppressed_combos", []))
    new_combos = format_combo_set(combos)
    content = re.sub(
        r'SUPPRESSED_COMBOS = \{[^}]+\}',
        new_combos,
        content,
        flags=re.DOTALL,
    )

    SIGNALS_PY.write_text(content)

    # Record the rollback as a new version
    save_version(
        trigger="rollback",
        reason=f"Rolled back to {version_id}",
    )

    return True


def _fmt_version(v, verbose=False):
    """Format a version entry for display."""
    vid = v["id"]
    ts = v["timestamp"][:16].replace("T", " ")
    trigger = v["trigger"]
    reason = v.get("reason", "")
    params = v["params"]
    n_tickers = len(params.get("suppressed_tickers", []))
    n_combos = len(params.get("suppressed_combos", []))

    line = f"  {vid:5s}  {ts}  [{trigger:10s}]  {n_tickers} tickers, {n_combos} combos"
    if reason:
        line += f"  — {reason}"

    if verbose:
        lines = [line]
        diff = v.get("diff", {})
        if diff.get("tickers", {}).get("added"):
            lines.append(f"         + tickers: {', '.join(diff['tickers']['added'])}")
        if diff.get("tickers", {}).get("removed"):
            lines.append(f"         - tickers: {', '.join(diff['tickers']['removed'])}")
        if diff.get("combos", {}).get("added"):
            for c in diff["combos"]["added"]:
                lines.append(f"         + combo: {c[0]}+{c[1]}")
        if diff.get("combos", {}).get("removed"):
            for c in diff["combos"]["removed"]:
                lines.append(f"         - combo: {c[0]}+{c[1]}")
        if diff.get("thresholds"):
            for k, ch in diff["thresholds"].items():
                lines.append(f"         ~ {k}: {ch['from']} → {ch['to']}")
        metrics = v.get("metrics", {})
        if metrics:
            lines.append(f"         metrics: WR {metrics.get('wr_before', '?')}% → {metrics.get('wr_after', '?')}% "
                        f"(test: {metrics.get('wr_test_before', '?')}% → {metrics.get('wr_test_after', '?')}%)")
        return "\n".join(lines)

    return line


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "status"

    if cmd == "status":
        data = _load_versions()
        versions = data["versions"]
        current = _read_current_params()
        print(f"Current: {len(current['suppressed_tickers'])} tickers, "
              f"{len(current['suppressed_combos'])} combos suppressed")
        if current["thresholds"]:
            print(f"Thresholds: WR≥{current['thresholds'].get('TICKER_WR_THRESHOLD', '?')}, "
                  f"min train={current['thresholds'].get('TICKER_MIN_SIGNALS_TRAIN', '?')}, "
                  f"split={current['thresholds'].get('TRAIN_RATIO', '?')}")
        print(f"\nVersions: {len(versions)} recorded")
        if versions:
            print("\nRecent:")
            for v in versions[-5:]:
                print(_fmt_version(v, verbose=True))

    elif cmd == "list":
        data = _load_versions()
        if not data["versions"]:
            print("No versions recorded yet. Run: python3 scripts/param_manager.py snapshot")
            return
        for v in data["versions"]:
            print(_fmt_version(v))

    elif cmd == "show" and len(args) >= 2:
        v = get_version(args[1])
        if not v:
            print(f"Version {args[1]} not found")
            return
        print(_fmt_version(v, verbose=True))
        print(f"\n  Tickers: {', '.join(v['params'].get('suppressed_tickers', [])) or '(none)'}")
        print(f"  Combos:  {', '.join(f'{c[0]}+{c[1]}' for c in v['params'].get('suppressed_combos', [])) or '(none)'}")

    elif cmd == "diff" and len(args) >= 3:
        v1 = get_version(args[1])
        v2 = get_version(args[2])
        if not v1 or not v2:
            print(f"Version not found: {args[1] if not v1 else args[2]}")
            return
        diff = _compute_diff(v1["params"], v2["params"])
        if not diff:
            print(f"{args[1]} and {args[2]} are identical")
            return
        print(f"Diff: {args[1]} → {args[2]}")
        if diff.get("tickers", {}).get("added"):
            print(f"  + tickers: {', '.join(diff['tickers']['added'])}")
        if diff.get("tickers", {}).get("removed"):
            print(f"  - tickers: {', '.join(diff['tickers']['removed'])}")
        if diff.get("combos", {}).get("added"):
            for c in diff["combos"]["added"]:
                print(f"  + combo: {c[0]}+{c[1]}")
        if diff.get("combos", {}).get("removed"):
            for c in diff["combos"]["removed"]:
                print(f"  - combo: {c[0]}+{c[1]}")
        if diff.get("thresholds"):
            for k, ch in diff["thresholds"].items():
                print(f"  ~ {k}: {ch['from']} → {ch['to']}")

    elif cmd == "rollback" and len(args) >= 2:
        vid = args[1]
        v = get_version(vid)
        if not v:
            print(f"Version {vid} not found")
            return
        print(f"Rolling back to {vid}:")
        print(_fmt_version(v, verbose=True))
        if rollback_to(vid):
            print(f"\nRestored signals.py to {vid} parameters")
        else:
            print("\nRollback failed")

    elif cmd == "snapshot":
        reason = " ".join(args[1:]) if len(args) > 1 else "Manual snapshot"
        entry = save_version(trigger="manual", reason=reason)
        print(f"Saved {entry['id']}")
        print(_fmt_version(entry, verbose=True))

    else:
        print("Usage:")
        print("  param_manager.py                     # current status")
        print("  param_manager.py list                # all versions")
        print("  param_manager.py show v3             # version details")
        print("  param_manager.py diff v2 v3          # compare versions")
        print("  param_manager.py rollback v3         # restore version")
        print("  param_manager.py snapshot [reason]   # save current state")


if __name__ == "__main__":
    main()
