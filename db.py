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
# Table settings
# =========================
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

# =========================
# Table candles_multi (4h, 1d, ...)
# =========================
db.execute("""
CREATE TABLE IF NOT EXISTS candles_multi (
    timestamp BIGINT,
    symbol VARCHAR,
    timeframe VARCHAR,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    PRIMARY KEY(timestamp, symbol, timeframe)
)
""")

# =========================
# Migration strategies v2 :
# timeframe, stops ATR, sizing a risque fixe, filtre de regime.
# Valeurs par defaut = comportement historique (SL/TP en %, all-in,
# pas de regime) : les strategies existantes ne changent pas.
# =========================
strategy_cols = {
    row[1]
    for row in db.execute("PRAGMA table_info(strategies)").fetchall()
}

for _col, _typ, _default in [
    ("timeframe", "VARCHAR", "'1hour'"),
    ("sl_mode", "VARCHAR", "'percent'"),
    ("atr_period", "INTEGER", "14"),
    ("sl_atr", "DOUBLE", "2.0"),
    ("tp_atr", "DOUBLE", "4.0"),
    ("risk_pct", "DOUBLE", "0"),
    ("regime_ema", "INTEGER", "0")
]:
    if _col not in strategy_cols:
        db.execute(
            f"ALTER TABLE strategies ADD COLUMN {_col} {_typ} DEFAULT {_default}"
        )

# Passage unique au timeframe 4h par defaut : les strategies creees
# avant l'existence du champ etaient implicitement en 1h
_migrated = db.execute(
    "SELECT value FROM settings WHERE key = 'strategy_tf_migrated'"
).fetchone()

if not _migrated:
    db.execute(
        "UPDATE strategies SET timeframe = '4hour' WHERE timeframe = '1hour'"
    )
    db.execute(
        "UPDATE settings SET value = '4hour' "
        "WHERE key = 'timeframe' AND value = '1hour'"
    )
    db.execute(
        "INSERT OR REPLACE INTO settings VALUES ('strategy_tf_migrated', '1')"
    )

db.commit()


# =========================
# Sauvegarde bougie
# =========================
def save_candle(timestamp, symbol, open_price, high_price, low_price, close_price, volume):
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


def create_strategy(name, ema_fast, ema_slow, ema_trend, rsi_period, rsi_min,
                    stop_loss, take_profit, initial_capital,
                    timeframe="4hour", sl_mode="percent", atr_period=14,
                    sl_atr=2.0, tp_atr=4.0, risk_pct=0.0, regime_ema=0):
    next_id = db.execute(
        """
        SELECT COALESCE(MAX(id), 0) + 1
        FROM strategies
        """
    ).fetchone()[0]

    db.execute(
        """
        INSERT INTO strategies
        (id, name, ema_fast, ema_slow, ema_trend, rsi_period, rsi_min,
         stop_loss, take_profit, initial_capital, timeframe, sl_mode,
         atr_period, sl_atr, tp_atr, risk_pct, regime_ema)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [next_id, name, ema_fast, ema_slow, ema_trend, rsi_period, rsi_min,
         stop_loss, take_profit, initial_capital, timeframe, sl_mode,
         atr_period, sl_atr, tp_atr, risk_pct, regime_ema]
    )

    db.commit()
    return next_id


def get_strategies():
    return db.execute(
        """
        SELECT * FROM strategies ORDER BY id
        """
    ).fetchall()


def get_strategy(strategy_id):
    return db.execute(
        """
        SELECT * FROM strategies WHERE id = ?
        """,
        [strategy_id]
    ).fetchone()


def create_default_strategy():
    exists = db.execute(
        """
        SELECT COUNT(*) FROM strategies
        """
    ).fetchone()[0]

    if exists == 0:
        create_strategy("Default", 9, 20, 50, 14, 55, 1.0, 2.0, 1000)


# =========================
# Nombre de bougies
# =========================
def count_candles(symbol=None):
    if symbol:
        result = db.execute(
            "SELECT COUNT(*) FROM candles WHERE symbol = ?",
            [symbol]
        ).fetchone()
    else:
        result = db.execute(
            "SELECT COUNT(*) FROM candles"
        ).fetchone()
    return result[0]


# =========================
# Dernieres bougies
# =========================
def get_last_candles(limit=100):
    rows = db.execute(
        """
        SELECT timestamp, symbol, open, high, low, close, volume
        FROM candles
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        [limit]
    ).fetchall()
    print("ROWS =", len(rows))
    return rows


