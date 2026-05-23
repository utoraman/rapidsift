import pandas as pd
from data.db import get_connection


def _ensure_cache_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS validation_cache (
            signal_id INTEGER,
            ticker VARCHAR,
            timestamp TIMESTAMP,
            signal_type VARCHAR,
            price_at_signal DOUBLE,
            details VARCHAR,
            entry_price DOUBLE,
            entry_date DATE,
            max_high_14d DOUBLE,
            max_drawdown_pct DOUBLE,
            hit_5pct BOOLEAN,
            days_to_hit_5pct INTEGER,
            hit_10pct BOOLEAN,
            days_to_hit_10pct INTEGER,
            matured BOOLEAN
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS win_rate_cache (
            ticker VARCHAR,
            signal_type VARCHAR,
            total_signals INTEGER,
            wins_5pct INTEGER,
            win_rate_5pct DOUBLE,
            wins_10pct INTEGER,
            win_rate_10pct DOUBLE,
            summary_5pct VARCHAR,
            summary_10pct VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS baseline_cache (
            total_entries INTEGER,
            hits_5pct INTEGER,
            win_rate_5pct DOUBLE,
            hits_10pct INTEGER,
            win_rate_10pct DOUBLE
        )
    """)


def rebuild_cache(lookahead_days: int = 14):
    """Rebuild all validation caches using pure SQL. Called after backfill or data refresh."""
    con = get_connection()
    _ensure_cache_tables(con)
    con.execute("DELETE FROM validation_cache")
    con.execute("DELETE FROM win_rate_cache")
    con.execute("DELETE FROM baseline_cache")

    # Step 1: Validate all buy signals in one SQL pass
    con.execute(f"""
        INSERT INTO validation_cache
        WITH buy_signals AS (
            SELECT id, ticker, timestamp, signal_type, price_at_signal, details
            FROM signals_fired
            WHERE direction = 'buy'
        ),
        with_entry AS (
            SELECT
                s.*,
                e.date as entry_date,
                e.open as entry_price
            FROM buy_signals s
            LEFT JOIN LATERAL (
                SELECT date, open FROM daily_prices dp
                WHERE dp.ticker = s.ticker AND dp.date > s.timestamp::DATE
                ORDER BY dp.date LIMIT 1
            ) e ON true
        ),
        with_future AS (
            SELECT
                w.*,
                f.max_high,
                f.min_low,
                f.first_hit_5pct_date,
                f.first_hit_10pct_date
            FROM with_entry w
            LEFT JOIN LATERAL (
                SELECT
                    MAX(dp.high) as max_high,
                    MIN(dp.low) as min_low,
                    MIN(CASE WHEN dp.high >= w.entry_price * 1.05 THEN dp.date END) as first_hit_5pct_date,
                    MIN(CASE WHEN dp.high >= w.entry_price * 1.10 THEN dp.date END) as first_hit_10pct_date
                FROM daily_prices dp
                WHERE dp.ticker = w.ticker
                  AND dp.date >= w.entry_date
                  AND dp.date <= w.entry_date + INTERVAL '{lookahead_days} days'
            ) f ON true
        )
        SELECT
            id as signal_id,
            ticker,
            timestamp,
            signal_type,
            price_at_signal,
            details,
            entry_price,
            entry_date,
            max_high as max_high_14d,
            ROUND(((min_low - entry_price) / entry_price) * 100, 2) as max_drawdown_pct,
            (first_hit_5pct_date IS NOT NULL) as hit_5pct,
            (first_hit_5pct_date - entry_date)::INTEGER as days_to_hit_5pct,
            (first_hit_10pct_date IS NOT NULL) as hit_10pct,
            (first_hit_10pct_date - entry_date)::INTEGER as days_to_hit_10pct,
            (timestamp <= current_timestamp - INTERVAL '{lookahead_days} days') as matured
        FROM with_future
        WHERE entry_price IS NOT NULL
    """)

    # Step 2: Compute win rates per ticker + signal_type
    con.execute("""
        INSERT INTO win_rate_cache
        SELECT
            ticker,
            signal_type,
            COUNT(*) as total_signals,
            SUM(CASE WHEN hit_5pct THEN 1 ELSE 0 END) as wins_5pct,
            ROUND(SUM(CASE WHEN hit_5pct THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as win_rate_5pct,
            SUM(CASE WHEN hit_10pct THEN 1 ELSE 0 END) as wins_10pct,
            ROUND(SUM(CASE WHEN hit_10pct THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as win_rate_10pct,
            CONCAT(SUM(CASE WHEN hit_5pct THEN 1 ELSE 0 END), '/', COUNT(*),
                   ' hit +5.0% (', ROUND(SUM(CASE WHEN hit_5pct THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1), '%)') as summary_5pct,
            CONCAT(SUM(CASE WHEN hit_10pct THEN 1 ELSE 0 END), '/', COUNT(*),
                   ' hit +10.0% (', ROUND(SUM(CASE WHEN hit_10pct THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1), '%)') as summary_10pct
        FROM validation_cache
        WHERE matured = true
        GROUP BY ticker, signal_type
        ORDER BY win_rate_5pct DESC, total_signals DESC
    """)

    # Step 3: Compute random baseline in SQL
    con.execute(f"""
        INSERT INTO baseline_cache
        WITH entries AS (
            SELECT
                dp.ticker,
                dp.date as entry_date,
                dp.open as entry_price
            FROM daily_prices dp
            WHERE dp.ticker != 'SPY'
              AND dp.open > 0
              AND dp.date <= current_date - INTERVAL '{lookahead_days} days'
        ),
        with_future AS (
            SELECT
                e.ticker,
                e.entry_date,
                e.entry_price,
                MAX(f.high) as max_high
            FROM entries e
            JOIN daily_prices f ON f.ticker = e.ticker
                AND f.date > e.entry_date
                AND f.date <= e.entry_date + INTERVAL '{lookahead_days} days'
            GROUP BY e.ticker, e.entry_date, e.entry_price
        )
        SELECT
            COUNT(*) as total_entries,
            SUM(CASE WHEN max_high >= entry_price * 1.05 THEN 1 ELSE 0 END) as hits_5pct,
            ROUND(SUM(CASE WHEN max_high >= entry_price * 1.05 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as win_rate_5pct,
            SUM(CASE WHEN max_high >= entry_price * 1.10 THEN 1 ELSE 0 END) as hits_10pct,
            ROUND(SUM(CASE WHEN max_high >= entry_price * 1.10 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as win_rate_10pct
        FROM with_future
    """)

    counts = con.execute("SELECT COUNT(*) FROM validation_cache").fetchone()[0]
    wr_counts = con.execute("SELECT COUNT(*) FROM win_rate_cache").fetchone()[0]
    con.close()
    return counts, wr_counts


# --- Dashboard-facing functions: read from cache ---

def validate_buy_signals(gain_targets: list[float] = [5.0, 10.0], lookahead_days: int = 14) -> pd.DataFrame:
    con = get_connection()
    _ensure_cache_tables(con)
    df = con.execute("SELECT * FROM validation_cache ORDER BY timestamp DESC").fetchdf()
    con.close()
    return df


def signal_win_rates(gain_targets: list[float] = [5.0, 10.0], lookahead_days: int = 14) -> pd.DataFrame:
    con = get_connection()
    _ensure_cache_tables(con)
    df = con.execute("SELECT * FROM win_rate_cache ORDER BY win_rate_5pct DESC, total_signals DESC").fetchdf()
    con.close()
    return df


def signal_detail_report(gain_targets: list[float] = [5.0, 10.0], lookahead_days: int = 14) -> pd.DataFrame:
    con = get_connection()
    _ensure_cache_tables(con)
    df = con.execute("SELECT * FROM validation_cache ORDER BY timestamp DESC").fetchdf()
    con.close()
    return df


def random_baseline(gain_targets: list[float] = [5.0, 10.0], lookahead_days: int = 14) -> dict:
    con = get_connection()
    _ensure_cache_tables(con)
    row = con.execute("SELECT * FROM baseline_cache LIMIT 1").fetchdf()
    con.close()
    if row.empty:
        return {}
    return row.iloc[0].to_dict()
