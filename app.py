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
    url = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval=1h&limit=100"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=15
        )

        print("Status Code:", response.status_code)

        if response.status_code != 200:
            print("Response:", response.text)
            return pd.DataFrame()

        data = response.json()

        if not isinstance(data, list):
            print("Unexpected Binance response:", data)
            return pd.DataFrame()

        df = pd.DataFrame(data)

        if len(df) == 0:
            print("Empty dataframe from Binance")
            return pd.DataFrame()

        df = df.iloc[:, [0, 1, 2, 3, 4, 5]]
        df.columns = [
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        df["close"] = pd.to_numeric(
            df["close"],
            errors="coerce"
        )

        df = df.dropna()

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
# Debug Binance
# =========================
@app.route("/debug")
def debug():

    urls = [
        "https://api.lbkex.com/v2/kline.do?symbol=bch_usdt&type=1hour&size=5",
        "https://api.lbkex.com/v2/kline.do?symbol=bch_usdt&type=hour1&size=5",
        "https://api.lbkex.com/v2/kline.do?symbol=bch_usdt&type=60min&size=5"
    ]

    results = {}

    for u in urls:
        try:
            r = requests.get(u, timeout=10)
            results[u] = r.json()
        except Exception as e:
            results[u] = str(e)

    return jsonify(results)


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

        if ema9_value > ema20_value and rsi_value < 70:
            signal_value = "BUY"

        elif ema9_value < ema20_value:
            signal_value = "SELL"

        else:
            signal_value = "HOLD"

        return jsonify({
            "pair": SYMBOL,
            "signal": signal_value,
            "price": round(price, 4),
            "ema9": round(ema9_value, 4),
            "ema20": round(ema20_value, 4),
            "rsi": round(rsi_value, 2),
            "rows": len(df)
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
