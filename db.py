import duckdb

DB_FILE = "trading.duckdb"

db = duckdb.connect(DB_FILE)

# =========================
# Table candles
# =========================
db.execute("""
CREATE TABLE IF NOT EXISTS candles (
    timestamp BIGINT,
    symbol VARCHAR,
    close DOUBLE,
    PRIMARY KEY(timestamp, symbol)
)
""")

# =========================
# Sauvegarde bougie
# =========================
def save_candle(timestamp, symbol, close):

    db.execute(
        """
        INSERT OR IGNORE INTO candles
        VALUES (?, ?, ?)
        """,
        [
            int(timestamp),
            symbol,
            float(close)
        ]
    )

# =========================
# Nombre de bougies
# =========================
def count_candles():

    result = db.execute(
        """
        SELECT COUNT(*)
        FROM candles
        """
    ).fetchone()

    return result[0]

# =========================
# Dernières bougies
# =========================
def get_last_candles(limit=100):

    rows = db.execute(
        """
        SELECT
            timestamp,
            symbol,
            close
        FROM candles
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        [limit]
    ).fetchall()

    return rows
