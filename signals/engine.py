import yaml
from pathlib import Path
from data.db import get_prices, log_signal, compute_adjusted_returns
from data.fetcher import get_current_price
from signals.price import check_price_thresholds, check_percent_change
from signals.technical import check_rsi, check_macd_crossover, check_ma_crossover
from signals.volume import check_volume_spike
from signals.adjusted import check_adjusted_drop, check_adjusted_surge


def load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def evaluate_ticker(ticker: str, config: dict) -> list[dict]:
    signals_found = []
    df = get_prices(ticker, days=60)
    if df.empty:
        return signals_found

    current_price = df["close"].iloc[-1] if not df.empty else None
    sig_config = config["signals"]

    if sig_config["price_threshold"]["enabled"]:
        rules = sig_config["price_threshold"].get("rules") or []
        if current_price and rules:
            for direction, detail in check_price_thresholds(ticker, current_price, rules):
                signals_found.append({"ticker": ticker, "type": "price_threshold",
                                      "direction": direction, "detail": detail})

    if sig_config["percent_change"]["enabled"]:
        result = check_percent_change(
            df.tail(2),
            daily_threshold=sig_config["percent_change"]["daily_threshold"]
        )
        if result:
            signals_found.append({"ticker": ticker, "type": "percent_change",
                                  "direction": result[0], "detail": result[1]})

    if sig_config["technical"]["enabled"]:
        tc = sig_config["technical"]
        rsi_result = check_rsi(df, tc["rsi"]["period"], tc["rsi"]["oversold"], tc["rsi"]["overbought"])
        if rsi_result:
            signals_found.append({"ticker": ticker, "type": "rsi",
                                  "direction": rsi_result[0], "detail": rsi_result[1]})

        macd_result = check_macd_crossover(df, tc["macd"]["fast"], tc["macd"]["slow"], tc["macd"]["signal"])
        if macd_result:
            signals_found.append({"ticker": ticker, "type": "macd",
                                  "direction": macd_result[0], "detail": macd_result[1]})

        ma_result = check_ma_crossover(df, tc["moving_average_crossover"]["fast_period"],
                                       tc["moving_average_crossover"]["slow_period"])
        if ma_result:
            signals_found.append({"ticker": ticker, "type": "ma_crossover",
                                  "direction": ma_result[0], "detail": ma_result[1]})

    if sig_config["volume"]["enabled"]:
        vol_result = check_volume_spike(df, sig_config["volume"]["spike_multiplier"])
        if vol_result:
            signals_found.append({"ticker": ticker, "type": "volume_spike",
                                  "direction": vol_result[0], "detail": vol_result[1]})

    if sig_config["adjusted_return"]["enabled"]:
        adj_cfg = sig_config["adjusted_return"]
        drop_result = check_adjusted_drop(ticker, threshold=adj_cfg["drop_threshold"])
        if drop_result:
            signals_found.append({"ticker": ticker, "type": "adjusted_drop",
                                  "direction": drop_result[0], "detail": drop_result[1]})
        surge_result = check_adjusted_surge(ticker, threshold=adj_cfg["surge_threshold"])
        if surge_result:
            signals_found.append({"ticker": ticker, "type": "adjusted_surge",
                                  "direction": surge_result[0], "detail": surge_result[1]})

    for sig in signals_found:
        log_signal(ticker, sig["type"], sig["direction"], current_price or 0, sig["detail"])

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
