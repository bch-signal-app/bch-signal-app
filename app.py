from flask import Flask, jsonify, make_response, render_template, request
from flask_cors import CORS
import pandas as pd
import os
from datetime import datetime, timezone

from db import count_candles
from db import get_last_candles
from db import get_candles_tf
from db import get_first_timestamp
from db import set_setting
from db import get_setting
from backtest import run_backtest
from db import db
from db import create_default_strategy
from db import get_strategies
from db import get_strategy
from db import clone_strategy
from db import delete_strategy
from db import update_strategy
from kucoin import update_candles



# =========================
# Version affichee dans le dashboard
# build.txt = nombre total de commits du projet, ecrit a chaque
# commit par hooks/pre-commit. Le fichier est dans le depot :
# local et Render affichent le meme numero.
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def read_build():

    try:
        with open(os.path.join(BASE_DIR, "build.txt")) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


BUILD_NUMBER = read_build()

APP_VERSION = (
    f"{datetime.now():%Y-%m-%d}."
    f"{BUILD_NUMBER}"
)

APP_COMMIT = os.environ.get("RENDER_GIT_COMMIT", "")[:7]

APP_BRANCH = os.environ.get("RENDER_GIT_BRANCH", "")


app = Flask(__name__)

CORS(app)

# =========================
# Configuration (valeurs par défaut)
# La table `settings` en base prime sur ces constantes
# (voir get_app_settings)
# =========================
APP_SYMBOL = "BCHUSDT"

API_SYMBOL = "BCH-USDT"

APP_TIMEFRAME = "1hour"

APP_HISTORY_SIZE = 1000

INITIAL_CAPITAL = 1000

# En pourcentage (2.0 = 2%), unité utilisée par le backtest et le dashboard
STOP_LOSS = 1.0
TAKE_PROFIT = 2.0

APP_TRADING_FEE = 0.001  # 0.1%

EMA_FAST = 9
EMA_SLOW = 20

RSI_PERIOD = 14
RSI_MIN = 55
EMA_TREND = 50

# Durée d'une bougie KuCoin en secondes, par type de timeframe
TIMEFRAME_SECONDS = {
    "1min": 60,
    "3min": 180,
    "5min": 300,
    "15min": 900,
    "30min": 1800,
    "1hour": 3600,
    "2hour": 7200,
    "4hour": 14400,
    "6hour": 21600,
    "8hour": 28800,
    "12hour": 43200,
    "1day": 86400,
    "1week": 604800
}


# =========================
# Lecture des réglages
# (table settings, sinon valeur par défaut)
# =========================
def _as_int(value, default):

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value, default):

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_app_settings():

    return {
        "timeframe":
            str(
                get_setting(
                    "timeframe",
                    APP_TIMEFRAME
                )
            ),

        "history_size":
            _as_int(
                get_setting("history_size", APP_HISTORY_SIZE),
                APP_HISTORY_SIZE
            ),

        "ema_fast":
            _as_int(
                get_setting("ema_fast", EMA_FAST),
                EMA_FAST
            ),

        "ema_slow":
            _as_int(
                get_setting("ema_slow", EMA_SLOW),
                EMA_SLOW
            ),

        "ema_trend":
            _as_int(
                get_setting("ema_trend", EMA_TREND),
                EMA_TREND
            ),

        "rsi_period":
            _as_int(
                get_setting("rsi_period", RSI_PERIOD),
                RSI_PERIOD
            ),

        "rsi_min":
            _as_int(
                get_setting("rsi_min", RSI_MIN),
                RSI_MIN
            ),

        "sl_mode":
            str(
                get_setting("sl_mode", "percent")
            ),

        "atr_period":
            _as_int(
                get_setting("atr_period", 14),
                14
            ),

        "sl_atr":
            _as_float(
                get_setting("sl_atr", 2.0),
                2.0
            ),

        "tp_atr":
            _as_float(
                get_setting("tp_atr", 4.0),
                4.0
            ),

        "risk_pct":
            _as_float(
                get_setting("risk_pct", 0.0),
                0.0
            ),

        "regime_ema":
            _as_int(
                get_setting("regime_ema", 0),
                0
            ),

        "stop_loss":
            _as_float(
                get_setting("stop_loss", STOP_LOSS),
                STOP_LOSS
            ),

        "take_profit":
            _as_float(
                get_setting("take_profit", TAKE_PROFIT),
                TAKE_PROFIT
            ),

        "initial_capital":
            _as_float(
                get_setting("initial_capital", INITIAL_CAPITAL),
                INITIAL_CAPITAL
            )
    }

