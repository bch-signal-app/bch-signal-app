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
# Table strategies
# =========================
db.execute("""
CREATE TABLE IF NOT EXISTS strategies (
    id BIGINT PRIMARY KEY,
    name VARCHAR,

    ema_fast INTEGER,
    ema_slow INTEGER,
    ema_trend INTEGER,

    rsi_period INTEGER,
    rsi_min INTEGER,

    stop_loss DOUBLE,
    take_profit DOUBLE,

    initial_capital DOUBLE
)
""")

def create_strategy(
    name,
    ema_fast,
    ema_slow,
    ema_trend,
    rsi_period,
    rsi_min,
    stop_loss,
    take_profit,
    initial_capital
):
    next_id = db.execute(
        """
        SELECT COALESCE(MAX(id), 0) + 1
        FROM strategies
        """
    ).fetchone()[0]

    db.execute(
        """
        INSERT INTO strategies
        (
            id,
            name,
            ema_fast,
            ema_slow,
            ema_trend,
            rsi_period,
            rsi_min,
            stop_loss,
            take_profit,
            initial_capital
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            next_id,
            name,
            ema_fast,
            ema_slow,
            ema_trend,
            rsi_period,
            rsi_min,
            stop_loss,
            take_profit,
            initial_capital
        ]
    )

def get_strategies():

    return db.execute(
        """
        SELECT *
        FROM strategies
        ORDER BY id
        """
    ).fetchall()

def get_strategy(strategy_id):

    return db.execute(
        """
        SELECT *
        FROM strategies
        WHERE id = ?
        """,
        [strategy_id]
    ).fetchone()


def create_default_strategy():

    exists = db.execute(
        """
        SELECT COUNT(*)
        FROM strategies
        """
    ).fetchone()[0]

    if exists == 0:

        create_strategy(
            "Default",
            9,
            20,
            50,
            14,
            55,
            1.0,
            2.0,
            1000
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

def clone_strategy(strategy_id):

    row = get_strategy(strategy_id)

    if not row:
        return None

    cursor = db.execute(
        """
        INSERT INTO strategies (
            name,
            ema_fast,
            ema_slow,
            ema_trend,
            rsi_period,
            rsi_min,
            stop_loss,
            take_profit,
            initial_capital
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row[1] + " Copy",
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9]
        )
    )

    db.commit()

    return cursor.lastrowid

def delete_strategy(strategy_id):

    db.execute(
        """
        DELETE FROM strategies
        WHERE id = ?
        """,
        (strategy_id,)
    )

    db.commit()

    