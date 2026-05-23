import yaml
from pathlib import Path
from data.db import get_prices, get_signals, log_signal, compute_adjusted_returns
from data.fetcher import get_current_price
from signals.price import check_price_thresholds, check_percent_change
from signals.technical import check_rsi, check_macd_crossover, check_ma_crossover
from signals.volume import check_volume_spike
from signals.adjusted import check_adjusted_drop, check_adjusted_surge

COOLDOWN_DAYS = 5


def load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _is_on_cooldown(ticker: str, signal_type: str, recent_signals) -> bool:
    if recent_signals.empty:
        return False
    match = recent_signals[
        (recent_signals["ticker"] == ticker) & (recent_signals["signal_type"] == signal_type)
    ]
    return not match.empty


def evaluate_ticker(ticker: str, config: dict, recent_signals=None) -> list[dict]:
    signals_found = []
    df = get_prices(ticker, days=60)
    if df.empty:
        return signals_found

    current_price = df["close"].iloc[-1] if not df.empty else None
    sig_config = config["signals"]

    if recent_signals is None:
        recent_signals = get_signals(ticker, days=COOLDOWN_DAYS)

    if sig_config["price_threshold"]["enabled"]:
        rules = sig_config["price_threshold"].get("rules") or []
        if current_price and rules:
            for direction, detail in check_price_thresholds(ticker, current_price, rules):
                signals_found.append({"ticker": ticker, "type": "price_threshold",
                                      "direction": direction, "detail": detail})

    if sig_config["percent_change"]["enabled"]:
        result = check_percent_change(
            df,
            daily_threshold=sig_config["percent_change"]["daily_threshold"]
        )
        if result and not _is_on_cooldown(ticker, "percent_change", recent_signals):
            signals_found.append({"ticker": ticker, "type": "percent_change",
                                  "direction": result[0], "detail": result[1]})

    if sig_config["technical"]["enabled"]:
        tc = sig_config["technical"]
        rsi_result = check_rsi(df, tc["rsi"]["period"], tc["rsi"]["oversold"], tc["rsi"]["overbought"])
        if rsi_result and not _is_on_cooldown(ticker, "rsi", recent_signals):
            signals_found.append({"ticker": ticker, "type": "rsi",
                                  "direction": rsi_result[0], "detail": rsi_result[1]})

        macd_result = check_macd_crossover(df, tc["macd"]["fast"], tc["macd"]["slow"], tc["macd"]["signal"])
        if macd_result and not _is_on_cooldown(ticker, "macd", recent_signals):
            signals_found.append({"ticker": ticker, "type": "macd",
                                  "direction": macd_result[0], "detail": macd_result[1]})

        ma_result = check_ma_crossover(df, tc["moving_average_crossover"]["fast_period"],
                                       tc["moving_average_crossover"]["slow_period"])
        if ma_result and not _is_on_cooldown(ticker, "ma_crossover", recent_signals):
            signals_found.append({"ticker": ticker, "type": "ma_crossover",
                                  "direction": ma_result[0], "detail": ma_result[1]})

    if sig_config["volume"]["enabled"]:
        vol_result = check_volume_spike(df, sig_config["volume"]["spike_multiplier"])
        if vol_result and not _is_on_cooldown(ticker, "volume_spike", recent_signals):
            signals_found.append({"ticker": ticker, "type": "volume_spike",
                                  "direction": vol_result[0], "detail": vol_result[1]})

    if sig_config["adjusted_return"]["enabled"]:
        adj_cfg = sig_config["adjusted_return"]
        drop_result = check_adjusted_drop(ticker, threshold=adj_cfg["drop_threshold"])
        if drop_result and not _is_on_cooldown(ticker, "adjusted_drop", recent_signals):
            signals_found.append({"ticker": ticker, "type": "adjusted_drop",
                                  "direction": drop_result[0], "detail": drop_result[1]})
        surge_result = check_adjusted_surge(ticker, threshold=adj_cfg["surge_threshold"])
        if surge_result and not _is_on_cooldown(ticker, "adjusted_surge", recent_signals):
            signals_found.append({"ticker": ticker, "type": "adjusted_surge",
                                  "direction": surge_result[0], "detail": surge_result[1]})

    for sig in signals_found:
        log_signal(ticker, sig["type"], sig["direction"], current_price or 0, sig["detail"])

    # Confluence: if 2+ buy signals fire on the same ticker, log a composite signal
    buy_signals = [s for s in signals_found if s["direction"] == "buy"]
    if len(buy_signals) >= 2:
        components = ", ".join(s["type"] for s in buy_signals)
        detail = f"Confluence ({len(buy_signals)} signals): {components}"
        log_signal(ticker, "confluence", "buy", current_price or 0, detail)
        signals_found.append({"ticker": ticker, "type": "confluence",
                              "direction": "buy", "detail": detail})

    return signals_found


def evaluate_all(config: dict = None) -> list[dict]:
    if config is None:
        config = load_config()
    compute_adjusted_returns()
    all_signals = []
    for ticker in config["watchlist"]:
        signals = evaluate_ticker(ticker, config)
        all_signals.extend(signals)
    return all_signals
