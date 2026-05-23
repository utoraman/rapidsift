import yfinance as yf
import pandas as pd
from data.db import store_intraday, store_daily


def fetch_intraday(ticker: str, period: str = "1d", interval: str = "15m") -> pd.DataFrame:
    t = yf.Ticker(ticker)
    df = t.history(period=period, interval=interval)
    if not df.empty:
        store_intraday(ticker, df)
    return df


def fetch_daily(ticker: str, period: str = "3mo") -> pd.DataFrame:
    t = yf.Ticker(ticker)
    df = t.history(period=period, interval="1d")
    if not df.empty:
        store_daily(ticker, df)
    return df


def fetch_all(tickers: list[str]):
    all_tickers = list(set(tickers + ["SPY"]))
    for ticker in all_tickers:
        fetch_intraday(ticker)
        fetch_daily(ticker)


def get_current_price(ticker: str) -> float | None:
    t = yf.Ticker(ticker)
    info = t.fast_info
    return info.get("lastPrice") or info.get("last_price")
