from flask import Flask, jsonify
import requests
import pandas as pd
import os

app = Flask(__name__)
SYMBOL = "BCHUSDT"

# 📊 Binance data safe
def get_data():
    url = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval=1h&limit=100"
    
    try:
        r = requests.get(url, timeout=10)
        data = r.json()

        if not isinstance(data, list):
            return pd.DataFrame()

        df = pd.DataFrame(data)

        df = df.iloc[:, [0,1,2,3,4,5]]
        df.columns = ["time","open","high","low","close","volume"]

        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna()

        return df

    except:
        return pd.DataFrame()

# 📈 EMA
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

# 📉 RSI SAFE
def rsi(series, period=14):
    delta = series.diff()

    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()

    rs = gain / loss.replace(0, 1)
    return (100 - (100 / (1 + rs))).fillna(50)

@app.route("/")
def home():
    return "BCH Signal API OK"

@app.route("/signal")
def signal():
    df = get_data()

    # 🔴 ULTRA SAFE CHECK
    if df is None or df.empty or len(df) < 20:
        return jsonify({
            "error": "not enough data",
            "rows": int(len(df)) if df is not None else 0
        })

    df["ema9"] = ema(df["close"], 9)
    df["ema20"] = ema(df["close"], 20)
    df["rsi"] = rsi(df["close"])

    last = df.iloc[-1]

    price = float(last["close"])
    ema9 = float(last["ema9"])
    ema20 = float(last["ema20"])
    rsi_value = float(last["rsi"])

    # 🎯 SIGNAL LOGIC
    if ema9 > ema20 and rsi_value < 70:
        signal = "BUY"
    elif ema9 < ema20:
        signal = "SELL"
    else:
        signal = "HOLD"

    return jsonify({
        "pair": SYMBOL,
        "signal": signal,
        "price": price,
        "rsi": rsi_value
    })

# 🚀 RENDER FIX
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
