import pandas as pd


def check_price_thresholds(ticker: str, current_price: float, rules: list[dict]):
    signals = []
    for rule in rules:
        if rule.get("ticker") != ticker:
            continue
        direction = rule["direction"]
        target = rule["price"]
        if direction == "below" and current_price <= target:
            signals.append(("buy", f"Price ${current_price:.2f} crossed below ${target:.2f}"))
        elif direction == "above" and current_price >= target:
            signals.append(("sell", f"Price ${current_price:.2f} crossed above ${target:.2f}"))
    return signals


def check_percent_change(df: pd.DataFrame, daily_threshold: float = 3.0, intraday_threshold: float = 2.0):
    if df.empty or len(df) < 2:
        return None

    latest_close = df["close"].iloc[-1]
    prev_close = df["close"].iloc[0]

    pct_change = ((latest_close - prev_close) / prev_close) * 100

    if abs(pct_change) >= daily_threshold:
        direction = "sell" if pct_change > 0 else "buy"
        return (direction, f"Daily move {pct_change:+.2f}% (threshold: {daily_threshold}%)")
    return None
