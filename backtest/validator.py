import pandas as pd
from data.db import get_connection


def validate_buy_signals(gain_targets: list[float] = [5.0, 10.0], lookahead_days: int = 14) -> pd.DataFrame:
    con = get_connection()

    signals = con.execute("""
        SELECT id, ticker, timestamp, signal_type, price_at_signal, details
        FROM signals_fired
        WHERE direction = 'buy'
        ORDER BY timestamp DESC
    """).fetchdf()

    if signals.empty:
        con.close()
        return pd.DataFrame()

    results = []
    for _, sig in signals.iterrows():
        future_prices = con.execute("""
            SELECT date, high
            FROM daily_prices
            WHERE ticker = ?
            AND date > ?::DATE
            AND date <= ?::DATE + INTERVAL '14 days'
            ORDER BY date
        """, [sig["ticker"], sig["timestamp"], sig["timestamp"]]).fetchdf()

        max_high = future_prices["high"].max() if not future_prices.empty else None

        row = {
            "signal_id": sig["id"],
            "ticker": sig["ticker"],
            "timestamp": sig["timestamp"],
            "signal_type": sig["signal_type"],
            "price_at_signal": sig["price_at_signal"],
            "details": sig["details"],
            "max_high_14d": max_high,
        }

        for target in gain_targets:
            target_price = sig["price_at_signal"] * (1 + target / 100)
            hit = False
            days_to_hit = None

            if not future_prices.empty:
                hit = max_high >= target_price
                if hit:
                    hit_row = future_prices[future_prices["high"] >= target_price].iloc[0]
                    sig_date = pd.Timestamp(sig["timestamp"]).date()
                    hit_date = pd.Timestamp(hit_row["date"]).date()
                    days_to_hit = (hit_date - sig_date).days

            suffix = str(int(target))
            row[f"hit_{suffix}pct"] = hit
            row[f"days_to_hit_{suffix}pct"] = days_to_hit

        results.append(row)

    con.close()
    return pd.DataFrame(results)


def signal_win_rates(gain_targets: list[float] = [5.0, 10.0], lookahead_days: int = 14) -> pd.DataFrame:
    df = validate_buy_signals(gain_targets, lookahead_days)
    if df.empty:
        return pd.DataFrame()

    import datetime
    cutoff = datetime.datetime.now() - datetime.timedelta(days=lookahead_days)
    matured = df[df["timestamp"] <= cutoff]

    if matured.empty:
        return pd.DataFrame()

    agg_dict = {"hit_5pct": ["count", "sum"]}
    for target in gain_targets:
        suffix = str(int(target))
        agg_dict[f"hit_{suffix}pct"] = ["sum"]

    grouped = matured.groupby(["ticker", "signal_type"]).agg(
        total_signals=("hit_5pct", "count"),
        **{f"wins_{int(t)}pct": (f"hit_{int(t)}pct", "sum") for t in gain_targets}
    ).reset_index()

    for target in gain_targets:
        suffix = str(int(target))
        grouped[f"win_rate_{suffix}pct"] = (grouped[f"wins_{suffix}pct"] / grouped["total_signals"] * 100).round(1)
        grouped[f"summary_{suffix}pct"] = grouped.apply(
            lambda r, s=suffix, t=target: f"{int(r[f'wins_{s}pct'])}/{int(r['total_signals'])} hit +{t}% ({r[f'win_rate_{s}pct']}%)", axis=1
        )

    grouped = grouped.sort_values(["win_rate_5pct", "total_signals"], ascending=[False, False])
    return grouped


def signal_detail_report(gain_targets: list[float] = [5.0, 10.0], lookahead_days: int = 14) -> pd.DataFrame:
    df = validate_buy_signals(gain_targets, lookahead_days)
    if df.empty:
        return pd.DataFrame()

    import datetime
    cutoff = datetime.datetime.now() - datetime.timedelta(days=lookahead_days)
    df["matured"] = df["timestamp"] <= cutoff

    return df.sort_values("timestamp", ascending=False)
