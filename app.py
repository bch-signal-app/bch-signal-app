from flask import Flask, jsonify
import requests
import pandas as pd

app = Flask(__name__)
SYMBOL = "BCHUSDT"

def get_data():
    url = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval=1h&limit=100"
    data = requests.get(url).json()

    df = pd.DataFrame(data, columns=["t","o","h","l","c","v","_"])
    df["c"] = df["c"].astype(float)
    return df

def ema(series, span):
    return series.ewm(span=span).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

@app.route("/signal")
def signal():
    df = get_data()

    df["ema9"] = ema(df["c"], 9)
    df["ema20"] = ema(df["c"], 20)
    df["rsi"] = rsi(df["c"])

    last = df.iloc[-1]

    if last["ema9"] > last["ema20"] and 40 < last["rsi"] < 70:
        sig = "BUY"
    elif last["ema9"] < last["ema20"] and last["rsi"] < 60:
        sig = "SELL"
    else:
        sig = "HOLD"

    return jsonify({
        "pair": SYMBOL,
        "signal": sig,
        "price": float(last["c"]),
        "rsi": float(last["rsi"])
    })

if __name__ == "__main__":
    app.run()

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
