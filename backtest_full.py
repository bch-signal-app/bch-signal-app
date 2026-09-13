"""Backtest des strategies sur TOUT l'historique stocke dans DuckDB.

- Cree les 10 strategies si elles n'existent pas encore.
- Moteur rapide (listes, pas de iloc) avec les memes regles que
  backtest.py : croisement EMA + filtre RSI > rsi_min + prix > EMA
  tendance a l'achat, croisement inverse / stop-loss / take-profit
  a la vente, position finale valorisee au dernier cours ("FIN").
- Compare avec un Buy & Hold et sauvegarde backtest_report.md.

Usage : python backtest_full.py
"""
import json
from datetime import datetime

from db import db, create_strategy, get_strategies

FEE = 0.001

# (nom, ema_fast, ema_slow, ema_trend, rsi_period, rsi_min,
#  stop_loss %, take_profit %, capital initial)
STRATEGIES = [
    ("Default",          9,  20,  50, 14, 55, 1.0,  2.0, 1000),
    ("Scalp Rapide",     5,  12,  50,  9, 52, 0.6,  1.2, 1000),
    ("Turbo",            3,   8,  50,  7, 55, 0.5,  1.0, 1000),
    ("Swing Classique", 12,  26, 100, 14, 55, 2.0,  4.0, 1000),
    ("Tendance 200",    20,  50, 200, 21, 55, 3.0,  6.0, 1000),
    ("Momentum Fort",   10,  30, 100, 14, 62, 2.5,  5.0, 1000),
    ("Prudent",         15,  40, 200, 14, 50, 1.5,  3.0, 1000),
    ("Long Terme",      50, 100, 200, 14, 55, 4.0, 10.0, 1000),
    ("RSI Nerveux",      8,  21, 100,  7, 58, 1.2,  2.5, 1000),
    ("Equilibre 120",    6,  24, 120, 21, 52, 2.0,  4.5, 1000),
    ("Macro Tendance",  30,  80, 200, 21, 55, 5.0, 12.0, 1000),
]


def ensure_strategies():

    existing = {row[1] for row in get_strategies()}

    for s in STRATEGIES:
        if s[0] not in existing:
            create_strategy(*s)
            print("Strategie creee :", s[0])


def ema_series(closes, span):

    alpha = 2 / (span + 1)
    out = [0.0] * len(closes)
    e = closes[0]

    for i, c in enumerate(closes):
        e = c if i == 0 else alpha * c + (1 - alpha) * e
        out[i] = e

    return out


def rsi_series(closes, period):

    n = len(closes)
    out = [50.0] * n  # pandas fillna(50) sur les premieres valeurs

    gains = [0.0] * n
    losses = [0.0] * n

    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains[i] = d if d > 0 else 0.0
        losses[i] = -d if d < 0 else 0.0

    pg = [0.0] * (n + 1)
    pl = [0.0] * (n + 1)

    for i in range(n):
        pg[i + 1] = pg[i] + gains[i]
        pl[i + 1] = pl[i] + losses[i]

    for i in range(period - 1, n):
        mg = (pg[i + 1] - pg[i + 1 - period]) / period
        ml = (pl[i + 1] - pl[i + 1 - period]) / period
        rs = mg / (ml if ml != 0 else 1)
        out[i] = 100 - 100 / (1 + rs)

    return out


def fast_backtest(candles, ema_fast, ema_slow, ema_trend,
                  rsi_period, rsi_min, initial_capital,
                  stop_loss, take_profit):

    closes = [c[4] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    times = [c[0] for c in candles]

    ef = ema_series(closes, ema_fast)
    es = ema_series(closes, ema_slow)
    et = ema_series(closes, ema_trend)
    rv = rsi_series(closes, rsi_period)

    n = len(candles)

    capital = initial_capital
    position = 0.0
    entry_price = None
    trade = None
    trades = []
    wins = 0
    losses = 0

    equity = []
    bars_in_position = 0

    for i in range(1, n):

        prev_f = ef[i - 1]
        prev_s = es[i - 1]
        curr_f = ef[i]
        curr_s = es[i]

        price = closes[i]
        rsi_value = rv[i]
        trend_value = et[i]

        stop_hit = False
        tp_hit = False

        if position > 0:

            stop_price = entry_price * (1 - stop_loss / 100)
            tp_price = entry_price * (1 + take_profit / 100)

            if lows[i] <= stop_price:
                stop_hit = True
                price = stop_price
            elif highs[i] >= tp_price:
                tp_hit = True
                price = tp_price

        buy_signal = (
            prev_f <= prev_s
            and curr_f > curr_s
            and rsi_value > rsi_min
            and price > trend_value
        )

        sell_signal = prev_f >= prev_s and curr_f < curr_s

        if buy_signal and position == 0:

            quantity = capital * (1 - FEE) / price
            position = quantity
            entry_price = price

            trade = {
                "buy_time": times[i],
                "buy_price": round(price, 4),
                "capital_before": round(capital, 2),
            }
            capital = 0.0

        elif (sell_signal or stop_hit or tp_hit) and position > 0:

            capital = position * price * (1 - FEE)
            profit = capital - trade["capital_before"]

            if profit > 0:
                wins += 1
            else:
                losses += 1

            trades.append({
                **trade,
                "exit_reason": ("STOP_LOSS" if stop_hit
                                else "TAKE_PROFIT" if tp_hit
                                else "EMA_SELL"),
                "sell_time": times[i],
                "sell_price": round(price, 4),
                "capital_after": round(capital, 2),
                "profit": round(profit, 2),
                "profit_pct": round(
                    profit / trade["capital_before"] * 100, 2),
            })
            trade = None
            position = 0.0

        if position > 0:
            bars_in_position += 1
            equity.append(position * closes[i] * (1 - FEE))
        else:
            equity.append(capital)

    # Position finale valorisee au dernier cours (exit "FIN"),
    # comme dans backtest.py
    if position > 0 and trade is not None:

        capital = position * closes[-1] * (1 - FEE)
        profit = capital - trade["capital_before"]

        if profit > 0:
            wins += 1
        else:
            losses += 1

        trades.append({
            **trade,
            "exit_reason": "FIN",
            "sell_time": times[-1],
            "sell_price": round(closes[-1], 4),
            "capital_after": round(capital, 2),
            "profit": round(profit, 2),
            "profit_pct": round(
                profit / trade["capital_before"] * 100, 2),
        })
        position = 0.0

    peak = equity[0] if equity else initial_capital
    max_dd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = (peak - e) / peak
        if dd > max_dd:
            max_dd = dd

    return {
        "capital_end": round(capital, 2),
        "profit": round(capital - initial_capital, 2),
        "profit_pct": round(
            (capital - initial_capital) / initial_capital * 100, 2),
        "trades_count": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(trades) * 100, 2) if trades else 0,
        "max_dd_pct": round(max_dd * 100, 2),
        "exposure_pct": round(bars_in_position / (n - 1) * 100, 1),
        "trades": trades,
    }


