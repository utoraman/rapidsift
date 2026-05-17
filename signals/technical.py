import pandas as pd
import ta


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return ta.momentum.RSIIndicator(df["close"], window=period).rsi()


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
    macd = ta.trend.MACD(df["close"], window_fast=fast, window_slow=slow, window_sign=signal)
    return macd.macd(), macd.macd_signal(), macd.macd_diff()


def compute_sma(df: pd.DataFrame, period: int) -> pd.Series:
    return ta.trend.SMAIndicator(df["close"], window=period).sma_indicator()


def check_rsi(df: pd.DataFrame, period: int = 14, oversold: float = 30, overbought: float = 70):
    rsi = compute_rsi(df, period)
    if rsi.empty:
        return None
    latest = rsi.iloc[-1]
    if latest <= oversold:
        return ("buy", f"RSI={latest:.1f} (oversold <{oversold})")
    elif latest >= overbought:
        return ("sell", f"RSI={latest:.1f} (overbought >{overbought})")
    return None


def check_macd_crossover(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line, signal_line, _ = compute_macd(df, fast, slow, signal)
    if len(macd_line) < 2:
        return None
    prev_diff = macd_line.iloc[-2] - signal_line.iloc[-2]
    curr_diff = macd_line.iloc[-1] - signal_line.iloc[-1]
    if prev_diff < 0 and curr_diff > 0:
        return ("buy", f"MACD bullish crossover (MACD={macd_line.iloc[-1]:.3f})")
    elif prev_diff > 0 and curr_diff < 0:
        return ("sell", f"MACD bearish crossover (MACD={macd_line.iloc[-1]:.3f})")
    return None


def check_ma_crossover(df: pd.DataFrame, fast_period: int = 20, slow_period: int = 50):
    if len(df) < slow_period + 1:
        return None
    fast_ma = compute_sma(df, fast_period)
    slow_ma = compute_sma(df, slow_period)
    prev_diff = fast_ma.iloc[-2] - slow_ma.iloc[-2]
    curr_diff = fast_ma.iloc[-1] - slow_ma.iloc[-1]
    if prev_diff < 0 and curr_diff > 0:
        return ("buy", f"SMA{fast_period} crossed above SMA{slow_period}")
    elif prev_diff > 0 and curr_diff < 0:
        return ("sell", f"SMA{fast_period} crossed below SMA{slow_period}")
    return None
