import duckdb

DB_FILE = "trading.duckdb"

db = duckdb.connect(DB_FILE)

db.execute("""
CREATE TABLE IF NOT EXISTS candles (
    timestamp BIGINT,
    symbol VARCHAR,
    close DOUBLE
)
""")

def save_candle(timestamp, symbol, close):

    db.execute(
        """
        INSERT INTO candles
        VALUES (?, ?, ?)
        """,
        [
            int(timestamp),
            symbol,
            float(close)
        ]
    )

def count_candles():

    result = db.execute(
        """
        SELECT COUNT(*)
        FROM candles
        """
    ).fetchone()

    return result[0]
