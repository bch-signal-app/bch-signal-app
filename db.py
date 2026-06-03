import duckdb

DB_FILE = "trading_v2.duckdb"

db = duckdb.connect(DB_FILE)

# =========================
# Table candles
# =========================
db.execute("""
CREATE TABLE IF NOT EXISTS candles (
    timestamp BIGINT,
    symbol VARCHAR,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    PRIMARY KEY(timestamp, symbol)
)
""")

# =========================
# Sauvegarde bougie
# =========================
def save_candle(
    timestamp,
    symbol,
    open_price,
    high_price,
    low_price,
    close_price,
    volume
):

    db.execute(
        """
        INSERT OR REPLACE INTO candles
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            int(timestamp),
            symbol,
            float(open_price),
            float(high_price),
            float(low_price),
            float(close_price),
            float(volume)
        ]
    )


    db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key VARCHAR PRIMARY KEY,
            value VARCHAR
        )    
    """)
    
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
            open,
            high,
            low,
            close,
            volume
        FROM candles
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        [limit]
    ).fetchall()

    print("ROWS =", len(rows))
    return rows


# =========================
# settings
# =========================
def set_setting(key, value):

    db.execute(
        """
        INSERT OR REPLACE INTO settings
        VALUES (?, ?)
        """,
        [key, str(value)]
    )


def get_setting(key, default_value):

    result = db.execute(
        """
        SELECT value
        FROM settings
        WHERE key = ?
        """,
        [key]
    ).fetchone()

    if result:
        return result[0]

    return default_value

