from flask import Flask, jsonify, make_response, render_template, request
from flask_cors import CORS
import requests
import pandas as pd
import os
import time
from datetime import datetime

from db import save_candle
from db import count_candles
from db import get_last_candles
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
# Récupération données KuCoin
# =========================
def get_data():

    settings = get_app_settings()

    tf_seconds = TIMEFRAME_SECONDS.get(
        settings["timeframe"],
        3600
    )

    end_at = int(time.time())

    start_at = end_at - (settings["history_size"] * tf_seconds)

    url = (
        f"https://api.kucoin.com/api/v1/market/candles"
        f"?type={settings['timeframe']}"
        f"&symbol=BCH-USDT"
        f"&startAt={start_at}"
        f"&endAt={end_at}"
    )

    print(url)
    
    
#     url = (
#     f"https://api.kucoin.com/api/v1/market/candles"
#     f"?type={TIMEFRAME}"
#     f"&symbol=BCH-USDT"
# )

    try:

        response = requests.get(url, timeout=15)

        if response.status_code != 200:
            print("HTTP Error:", response.status_code)
            return pd.DataFrame()

        data = response.json()

        if data.get("code") != "200000":
            print("KuCoin Error:", data)
            return pd.DataFrame()

        candles = data.get("data", [])
        print("KuCoin returned:", len(candles))

        if len(candles) == 0:
            print("No candles returned")
            return pd.DataFrame()

        candles = candles[:settings["history_size"]]
        
        df = pd.DataFrame(
            candles,
            columns=[
                "time",
                "open",
                "close",
                "high",
                "low",
                "volume",
                "turnover"
            ]
        )

        # Conversion numérique
        for col in [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df = df.dropna()

        df = df.sort_values("time")

        for _, row in df.tail(settings["history_size"]).iterrows():

            save_candle(
                row["time"],
                APP_SYMBOL,
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"]
            )
        print("Rows loaded:", len(df))

        return df

    except Exception as e:

        print("ERROR get_data():", str(e))

        return pd.DataFrame()
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
        "rsi_period": settings["rsi_period"]
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

    rows = get_last_candles(get_app_settings()["history_size"])

    data = []

    for row in rows:

        data.append({
            "timestamp": row[0],
            "symbol": row[1],
            "open": row[2],
            "high": row[3],
            "low": row[4],
            "close": row[5],
            "volume": row[6]
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

        "initial_capital": row[9]
    })

# =========================
# Backtest
# =========================
@app.route("/backtest")
def backtest():

    settings = get_app_settings()

    rows = get_last_candles(settings["history_size"])

    result = run_backtest(
    rows,
    ema,
    rsi,
    settings["ema_fast"],
    settings["ema_slow"],
    settings["ema_trend"],
    settings["rsi_period"],
    settings["initial_capital"],
    APP_TRADING_FEE,
    settings["stop_loss"],
    settings["take_profit"]
    )

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
        "initial_capital": row[9]
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

    return jsonify({
        "success": True,
        "active_strategy_id": strategy_id
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
        data.get("initial_capital", row[9])
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

    rows = get_last_candles(get_app_settings()["history_size"])

    result = run_backtest(
        rows,
        ema,
        rsi,

        row[2],  # EMA_FAST
        row[3],  # EMA_SLOW
        row[4],  # EMA_TREND

        row[5],  # RSI_PERIOD

        row[9],  # INITIAL_CAPITAL

        APP_TRADING_FEE,

        row[7],  # STOP_LOSS
        row[8]   # TAKE_PROFIT
    )

    result["strategy_id"] = strategy_id
    result["strategy_name"] = row[1]

    return jsonify(result)

# =========================
# compare backtests
# =========================

@app.route("/backtest/compare")
def compare_backtests():

    rows = get_last_candles(get_app_settings()["history_size"])

    strategies = get_strategies()

    results = []

    for row in strategies:

        result = run_backtest(
            rows,
            ema,
            rsi,

            row[2],
            row[3],
            row[4],

            row[5],

            row[9],

            APP_TRADING_FEE,

            row[7],
            row[8]
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

            "win_rate": result["win_rate"]
        })

    results = sorted(
        results,
        key=lambda x: x["profit_pct"],
        reverse=True
    )

    return jsonify({
        "best_strategy": results[0] if results else None,
        "strategies": results
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