def main():

    ensure_strategies()

    rows = db.execute(
        "SELECT timestamp, open, high, low, close, volume "
        "FROM candles WHERE symbol = 'BCHUSDT' ORDER BY timestamp"
    ).fetchall()

    candles = [(int(r[0]), float(r[1]), float(r[2]),
                float(r[3]), float(r[4]), float(r[5])) for r in rows]

    first = datetime.fromtimestamp(candles[0][0])
    last = datetime.fromtimestamp(candles[-1][0])
    years = (candles[-1][0] - candles[0][0]) / (365.25 * 24 * 3600)

    print(f"Backtest sur {len(candles)} bougies horaires "
          f"({first:%Y-%m-%d} -> {last:%Y-%m-%d}, {years:.1f} ans)\n")

    # Benchmark Buy & Hold
    bh_qty = 1000 * (1 - FEE) / candles[0][4]
    bh_end = bh_qty * candles[-1][4] * (1 - FEE)
    bh_pct = (bh_end - 1000) / 1000 * 100

    results = []
    for s in get_strategies():
        r = fast_backtest(
            candles,
            s[2], s[3], s[4], s[5], s[6], s[9], s[7], s[8],
        )
        r["id"] = s[0]
        r["name"] = s[1]
        results.append(r)
        print(f"  {s[1]:<16} fait")

    results.sort(key=lambda x: x["profit_pct"], reverse=True)

    # ------- rapport markdown -------
    lines = [
        "# Rapport de backtest BCH-USDT (1h)",
        "",
        f"- Periode : {first:%Y-%m-%d} -> {last:%Y-%m-%d} "
        f"({years:.1f} ans, {len(candles)} bougies)",
        "- Frais : 0.1% par transaction, capital initial 1000 USDT",
        "- Sortie : croisement EMA inverse, stop-loss, take-profit, "
        "ou FIN (valorisation au dernier cours)",
        "",
        "| Strategie | Profit % | Capital final | Trades | Win rate % "
        "| Max DD % | Exposition % |",
        "|---|---|---|---|---|---|---|",
    ]

    json_out = {
        "period": {"first": str(first), "last": str(last),
                   "candles": len(candles), "years": round(years, 2)},
        "buy_hold_pct": round(bh_pct, 2),
        "results": [],
    }

    for r in results:
        lines.append(
            f"| {r['name']} | {r['profit_pct']} | {r['capital_end']} "
            f"| {r['trades_count']} | {r['win_rate']} "
            f"| {r['max_dd_pct']} | {r['exposure_pct']} |"
        )
        json_out["results"].append(
            {k: v for k, v in r.items() if k != "trades"})

    lines.extend([
        f"| Buy & Hold | {round(bh_pct, 2)} | {round(bh_end, 2)} "
        f"| 1 | - | {round(bh_pct, 2)} | 100 |",
        "",
        "Legende : Max DD = perte maximale sur la courbe de valeur "
        "(mark-to-market) ; Exposition = part du temps passe en position.",
    ])

    with open("backtest_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    with open("backtest_results.json", "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)

    print("\n=== Resultats (tries par profit) ===")
    print(f"{'Strategie':<16} {'Profit%':>9} {'Capital':>10} "
          f"{'Trades':>7} {'Win%':>6} {'DD%':>6} {'Expo%':>6}")
    for r in results:
        print(f"{r['name']:<16} {r['profit_pct']:>9} "
              f"{r['capital_end']:>10} {r['trades_count']:>7} "
              f"{r['win_rate']:>6} {r['max_dd_pct']:>6} "
              f"{r['exposure_pct']:>6}")
    print(f"{'Buy & Hold':<16} {round(bh_pct, 2):>9} "
          f"{round(bh_end, 2):>10} {1:>7} {'-':>6} "
          f"{round(bh_pct, 2):>6} {100:>6}")
    print("\nRapport : backtest_report.md")


if __name__ == "__main__":
    main()