# =========================
# Premiere bougie stockee
# =========================
def get_first_timestamp(symbol, timeframe="1hour"):
    if timeframe in (None, "1hour"):
        sql = "SELECT MIN(timestamp) FROM candles WHERE symbol = ?"
        params = [symbol]
    else:
        sql = ("SELECT MIN(timestamp) FROM candles_multi "
               "WHERE symbol = ? AND timeframe = ?")
        params = [symbol, timeframe]
    row = db.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else None


# =========================
# Bougies d'un timeframe depuis une date
# =========================
def get_candles_tf(symbol, timeframe="1hour", since_ts=None):
    if timeframe in (None, "1hour"):
        sql = ("SELECT timestamp, open, high, low, close, volume "
               "FROM candles WHERE symbol = ?")
        params = [symbol]
    else:
        sql = ("SELECT timestamp, open, high, low, close, volume "
               "FROM candles_multi WHERE symbol = ? AND timeframe = ?")
        params = [symbol, timeframe]
    if since_ts is not None:
        sql += " AND timestamp >= ?"
        params.append(since_ts)
    sql += " ORDER BY timestamp"
    return db.execute(sql, params).fetchall()


# =========================
# Bougies depuis une date
# =========================
def get_candles_since(symbol, since_ts):
    return db.execute(
        """
        SELECT timestamp, symbol, open, high, low, close, volume
        FROM candles
        WHERE symbol = ? AND timestamp >= ?
        ORDER BY timestamp
        """,
        [symbol, since_ts]
    ).fetchall()


# =========================
# Settings
# =========================
def set_setting(key, value):
    db.execute(
        """
        INSERT OR REPLACE INTO settings VALUES (?, ?)
        """,
        [key, str(value)]
    )
    db.commit()


def get_setting(key, default_value):
    result = db.execute(
        """
        SELECT value FROM settings WHERE key = ?
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

    next_id = db.execute(
        """
        SELECT COALESCE(MAX(id), 0) + 1 FROM strategies
        """
    ).fetchone()[0]

    db.execute(
        """
        INSERT INTO strategies
        (id, name, ema_fast, ema_slow, ema_trend, rsi_period, rsi_min,
         stop_loss, take_profit, initial_capital, timeframe, sl_mode,
         atr_period, sl_atr, tp_atr, risk_pct, regime_ema)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (next_id, row[1] + " Copy") + tuple(row[2:17])
    )

    db.commit()
    return next_id


def delete_strategy(strategy_id):
    db.execute(
        """
        DELETE FROM strategies WHERE id = ?
        """,
        (strategy_id,)
    )
    db.commit()


def update_strategy(strategy_id, name, ema_fast, ema_slow, ema_trend,
                    rsi_period, rsi_min, stop_loss, take_profit, initial_capital,
                    timeframe="1hour", sl_mode="percent", atr_period=14,
                    sl_atr=2.0, tp_atr=4.0, risk_pct=0.0, regime_ema=0):
    db.execute(
        """
        UPDATE strategies
        SET name = ?, ema_fast = ?, ema_slow = ?, ema_trend = ?,
            rsi_period = ?, rsi_min = ?, stop_loss = ?, take_profit = ?,
            initial_capital = ?, timeframe = ?, sl_mode = ?,
            atr_period = ?, sl_atr = ?, tp_atr = ?, risk_pct = ?,
            regime_ema = ?
        WHERE id = ?
        """,
        [
            name, int(ema_fast), int(ema_slow), int(ema_trend),
            int(rsi_period), int(rsi_min),
            float(stop_loss), float(take_profit), float(initial_capital),
            str(timeframe), str(sl_mode),
            int(atr_period), float(sl_atr), float(tp_atr),
            float(risk_pct), int(regime_ema),
            int(strategy_id)
        ]
    )
    db.commit()
    