# =========================
# Recuperation donnees KuCoin
# Mise a jour incrementielle : la base DuckDB conserve tout,
# on ne telecharge que les bougies manquantes.
# =========================
def get_data():

    settings = get_app_settings()

    tf = settings["timeframe"]

    tf_seconds = TIMEFRAME_SECONDS.get(tf, 3600)

    update_candles(
        API_SYMBOL,
        APP_SYMBOL,
        tf,
        tf_seconds,
        target=settings["history_size"],
        table=("candles" if tf == "1hour" else "candles_multi")
    )

    if tf == "1hour":
        sql = ("SELECT timestamp, open, high, low, close, volume "
               "FROM candles WHERE symbol = ? "
               "ORDER BY timestamp DESC LIMIT ?")
        params = [APP_SYMBOL, settings["history_size"]]
    else:
        sql = ("SELECT timestamp, open, high, low, close, volume "
               "FROM candles_multi WHERE symbol = ? AND timeframe = ? "
               "ORDER BY timestamp DESC LIMIT ?")
        params = [APP_SYMBOL, tf, settings["history_size"]]

    rows = db.execute(sql, params).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows,
        columns=[
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    return df.sort_values("time")
# =========================
# EMA
# =========================
def ema(series, span):

    return series.ewm(
        span=span,
        adjust=False
    ).mean()


# =========================
# RSI
# =========================
def rsi(series, period=RSI_PERIOD):

    delta = series.diff()

    gain = delta.where(
        delta > 0,
        0
    ).rolling(period).mean()

    loss = (
        -delta.where(
            delta < 0,
            0
        )
    ).rolling(period).mean()

    rs = gain / loss.replace(0, 1)

    return (
        100 - (100 / (1 + rs))
    ).fillna(50)


def parse_since_date(value):
    """'AAAA-MM-JJ' -> timestamp UTC (minuit), None si absent/invalide."""

    if not value:
        return None

    try:
        return int(
            datetime.strptime(value, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    except ValueError:
        return None


def iso_date(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


# =========================
# Home
# =========================
@app.route("/")
def home():

    return jsonify({
        "status": "online",
        "pair": APP_SYMBOL
    })

# =========================
# dashboard
# =========================
@app.route("/dashboard")
def dashboard():
    resp = make_response(
        render_template(
            "dashboard.html",
            app_version=APP_VERSION
        )
    )

    # Garantit un badge de version toujours a jour (pas de cache navigateur)
    resp.headers["Cache-Control"] = "no-store"

    return resp

# =========================
# version
# =========================
@app.route("/version")
def version():
    return {
        "version": APP_VERSION,
        "commit": APP_COMMIT or "local",
        "branch": APP_BRANCH or "local"
    }

@app.route("/config")
def config():
    settings = get_app_settings()

    return jsonify({
        "symbol": APP_SYMBOL,
        "timeframe": settings["timeframe"],
        "history_size": settings["history_size"],
        "ema_fast": settings["ema_fast"],
        "ema_slow": settings["ema_slow"],
        "ema_trend": settings["ema_trend"],
        "rsi_period": settings["rsi_period"],
        "rsi_min": settings["rsi_min"]
    })


# =========================
# Debug KuCoin
# =========================
@app.route("/debug")
def debug():

    url = "https://api.kucoin.com/api/v1/market/candles?type=1hour&symbol=BCH-USDT"

    try:

        r = requests.get(url, timeout=15)

        return jsonify({
            "status_code": r.status_code,
            "response": r.json()
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        })


# =========================
# Prix actuel
# =========================
@app.route("/price")
def price():

    df = get_data()

    if df.empty:

        return jsonify({
            "error": "no data"
        })

    last = df.iloc[-1]

    return jsonify({
        "pair": APP_SYMBOL,
        "price": round(float(last["close"]), 4),
        "time": int(last["time"])
    })

# =========================
# refresh
# =========================
@app.route("/refresh")
def refresh():
    df = get_data()

    db.commit()

    return {
        "loaded": len(df),
        "stored_candles": count_candles()
    }

# =========================
# dbinfo
# =========================
@app.route("/dbinfo")
def dbinfo():

    result = db.execute("""
        SELECT
            MIN(timestamp),
            MAX(timestamp),
            COUNT(*)
        FROM candles
    """).fetchone()

    return {
        "min_timestamp": result[0],
        "max_timestamp": result[1],
        "count": result[2]
    }

# =========================
# Signal Trading
# =========================
@app.route("/signal")
def signal():

    df = get_data()

    if df.empty:

        return jsonify({
            "error": "not enough data"
        })

    try:

        settings = get_app_settings()

        tf = settings["timeframe"]

        df["EMA_FAST"] = ema(
        df["close"],
        settings["ema_fast"]
        )

        df["EMA_SLOW"] = ema(
        df["close"],
        settings["ema_slow"]
        )

        df["rsi"] = rsi(
        df["close"],
        settings["rsi_period"]
        )

        last = df.iloc[-1]

        price = float(last["close"])
        ema_fast_value = float(last["EMA_FAST"])
        ema_slow_value = float(last["EMA_SLOW"])
        rsi_value = float(last["rsi"])

        last_time = int(last["time"])

        if ema_fast_value > ema_slow_value and rsi_value < 70:

            signal_value = "BUY"

        elif ema_fast_value < ema_slow_value:

            signal_value = "SELL"

        else:

            signal_value = "HOLD"

        return jsonify({

            "pair": APP_SYMBOL,

            "signal": signal_value,

            "price": round(price, 4),

            "EMA_FAST": round(
                ema_fast_value,
                4
            ),

            "EMA_SLOW": round(
                ema_slow_value,
                4
            ),

            "rsi": round(
                rsi_value,
                2
            ),

            "timeframe": tf,

            "ema_fast_period": settings["ema_fast"],

            "ema_slow_period": settings["ema_slow"],

            "rsi_period": settings["rsi_period"],

            "rows": len(df),

            "candle_time": last_time

        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        })


# =========================
# Statistiques
# =========================
@app.route("/stats")
def stats():
    try:
        count = count_candles()
        return jsonify({
            "stored_candles": count
        })
    except Exception as e:
        return jsonify({
            "stored_candles": 0,
            "error": str(e)
        })

# =========================
# History
# ========================= 
@app.route("/history")
def history():

    settings = get_app_settings()

    tf = settings["timeframe"]
    tf_seconds = TIMEFRAME_SECONDS.get(tf, 3600)
    table = "candles" if tf == "1hour" else "candles_multi"

    update_candles(
        API_SYMBOL,
        APP_SYMBOL,
        tf,
        tf_seconds,
        target=settings["history_size"],
        table=table
    )

    if tf == "1hour":
        sql = ("SELECT timestamp, open, high, low, close, volume "
               "FROM candles WHERE symbol = ? "
               "ORDER BY timestamp DESC LIMIT ?")
        params = [APP_SYMBOL, settings["history_size"]]
    else:
        sql = ("SELECT timestamp, open, high, low, close, volume "
               "FROM candles_multi WHERE symbol = ? AND timeframe = ? "
               "ORDER BY timestamp DESC LIMIT ?")
        params = [APP_SYMBOL, tf, settings["history_size"]]

    rows = db.execute(sql, params).fetchall()

    data = []

    for row in rows:

        data.append({
            "timestamp": row[0],
            "symbol": APP_SYMBOL,
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "volume": row[5],
            "timeframe": tf
        })

    return jsonify(data)

# =========================
# strategies
# =========================
@app.route("/strategies")
def strategies():

    rows = get_strategies()

    data = []

    for row in rows:

        data.append({
            "id": row[0],
            "name": row[1]
        })

    return jsonify(data)

# =========================
# strategy
# =========================
@app.route("/strategy/<int:strategy_id>")
def strategy(strategy_id):

    row = get_strategy(strategy_id)

    if not row:
        return jsonify({
            "error": "strategy not found"
        })

    return jsonify({

        "id": row[0],
        "name": row[1],

        "ema_fast": row[2],
        "ema_slow": row[3],
        "ema_trend": row[4],

        "rsi_period": row[5],
        "rsi_min": row[6],

        "stop_loss": row[7],
        "take_profit": row[8],

        "initial_capital": row[9],

        "timeframe": row[10],
        "sl_mode": row[11],
        "atr_period": row[12],
        "sl_atr": row[13],
        "tp_atr": row[14],
        "risk_pct": row[15],
        "regime_ema": row[16]
    })

# =========================
# Backtest
# =========================
@app.route("/backtest")
def backtest():

    settings = get_app_settings()

    tf = settings["timeframe"]
    tf_seconds = TIMEFRAME_SECONDS.get(tf, 3600)
    table = "candles" if tf == "1hour" else "candles_multi"

    update_candles(
        API_SYMBOL,
        APP_SYMBOL,
        tf,
        tf_seconds,
        target=settings["history_size"],
        table=table
    )

    if tf == "1hour":
        sql = ("SELECT timestamp, symbol, open, high, low, close, volume "
               "FROM candles WHERE symbol = ? "
               "ORDER BY timestamp DESC LIMIT ?")
        params = [APP_SYMBOL, settings["history_size"]]
    else:
        sql = ("SELECT timestamp, symbol, open, high, low, close, volume "
               "FROM candles_multi "
               "WHERE symbol = ? AND timeframe = ? "
               "ORDER BY timestamp DESC LIMIT ?")
        params = [APP_SYMBOL, tf, settings["history_size"]]

    rows = db.execute(sql, params).fetchall()

    result = run_backtest(
    rows,
    ema,
    rsi,
    settings["ema_fast"],
    settings["ema_slow"],
    settings["ema_trend"],
    settings["rsi_period"],
    settings["rsi_min"],
    settings["initial_capital"],
    APP_TRADING_FEE,
    settings["stop_loss"],
    settings["take_profit"],
    SL_MODE=settings["sl_mode"],
    ATR_PERIOD=settings["atr_period"],
    SL_ATR=settings["sl_atr"],
    TP_ATR=settings["tp_atr"],
    RISK_PCT=settings["risk_pct"],
    REGIME_EMA=settings["regime_ema"]
    )

    result["timeframe"] = tf

    return jsonify(result)

# =========================
# /settings
# =========================    
@app.route("/settings")
def settings():

    return jsonify(get_app_settings())

# =========================
# settings/update
# =========================
@app.route("/settings/update", methods=["POST"])
def update_settings():

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "no data provided"
        })

    for key, value in data.items():
        set_setting(key, str(value))

    return jsonify({
        "success": True
    })

# =========================
# active-strategy 
# =========================

@app.route("/active-strategy")
def active_strategy():
    strategy_id = int(
        get_setting("active_strategy_id", 1)
    )

    row = get_strategy(strategy_id)

    if not row:
        return jsonify({
            "error": "strategy not found"
        })

    return jsonify({
        "id": row[0],
        "name": row[1],
        "ema_fast": row[2],
        "ema_slow": row[3],
        "ema_trend": row[4],
        "rsi_period": row[5],
        "rsi_min": row[6],
        "stop_loss": row[7],
        "take_profit": row[8],
        "initial_capital": row[9],
        "timeframe": row[10],
        "sl_mode": row[11],
        "atr_period": row[12],
        "sl_atr": row[13],
        "tp_atr": row[14],
        "risk_pct": row[15],
        "regime_ema": row[16]
    })

# =========================
# set active-strategy
# =========================
@app.route("/active-strategy/<int:strategy_id>")
def set_active_strategy(strategy_id):

    row = get_strategy(strategy_id)

    if not row:
        return jsonify({
            "error": "strategy not found"
        })

    set_setting(
        "active_strategy_id",
        strategy_id
    )

    # Synchronise les parametres de la strategie dans la config
    # globale : /signal, /config, /settings, /history et /backtest
    # reflechent desormais la strategie active
    synced = {
        "timeframe": row[10],
        "ema_fast": row[2],
        "ema_slow": row[3],
        "ema_trend": row[4],
        "rsi_period": row[5],
        "rsi_min": row[6],
        "stop_loss": row[7],
        "take_profit": row[8],
        "initial_capital": row[9],
        "sl_mode": row[11],
        "atr_period": row[12],
        "sl_atr": row[13],
        "tp_atr": row[14],
        "risk_pct": row[15],
        "regime_ema": row[16]
    }

    for key, value in synced.items():
        set_setting(key, value)

    return jsonify({
        "success": True,
        "active_strategy_id": strategy_id,
        "synced": synced
    })

# =========================
# clone strategy
# =========================
@app.route(
    "/strategy/<int:strategy_id>/clone",
    methods=["POST"]
)
def clone_strategy_route(strategy_id):

    new_id = clone_strategy(strategy_id)

    if not new_id:
        return jsonify({
            "error": "strategy not found"
        })

    return jsonify({
        "success": True,
        "new_strategy_id": new_id
    })

# =========================
# delete  strategy
# =========================
@app.route(
    "/strategy/<int:strategy_id>/delete"
)
def delete_strategy_route(strategy_id):

    delete_strategy(strategy_id)

    return jsonify({
        "success": True
    })

# =========================
# delete update_strategy_route
# =========================
@app.route("/strategy/<int:strategy_id>/update", methods=["PUT"])
def update_strategy_route(strategy_id):

    row = get_strategy(strategy_id)

    if not row:
        return jsonify({
            "error": "strategy not found"
        })

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "no data provided"
        })

    update_strategy(
        strategy_id,
        data.get("name", row[1]),
        data.get("ema_fast", row[2]),
        data.get("ema_slow", row[3]),
        data.get("ema_trend", row[4]),
        data.get("rsi_period", row[5]),
        data.get("rsi_min", row[6]),
        data.get("stop_loss", row[7]),
        data.get("take_profit", row[8]),
        data.get("initial_capital", row[9]),
        data.get("timeframe", row[10]),
        data.get("sl_mode", row[11]),
        data.get("atr_period", row[12]),
        data.get("sl_atr", row[13]),
        data.get("tp_atr", row[14]),
        data.get("risk_pct", row[15]),
        data.get("regime_ema", row[16])
    )

    return jsonify({
        "success": True,
        "strategy_id": strategy_id
    })

