from flask import Flask, jsonify
import requests
import pandas as pd
import os
import time

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


app = Flask(__name__)

# =========================
# Configuration  
# =========================
APP_SYMBOL = "BCHUSDT"

APP_TIMEFRAME = "1hour"

APP_HISTORY_SIZE = 1000

INITIAL_CAPITAL = 1000

STOP_LOSS = 1.0
TAKE_PROFIT = 2.0

APP_TRADING_FEE = 0.001  # 0.1%

EMA_FAST = 9
EMA_SLOW = 20

RSI_PERIOD = 14
EMA_TREND = 50

# =========================
# Récupération données KuCoin
# =========================
def get_data():

    
    end_at = int(time.time())

    start_at = end_at - (1000 * 3600)

    url = (
        f"https://api.kucoin.com/api/v1/market/candles"
        f"?type={APP_TIMEFRAME}"
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

        candles = candles[:APP_HISTORY_SIZE]
        
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

        for _, row in df.tail(APP_HISTORY_SIZE).iterrows():

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
# version
# =========================
@app.route("/version")
def version():
    return {
        "version": "2026-06-09"
    }

@app.route("/config")
def config():
    return jsonify({
        "symbol": APP_SYMBOL,
        "timeframe": APP_TIMEFRAME,
        "history_size": APP_HISTORY_SIZE,
        "ema_fast": EMA_FAST,
        "ema_slow": EMA_SLOW,
        "rsi_period": RSI_PERIOD
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

        df["EMA_FAST"] = ema(
        df["close"],
        EMA_FAST
        )

        df["EMA_SLOW"] = ema(
        df["close"],
        EMA_SLOW
        )

        df["rsi"] = rsi(
        df["close"],
        RSI_PERIOD
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

    return jsonify({
        "stored_candles": count_candles()
    })


# =========================
# History
# ========================= 
@app.route("/history")
def history():

    rows = get_last_candles(APP_HISTORY_SIZE)

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

    rows = get_last_candles(APP_HISTORY_SIZE)

    result = run_backtest(
    rows,
    ema,
    rsi,
    EMA_FAST,
    EMA_SLOW,
    EMA_TREND,
    RSI_PERIOD,
    INITIAL_CAPITAL,
    APP_TRADING_FEE,
    STOP_LOSS,
    TAKE_PROFIT
    )

    return jsonify(result)

# =========================
# config
# =========================
@app.route("/config")
def config():

    return jsonify({
        "symbol": APP_SYMBOL,
        "timeframe": APP_TIMEFRAME,
        "history_size": APP_HISTORY_SIZE,
        "ema_fast": EMA_FAST,
        "ema_slow": EMA_SLOW,
        "rsi_period": RSI_PERIOD
    })

# =========================
# /settings
# =========================    
@app.route("/settings")
def settings():

    return jsonify({

        "timeframe":
            get_setting(
                "timeframe",
                "1hour"
            ),

        "history_size":
            int(
                get_setting(
                    "history_size",
                    100
                )
            ),

        "ema_fast":
            int(
                get_setting(
                    "ema_fast",
                    9
                )
            ),

        "ema_slow":
            int(
                get_setting(
                    "ema_slow",
                    20
                )
            ),

        "rsi_period":
            int(
                get_setting(
                    "rsi_period",
                    14
                )
            ),

        "take_profit":
            float(
                get_setting(
                    "take_profit",
                    0.05
                )
            ),

        "stop_loss":
            float(
                get_setting(
                    "stop_loss",
                    0.02
                )
            ),

        "initial_capital":
            float(
                get_setting(
                    "initial_capital",
                    1000
                )
            )
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
# backtest strategy
# =========================
@app.route("/backtest/<int:strategy_id>")
def backtest_strategy(strategy_id):

    row = get_strategy(strategy_id)

    if not row:
        return jsonify({
            "error": "strategy not found"
        })

    rows = get_last_candles(APP_HISTORY_SIZE)

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

    rows = get_last_candles(APP_HISTORY_SIZE)

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
