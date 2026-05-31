from flask import Flask, jsonify
import requests
import pandas as pd
import os

app = Flask(__name__)

SYMBOL = "BCHUSDT"


# =========================
# Récupération données Binance
# =========================
def get_data():

    url = "https://api.kucoin.com/api/v1/market/candles?type=1hour&symbol=BCH-USDT"

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
            return pd.DataFrame()

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

        df["close"] = pd.to_numeric(df["close"], errors="coerce")

        df = df.dropna()

        df = df.sort_values("time")

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
def rsi(series, period=14):
    delta = series.diff()

    gain = delta.where(
        delta > 0,
        0
    ).rolling(period).mean()

    loss = (-delta.where(
        delta < 0,
        0
    )).rolling(period).mean()

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
# Debug kucoin
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
# Signal Trading
# =========================
@app.route("/signal")
def signal():

    df = get_data()

    if df.empty:
        return jsonify({
            "error": "not enough data",
            "rows": 0
        })

    if len(df) < 20:
        return jsonify({
            "error": "not enough data",
            "rows": len(df)
        })

    try:

        df["ema9"] = ema(df["close"], 9)
        df["ema20"] = ema(df["close"], 20)
        df["rsi"] = rsi(df["close"])

        last = df.iloc[-1]

        price = float(last["close"])
        ema9_value = float(last["ema9"])
        ema20_value = float(last["ema20"])
        rsi_value = float(last["rsi"])

        last_time = int(last["time"])

        if ema9_value > ema20_value and rsi_value < 70:
            signal_value = "BUY"

        elif ema9_value < ema20_value:
            signal_value = "SELL"

        else:
            signal_value = "HOLD"

last_time = int(last["time"])
        
return jsonify({
    "pair": SYMBOL,
    "signal": signal_value,
    "price": round(price, 4),
    "ema9": round(ema9_value, 4),
    "ema20": round(ema20_value, 4),
    "rsi": round(rsi_value, 2),
    "rows": len(df),
    "candle_time": last_time
})

    except Exception as e:
        return jsonify({
            "error": str(e)
        })


# =========================
# Render
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(
        host="0.0.0.0",
        port=port
    )
