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


def check_percent_change(df: pd.DataFrame, daily_threshold: float = 3.0, intraday_threshold: float = 2.0,
                         sigma_threshold: float = 2.0):
    """Fire only when the daily move exceeds sigma_threshold standard deviations of recent returns.
    Falls back to the fixed daily_threshold if insufficient history for volatility calc."""
    if df.empty or len(df) < 2:
        return None

    latest_close = df["close"].iloc[-1]
    prev_close = df["close"].iloc[-2] if len(df) >= 2 else df["close"].iloc[0]

    pct_change = ((latest_close - prev_close) / prev_close) * 100

    # Volatility-normalized: use trailing 20-day std dev if enough data
    if len(df) >= 22:
        returns = df["close"].pct_change().dropna().iloc[-20:] * 100
        std = returns.std()
        if std > 0:
            z_score = pct_change / std
            if abs(z_score) >= sigma_threshold:
                direction = "sell" if pct_change > 0 else "buy"
                return (direction, f"Daily move {pct_change:+.2f}% ({z_score:+.1f}σ, std={std:.2f}%)")
            return None

    # Fallback to fixed threshold
    if abs(pct_change) >= daily_threshold:
        direction = "sell" if pct_change > 0 else "buy"
        return (direction, f"Daily move {pct_change:+.2f}% (threshold: {daily_threshold}%)")
    return None
