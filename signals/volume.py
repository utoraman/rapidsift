import pandas as pd


def check_volume_spike(df: pd.DataFrame, spike_multiplier: float = 2.0):
    if len(df) < 21:
        return None

    avg_volume = df["volume"].iloc[-21:-1].mean()
    current_volume = df["volume"].iloc[-1]

    if avg_volume == 0:
        return None

    ratio = current_volume / avg_volume
    if ratio >= spike_multiplier:
        # Direction based on price action: down day + volume spike = capitulation (buy)
        close = df["close"].iloc[-1]
        open_price = df["open"].iloc[-1] if "open" in df.columns else df["close"].iloc[-2]
        direction = "buy" if close < open_price else "sell"
        return (direction, f"Volume spike: {ratio:.1f}x average ({current_volume:,.0f} vs avg {avg_volume:,.0f})")
    return None