# =========================
# backtest strategy
# =========================
@app.route("/backtest/<int:strategy_id>")
def backtest_strategy(strategy_id):

    row = get_strategy(strategy_id)

    if not row:
        return jsonify({
            "error": "strategy not found"
        })

    settings = get_app_settings()

    timeframe = row[10] or "1hour"
    tf_seconds = TIMEFRAME_SECONDS.get(timeframe, 3600)

    # mise a jour incrementielle du timeframe de la strategie
    update_candles(
        API_SYMBOL,
        APP_SYMBOL,
        timeframe,
        tf_seconds,
        target=settings["history_size"],
        table=("candles" if timeframe == "1hour" else "candles_multi")
    )

    all_rows = get_candles_tf(APP_SYMBOL, timeframe)
    rows = all_rows[-settings["history_size"]:]

    result = run_backtest(
        rows,
        ema,
        rsi,

        row[2],  # EMA_FAST
        row[3],  # EMA_SLOW
        row[4],  # EMA_TREND

        row[5],  # RSI_PERIOD
        row[6],  # RSI_MIN

        row[9],  # INITIAL_CAPITAL

        APP_TRADING_FEE,

        row[7],  # STOP_LOSS
        row[8],  # TAKE_PROFIT

        SL_MODE=row[11],     # sl_mode
        ATR_PERIOD=row[12],  # atr_period
        SL_ATR=row[13],      # sl_atr
        TP_ATR=row[14],      # tp_atr
        RISK_PCT=row[15],    # risk_pct
        REGIME_EMA=row[16]   # regime_ema
    )

    result["strategy_id"] = strategy_id
    result["strategy_name"] = row[1]
    result["timeframe"] = timeframe

    return jsonify(result)

