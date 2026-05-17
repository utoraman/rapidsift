import pandas as pd
from data.db import get_adjusted_returns


def check_adjusted_drop(ticker: str, threshold: float = -5.0):
    df = get_adjusted_returns(ticker, days=5)
    if df.empty:
        return None

    latest = df.iloc[-1]
    adj = latest["adjusted_return_pct"]

    if adj is not None and adj <= threshold:
        return ("buy", f"Market-adjusted drop {adj:+.2f}% "
                f"(stock {latest['day_return_pct']:+.2f}%, market {latest['market_return_pct']:+.2f}%)")
    return None


def check_adjusted_surge(ticker: str, threshold: float = 5.0):
    df = get_adjusted_returns(ticker, days=5)
    if df.empty:
        return None

    latest = df.iloc[-1]
    adj = latest["adjusted_return_pct"]

    if adj is not None and adj >= threshold:
        return ("sell", f"Market-adjusted surge +{adj:.2f}% "
                f"(stock {latest['day_return_pct']:+.2f}%, market {latest['market_return_pct']:+.2f}%)")
    return None
