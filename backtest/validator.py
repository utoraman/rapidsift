import pandas as pd
from data.db import get_connection


def compute_signal_outcomes(lookback_days: list[int] = [1, 3, 5, 10]):
    con = get_connection()

    signals = con.execute("""
        SELECT id, ticker, timestamp, direction, price_at_signal
        FROM signals_fired
        ORDER BY timestamp
    """).fetchdf()

    if signals.empty:
        con.close()
        return pd.DataFrame()

    outcomes = []
    for _, sig in signals.iterrows():
        for days in lookback_days:
            future_price = con.execute("""
                SELECT close FROM daily_prices
                WHERE ticker = ? AND date > ?::DATE
                ORDER BY date
                LIMIT 1 OFFSET ?
            """, [sig["ticker"], sig["timestamp"], days - 1]).fetchone()

            if future_price:
                price_at_check = future_price[0]
                return_pct = ((price_at_check - sig["price_at_signal"]) / sig["price_at_signal"]) * 100
                if sig["direction"] == "sell":
                    return_pct = -return_pct

                outcomes.append({
                    "signal_id": sig["id"],
                    "ticker": sig["ticker"],
                    "signal_timestamp": sig["timestamp"],
                    "check_timestamp": None,
                    "days_after": days,
                    "price_at_check": price_at_check,
                    "return_pct": return_pct
                })

    con.close()

    if not outcomes:
        return pd.DataFrame()

    return pd.DataFrame(outcomes)


def signal_reliability_report() -> pd.DataFrame:
    outcomes = compute_signal_outcomes()
    if outcomes.empty:
        return pd.DataFrame()

    con = get_connection()
    signals = con.execute("""
        SELECT id, signal_type, direction FROM signals_fired
    """).fetchdf()
    con.close()

    merged = outcomes.merge(signals, left_on="signal_id", right_on="id")

    report = merged.groupby(["signal_type", "direction", "days_after"]).agg(
        count=("return_pct", "count"),
        avg_return=("return_pct", "mean"),
        win_rate=("return_pct", lambda x: (x > 0).sum() / len(x) * 100),
        max_gain=("return_pct", "max"),
        max_loss=("return_pct", "min"),
    ).reset_index()

    return report