# =========================
# compare backtests
# =========================

@app.route("/backtest/period")
def backtest_period():

    tf = request.args.get("tf", "1hour")
    if tf not in TIMEFRAME_SECONDS:
        tf = "1hour"

    first_ts = get_first_timestamp(APP_SYMBOL, tf)

    if not first_ts:
        return jsonify({"error": "no data"})

    since_ts = parse_since_date(request.args.get("since"))

    clamped = False
    if since_ts is None or since_ts < first_ts:
        since_ts = first_ts
        clamped = True

    table = "candles" if tf == "1hour" else "candles_multi"
    if tf == "1hour":
        sql = ("SELECT COUNT(*) FROM candles "
               "WHERE symbol = ? AND timestamp >= ?")
        params = [APP_SYMBOL, since_ts]
    else:
        sql = ("SELECT COUNT(*) FROM candles_multi "
               "WHERE symbol = ? AND timestamp >= ? AND timeframe = ?")
        params = [APP_SYMBOL, since_ts, tf]
    count = db.execute(sql, params).fetchone()[0]

    return jsonify({
        "timeframe": tf,
        "since": iso_date(since_ts),
        "first_available": iso_date(first_ts),
        "clamped": clamped,
        "candles": count
    })


@app.route("/backtest/compare")
def compare_backtests():

    tf = request.args.get("tf", "1hour")
    if tf not in TIMEFRAME_SECONDS:
        tf = "1hour"
    tf_seconds = TIMEFRAME_SECONDS[tf]

    table = "candles" if tf == "1hour" else "candles_multi"

    settings = get_app_settings()

    # mise a jour incrementielle des bougies du timeframe demande
    update_candles(
        API_SYMBOL,
        APP_SYMBOL,
        tf,
        tf_seconds,
        target=settings["history_size"],
        table=table
    )

    first_ts = get_first_timestamp(APP_SYMBOL, tf)

    since_ts = parse_since_date(request.args.get("since"))

    # la date de debut ne peut pas preceder la premiere bougie disponible
    if since_ts is not None and first_ts is not None and since_ts < first_ts:
        since_ts = first_ts

    if since_ts is not None:
        rows = get_candles_tf(APP_SYMBOL, tf, since_ts)
    elif first_ts is not None:
        rows = get_candles_tf(APP_SYMBOL, tf, first_ts)
    else:
        rows = []

    period = {
        "timeframe": tf,
        "since": iso_date(since_ts if since_ts is not None else (first_ts or 0)),
        "first_available": iso_date(first_ts) if first_ts else None,
        "candles": len(rows)
    }

    if len(rows) < 250:
        return jsonify({
            "error":
                f"periode trop courte : {len(rows)} bougies (minimum 250)",
            "period": period
        })

    strategies = get_strategies()

    results = []

    for row in strategies:

        result = run_backtest(
            rows,
            ema,
            rsi,

            row[2],  # EMA_FAST
            row[3],  # EMA_SLOW
            row[4],  # EMA_TREND

            row[5],  # RSI_PERIOD
            row[6],  # RSI_MIN

            row[9],  # INITIAL_CAPITAL

            APP_TRADING_FEE,

            row[7],  # STOP_LOSS
            row[8],  # TAKE_PROFIT

            SL_MODE=row[11],     # sl_mode
            ATR_PERIOD=row[12],  # atr_period
            SL_ATR=row[13],      # sl_atr
            TP_ATR=row[14],      # tp_atr
            RISK_PCT=row[15],    # risk_pct
            REGIME_EMA=row[16]   # regime_ema
        )

        results.append({

            "id": row[0],
            "name": row[1],

            "profit_pct": result["profit_pct"],
            "profit": result["profit"],

            "capital_end": result["capital_end"],

            "trades_count": result["trades_count"],

            "wins": result["wins"],
            "losses": result["losses"],

            "win_rate": result["win_rate"],
            "max_dd_pct": result.get("max_dd_pct")
        })

    results = sorted(
        results,
        key=lambda x: x["profit_pct"],
        reverse=True
    )

    return jsonify({
        "best_strategy": results[0] if results else None,
        "strategies": results,
        "period": period
    })

# =========================
# Créer Une seule fois 
# =========================
create_default_strategy()

# =========================
# Render
# =========================
if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
