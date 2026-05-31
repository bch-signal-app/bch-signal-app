from flask import Flask, jsonify
import requests
import pandas as pd
import os

app = Flask(__name__)
SYMBOL = "BCHUSDT"

# 📊 Récupération données Binance
def get_data():
    url = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval=1h&limit=100"
    data = requests.get(url, timeout=10).json()

    df = pd.DataFrame(data, columns=[
        "t","o","h","l","c","v","x","x2","x3","x4","x5","x6"
    ])

    df["c"] = pd.to_numeric(df["c"], errors="coerce")
    return df.dropna()

# 📈 EMA
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

# 📉 RSI stable (sans crash)
def rsi(series, period=14):
    delta = series.diff()

    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()

    rs = gain / loss.replace(0, 1)
    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)

@app.route("/")
def home():
    return "BCH Signal API is running"

@app.route("/signal")
def signal():
    df = get_data()

    if len(df) < 20:
        return jsonify({"error": "not enough data"})

    df["ema9"] = ema(df["c"], 9)
    df["ema20"] = ema(df["c"], 20)
    df["rsi"] = rsi(df["c"])

    last = df.iloc[-1]

    ema9 = float(last["ema9"])
    ema20 = float(last["ema20"])
    rsi_value = float(last["rsi"])
    price = float(last["c"])

    # 🎯 Logique signal simple
    if ema9 > ema20 and 40 < rsi_value < 70:
        sig = "BUY"
    elif ema9 < ema20 and rsi_value < 60:
        sig = "SELL"
    else:
        sig = "HOLD"

    return jsonify({
        "pair": SYMBOL,
        "signal": sig,
        "price": price,
        "rsi": rsi_value
    })

# 🚀 IMPORTANT Render
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
