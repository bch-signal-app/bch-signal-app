import pandas as pd

def run_backtest(
    rows,
    ema,
    EMA_FAST,
    EMA_SLOW,
    INITIAL_CAPITAL,
    TRADING_FEE
):
    

    if len(rows) < EMA_SLOW:
        return {
        "error": "not enough candles"
    }

    df = pd.DataFrame(
        rows,
        columns=[
            "time",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df = df.sort_values("time")

    df["ema_fast"] = ema(
        df["close"],
        EMA_FAST
    )

    df["ema_slow"] = ema(
        df["close"],
        EMA_SLOW
    )

    capital = INITIAL_CAPITAL
    position = 0.0

    trade_number = 0
    trades = []

    current_trade = None

    for i in range(1, len(df)):

        prev_fast = df.iloc[i - 1]["ema_fast"]
        prev_slow = df.iloc[i - 1]["ema_slow"]

        curr_fast = df.iloc[i]["ema_fast"]
        curr_slow = df.iloc[i]["ema_slow"]

        price = float(df.iloc[i]["close"])
        timestamp = int(df.iloc[i]["time"])

        buy_signal = (
            prev_fast <= prev_slow
            and
            curr_fast > curr_slow
        )

        sell_signal = (
            prev_fast >= prev_slow
            and
            curr_fast < curr_slow
        )

        # BUY
        if buy_signal and position == 0:

            quantity = (
                capital * (1 - TRADING_FEE)
            ) / price

            position = quantity

            trade_number += 1

            current_trade = {
                "trade": trade_number,
                "buy_time": timestamp,
                "buy_price": round(price, 4),
                "capital_before": round(capital, 2),
                "quantity": round(quantity, 8)
            }

            capital = 0

        # SELL
        elif sell_signal and position > 0:

            capital = (
                position
                * price
                * (1 - TRADING_FEE)
            )

            profit = (
                capital
                - current_trade["capital_before"]
            )

            profit_pct = (
                profit
                / current_trade["capital_before"]
            ) * 100

            current_trade.update({

                "sell_time": timestamp,

                "sell_price": round(
                    price,
                    4
                ),

                "capital_after": round(
                    capital,
                    2
                ),

                "profit": round(
                    profit,
                    2
                ),

                "profit_pct": round(
                    profit_pct,
                    2
                )

            })

            trades.append(
                current_trade
            )

            current_trade = None

            position = 0

    capital_end = capital

    total_profit = (
        capital_end
        - INITIAL_CAPITAL
    )

    return {

        "capital_start":
            INITIAL_CAPITAL,

        "capital_end":
            round(capital_end, 2),

        "profit":
            round(total_profit, 2),

        "profit_pct":
            round(
                total_profit
                / INITIAL_CAPITAL
                * 100,
                2
            ),

        "trades_count":
            len(trades),

        "trades":
            trades

    }