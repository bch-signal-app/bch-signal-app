import pandas as pd

def run_backtest(
    rows,
    ema,
    rsi,
    EMA_FAST,
    EMA_SLOW,
    EMA_TREND,
    RSI_PERIOD,
    RSI_MIN,
    INITIAL_CAPITAL,
    TRADING_FEE,
    STOP_LOSS,
    TAKE_PROFIT
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

    df["ema_trend"] = ema(
    df["close"],
    EMA_TREND
    )
    
    df["rsi"] = rsi(
    df["close"],
    RSI_PERIOD
    )

    capital = INITIAL_CAPITAL
    position = 0.0

    trade_number = 0
    trades = []

    entry_price = None
    current_trade = None

    wins = 0
    losses = 0

    # Colonnes converties en listes : la boucle devient ~100x plus
    # rapide que l'acces iloc ligne a ligne (indispensable au-dela
    # de quelques milliers de bougies)
    times = df["time"].astype("int64").tolist()
    closes = df["close"].astype(float).tolist()
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()
    emas_fast = df["ema_fast"].astype(float).tolist()
    emas_slow = df["ema_slow"].astype(float).tolist()
    emas_trend = df["ema_trend"].astype(float).tolist()
    rsi_values = df["rsi"].astype(float).tolist()

    for i in range(1, len(df)):

        prev_fast = emas_fast[i - 1]
        prev_slow = emas_slow[i - 1]

        curr_fast = emas_fast[i]
        curr_slow = emas_slow[i]

        price = closes[i]
        timestamp = times[i]

        rsi_value = rsi_values[i]
        trend_value = emas_trend[i]

        stop_loss_triggered = False
        take_profit_triggered = False

        high_price = highs[i]
        low_price = lows[i]

        if position > 0:

            stop_price = (
                entry_price
                * (1 - STOP_LOSS / 100)
            )

            take_profit_price = (
                entry_price
                * (1 + TAKE_PROFIT / 100)
            )

            if low_price <= stop_price:
                stop_loss_triggered = True
                price = stop_price

            elif high_price >= take_profit_price:
                take_profit_triggered = True
                price = take_profit_price

        buy_signal = (
            prev_fast <= prev_slow
            and
            curr_fast > curr_slow
            and
            rsi_value > RSI_MIN
            and price > trend_value
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
            entry_price = price

            current_trade = {
                "trade": trade_number,
                "buy_time": timestamp,
                "buy_price": round(price, 4),
                "capital_before": round(capital, 2),
                "quantity": round(quantity, 8),
            }

            capital = 0

        # SELL
        elif (
            sell_signal
            or stop_loss_triggered
            or take_profit_triggered
        ) and position > 0:

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

            if profit > 0:
                wins += 1
            else:
                losses += 1

            current_trade.update({

                "exit_reason":
                    (
                        "STOP_LOSS"
                        if stop_loss_triggered
                        else
                        "TAKE_PROFIT"
                        if take_profit_triggered
                        else
                        "EMA_SELL"
                    ),

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

    # Position encore ouverte a la fin : on la valorise au dernier
    # cours, sinon le capital final serait compte pour 0
    if position > 0 and current_trade is not None:

        price = float(df.iloc[-1]["close"])
        timestamp = int(df.iloc[-1]["time"])

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

        if profit > 0:
            wins += 1
        else:
            losses += 1

        current_trade.update({

            "exit_reason": "FIN",

            "sell_time": timestamp,

            "sell_price": round(price, 4),

            "capital_after": round(capital, 2),

            "profit": round(profit, 2),

            "profit_pct": round(profit_pct, 2)

        })

        trades.append(current_trade)

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

        "wins":
            wins,

        "losses":
            losses,

        "win_rate":
            round(
                wins / len(trades) * 100,
                2
            ) if len(trades) > 0 else 0,

        "trades":
            trades
    }