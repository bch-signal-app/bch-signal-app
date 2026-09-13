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
    TAKE_PROFIT,
    SL_MODE="percent",
    ATR_PERIOD=14,
    SL_ATR=2.0,
    TP_ATR=4.0,
    RISK_PCT=0.0,
    REGIME_EMA=0
):
    """Moteur de backtest.

    Signal d'entree : croisement haussier EMA fast/slow + RSI > RSI_MIN
    + prix > EMA_TREND (+ prix > EMA de regime si REGIME_EMA > 0).

    Sortie : croisement baissier, stop-loss ou take-profit. Les stops
    sont en % (SL_MODE="percent") ou en multiples d'ATR
    (SL_MODE="atr", STOP_LOSS/TAKE_PROFIT ignores).

    RISK_PCT > 0 : taille de position telle qu'un stop touche perd
    environ RISK_PCT % de l'equite (plafonne au comptant : jamais
    plus que le capital disponible). RISK_PCT = 0 : tout le capital.

    Position encore ouverte a la fin : valorisee au dernier cours
    (exit "FIN").
    """

    if len(rows) < EMA_SLOW:
        return {
            "error": "not enough candles"
        }

    # deux formats de lignes acceptes : (time, symbol, o, h, l, c, v)
    # depuis get_last_candles, ou (time, o, h, l, c, v) depuis
    # get_candles_tf — la colonne symbol n'est pas utilisee
    norm = [
        r[:1] + r[2:] if len(r) == 7 else tuple(r)
        for r in rows
    ]

    df = pd.DataFrame(
        norm,
        columns=[
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df = df.sort_values("time")

    df["ema_fast"] = ema(df["close"], EMA_FAST)
    df["ema_slow"] = ema(df["close"], EMA_SLOW)
    df["ema_trend"] = ema(df["close"], EMA_TREND)
    df["rsi"] = rsi(df["close"], RSI_PERIOD)

    if SL_MODE == "atr":
        prev_close = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs()
        ], axis=1).max(axis=1)
        df["atr"] = tr.rolling(ATR_PERIOD).mean().fillna(0)
    else:
        df["atr"] = 0.0

    if REGIME_EMA:
        df["regime"] = ema(df["close"], REGIME_EMA)

    # Colonnes converties en listes : la boucle devient ~100x plus
    # rapide que l'acces iloc ligne a ligne
    times = df["time"].astype("int64").tolist()
    closes = df["close"].astype(float).tolist()
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()
    emas_fast = df["ema_fast"].astype(float).tolist()
    emas_slow = df["ema_slow"].astype(float).tolist()
    emas_trend = df["ema_trend"].astype(float).tolist()
    rsi_values = df["rsi"].astype(float).tolist()
    atrs = df["atr"].astype(float).tolist()
    regimes = (
        df["regime"].astype(float).tolist() if REGIME_EMA else None
    )

    cash = float(INITIAL_CAPITAL)
    qty = 0.0
    alloc = 0.0
    stop_price = 0.0
    target_price = 0.0
    capital_before = 0.0

    trade = None
    trades = []
    trade_number = 0
    wins = 0
    losses = 0
    curve = []
    bars_in = 0

    for i in range(1, len(df)):

        price = closes[i]

        # --- gestion de la position ouverte ---
        if qty > 0:

            exit_price = None
            exit_reason = None

            if lows[i] <= stop_price:
                exit_price = stop_price
                exit_reason = "STOP_LOSS"
            elif highs[i] >= target_price:
                exit_price = target_price
                exit_reason = "TAKE_PROFIT"
            elif (
                emas_fast[i - 1] >= emas_slow[i - 1]
                and emas_fast[i] < emas_slow[i]
            ):
                exit_price = price
                exit_reason = "EMA_SELL"

            if exit_price is not None:

                capital_after = cash + qty * exit_price * (1 - TRADING_FEE)
                profit = capital_after - capital_before
                profit_pct = (
                    profit / capital_before * 100 if capital_before else 0
                )

                if profit > 0:
                    wins += 1
                else:
                    losses += 1

                trade.update({
                    "exit_reason": exit_reason,
                    "sell_time": times[i],
                    "sell_price": round(exit_price, 4),
                    "capital_after": round(capital_after, 2),
                    "profit": round(profit, 2),
                    "profit_pct": round(profit_pct, 2)
                })

                trades.append(trade)
                trade = None
                cash = capital_after
                qty = 0.0

        # --- signal d'entree ---
        if qty == 0.0:

            buy_signal = (
                emas_fast[i - 1] <= emas_slow[i - 1]
                and emas_fast[i] > emas_slow[i]
                and rsi_values[i] > RSI_MIN
                and price > emas_trend[i]
                and (regimes is None or price > regimes[i])
            )

            if buy_signal:

                can_enter = True

                if SL_MODE == "atr":
                    if atrs[i] <= 0:
                        can_enter = False  # ATR pas encore calculable
                    else:
                        stop_price = price - SL_ATR * atrs[i]
                        target_price = price + TP_ATR * atrs[i]
                else:
                    stop_price = price * (1 - STOP_LOSS / 100)
                    target_price = price * (1 + TAKE_PROFIT / 100)

                if can_enter:

                    if stop_price <= 0:
                        stop_price = price * 0.5

                    stop_dist = price - stop_price

                    if RISK_PCT and RISK_PCT > 0:
                        # risque fixe : allocation telle qu'un stop
                        # touche perd ~RISK_PCT% de l'equite, plafonnee
                        # au comptant (jamais plus que le cash dispo)
                        alloc = min(
                            cash,
                            RISK_PCT / 100 * cash * price
                            / (stop_dist * (1 - TRADING_FEE))
                        )
                    else:
                        alloc = cash

                    if alloc > 0:

                        qty = alloc * (1 - TRADING_FEE) / price
                        capital_before = cash
                        cash -= alloc
                        trade_number += 1

                        trade = {
                            "trade": trade_number,
                            "buy_time": times[i],
                            "buy_price": round(price, 4),
                            "capital_before": round(capital_before, 2),
                            "quantity": round(qty, 8)
                        }

        if qty > 0:
            bars_in += 1
            curve.append(cash + qty * price * (1 - TRADING_FEE))
        else:
            curve.append(cash)

    # Position encore ouverte a la fin : valorisee au dernier cours
    if qty > 0 and trade is not None:

        capital_after = cash + qty * closes[-1] * (1 - TRADING_FEE)
        profit = capital_after - capital_before
        profit_pct = profit / capital_before * 100 if capital_before else 0

        if profit > 0:
            wins += 1
        else:
            losses += 1

        trade.update({
            "exit_reason": "FIN",
            "sell_time": times[-1],
            "sell_price": round(closes[-1], 4),
            "capital_after": round(capital_after, 2),
            "profit": round(profit, 2),
            "profit_pct": round(profit_pct, 2)
        })

        trades.append(trade)
        trade = None
        cash = capital_after
        qty = 0.0
        curve[-1] = cash

    capital_end = cash

    peak = curve[0] if curve else INITIAL_CAPITAL
    max_dd = 0.0
    for e in curve:
        if e > peak:
            peak = e
        dd = (peak - e) / peak
        if dd > max_dd:
            max_dd = dd

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

        "max_dd_pct":
            round(max_dd * 100, 2),

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
