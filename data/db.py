import duckdb
from pathlib import Path
import pandas as pd

DB_PATH = Path(__file__).parent.parent / "market.duckdb"


def get_connection():
    return duckdb.connect(str(DB_PATH))


def init_db():
    con = get_connection()
    con.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            ticker VARCHAR,
            timestamp TIMESTAMP,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            PRIMARY KEY (ticker, timestamp)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS daily_prices (
            ticker VARCHAR,
            date DATE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            adj_close DOUBLE,
            volume BIGINT,
            PRIMARY KEY (ticker, date)
        )
    """)
    con.execute("CREATE SEQUENCE IF NOT EXISTS signals_fired_seq START 1")
    con.execute("""
        CREATE TABLE IF NOT EXISTS signals_fired (
            id INTEGER DEFAULT nextval('signals_fired_seq'),
            ticker VARCHAR,
            timestamp TIMESTAMP DEFAULT current_timestamp,
            signal_type VARCHAR,
            direction VARCHAR,
            price_at_signal DOUBLE,
            details VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS signal_outcomes (
            signal_id INTEGER,
            ticker VARCHAR,
            signal_timestamp TIMESTAMP,
            check_timestamp TIMESTAMP,
            days_after INTEGER,
            price_at_check DOUBLE,
            return_pct DOUBLE
        )
    """)
    con.close()


def store_intraday(ticker: str, df: pd.DataFrame):
    if df.empty:
        return
    con = get_connection()
    df = df.reset_index()
    df["ticker"] = ticker
    df = df.rename(columns={"Datetime": "timestamp", "Open": "open", "High": "high",
                            "Low": "low", "Close": "close", "Volume": "volume"})
    cols = ["ticker", "timestamp", "open", "high", "low", "close", "volume"]
    df = df[cols]
    con.execute("""
        INSERT INTO prices
        SELECT * FROM df
        ON CONFLICT (ticker, timestamp) DO UPDATE SET
            open = excluded.open, high = excluded.high,
            low = excluded.low, close = excluded.close, volume = excluded.volume
    """)
    con.close()


def store_daily(ticker: str, df: pd.DataFrame):
    if df.empty:
        return
    con = get_connection()
    df = df.reset_index()
    df["ticker"] = ticker
    df = df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                            "Low": "low", "Close": "close", "Adj Close": "adj_close",
                            "Volume": "volume"})
    cols = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]
    available_cols = [c for c in cols if c in df.columns]
    df = df[available_cols]
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]
    con.execute("""
        INSERT INTO daily_prices
        SELECT * FROM df
        ON CONFLICT (ticker, date) DO UPDATE SET
            open = excluded.open, high = excluded.high,
            low = excluded.low, close = excluded.close,
            adj_close = excluded.adj_close, volume = excluded.volume
    """)
    con.close()


def log_signal(ticker: str, signal_type: str, direction: str, price: float, details: str = ""):
    con = get_connection()
    con.execute("""
        INSERT INTO signals_fired (ticker, signal_type, direction, price_at_signal, details)
        VALUES (?, ?, ?, ?, ?)
    """, [ticker, signal_type, direction, price, details])
    con.close()


def get_prices(ticker: str, days: int = 60) -> pd.DataFrame:
    con = get_connection()
    df = con.execute(f"""
        SELECT * FROM daily_prices
        WHERE ticker = ?
        AND date >= current_date - INTERVAL '{days} days'
        ORDER BY date
    """, [ticker]).fetchdf()
    con.close()
    return df


def get_intraday_prices(ticker: str, hours: int = 24) -> pd.DataFrame:
    con = get_connection()
    df = con.execute(f"""
        SELECT * FROM prices
        WHERE ticker = ?
        AND timestamp >= current_timestamp - INTERVAL '{hours} hours'
        ORDER BY timestamp
    """, [ticker]).fetchdf()
    con.close()
    return df


def get_signals(ticker: str = None, days: int = 30) -> pd.DataFrame:
    con = get_connection()
    query = f"""
        SELECT * FROM signals_fired
        WHERE timestamp >= current_timestamp - INTERVAL '{days} days'
    """
    params = []
    if ticker:
        query += " AND ticker = ?"
        params.append(ticker)
    query += " ORDER BY timestamp DESC"
    df = con.execute(query, params).fetchdf()
    con.close()
    return df
