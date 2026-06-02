from flask import Flask, jsonify
import requests
import pandas as pd
import os

from db import save_candle
from db import count_candles
from db import get_last_candles

app = Flask(__name__)

# =========================
# Configuration
# =========================
SYMBOL = "BCHUSDT"

TIMEFRAME = "1hour"

HISTORY_SIZE = 100

EMA_FAST = 9
EMA_SLOW = 20

RSI_PERIOD = 14

# =========================
# Récupération données KuCoin
# =========================
def get_data():

    url = (
    f"https://api.kucoin.com/api/v1/market/candles"
    f"?type={TIMEFRAME}"
    f"&symbol=BCH-USDT"
)

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

        if len(candles) == 0:
            print("No candles returned")
            return pd.DataFrame()

        candles = candles[:HISTORY_SIZE]
        
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

        for _, row in df.tail(HISTORY_SIZE).iterrows():

            save_candle(
                row["time"],
                SYMBOL,
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
        "pair": SYMBOL
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
        "pair": SYMBOL,
        "price": round(float(last["close"]), 4),
        "time": int(last["time"])
    })


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

            "pair": SYMBOL,

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

    rows = get_last_candles(HISTORY_SIZE)

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

