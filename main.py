import yaml
import time
import logging
from pathlib import Path
from datetime import datetime
import pytz

from data.db import init_db
from data.fetcher import fetch_all
from signals.engine import evaluate_all, load_config
from alerts.notifier import notify_signals
from backtest.validator import rebuild_cache
from dashboard.app import run_dashboard
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def is_market_open(config: dict) -> bool:
    schedule = config["schedule"]
    if not schedule["market_hours_only"]:
        return True

    tz = pytz.timezone(schedule["timezone"])
    now = datetime.now(tz)

    if now.weekday() >= 5:
        return False

    market_open = datetime.strptime(schedule["market_open"], "%H:%M").time()
    market_close = datetime.strptime(schedule["market_close"], "%H:%M").time()

    return market_open <= now.time() <= market_close


def run_cycle(config: dict):
    logger.info("Starting monitoring cycle...")

    logger.info(f"Fetching data for {len(config['watchlist'])} tickers")
    fetch_all(config["watchlist"])

    logger.info("Evaluating signals...")
    signals = evaluate_all(config)

    if signals:
        logger.info(f"Found {len(signals)} signal(s)!")
        for sig in signals:
            logger.info(f"  {sig['ticker']} | {sig['direction'].upper()} | {sig['type']} | {sig['detail']}")
        notify_signals(config, signals)
    else:
        logger.info("No signals this cycle.")

    logger.info("Rebuilding validation cache...")
    rebuild_cache()
    logger.info("Cycle complete.")
    return signals


def main():
    config = load_config()
    init_db()

    dashboard_thread = threading.Thread(
        target=run_dashboard,
        kwargs={"host": config["dashboard"]["host"], "port": config["dashboard"]["port"]},
        daemon=True
    )
    dashboard_thread.start()
    logger.info(f"Dashboard running at http://{config['dashboard']['host']}:{config['dashboard']['port']}")

    interval = config["schedule"]["interval_minutes"] * 60

    # Run first cycle immediately
    run_cycle(config)

    while True:
        time.sleep(interval)
        if is_market_open(config):
            config = load_config()  # Reload config each cycle for hot changes
            run_cycle(config)
        else:
            logger.info("Market closed, skipping cycle.")


if __name__ == "__main__":
    main()
