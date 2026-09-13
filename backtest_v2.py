"""Etude backtest v2 : timeframe, filtre de regime, stops ATR, sizing.

Applique les recommandations post-mortem du premier comparatif, en
gardant le MEME signal d'entree (croisement EMA + RSI + tendance)
pour isoler l'effet de chaque amelioration :

  1. timeframes plus hauts (4h, 1d) -> moins de bruit et de frais
  2. filtre de regime (prix > EMA200 de la periode tradee)
  3. stops/targets en ATR (adaptes a la volatilite) au lieu de % fixes
  4. sizing a risque fixe (1% du capital par trade) au lieu de all-in
  5. validation in-sample (2020-2023) / out-of-sample (2024+)

Les bougies 4h et 1d sont stockees dans la table candles_multi
(la table `candles` 1h de l'application n'est pas modifiee).
Le trading reste au comptant : jamais plus de capital que l'equite.

Usage : python backtest_v2.py
"""
import time
from datetime import datetime, timezone

from db import db, get_strategies
from kucoin import fetch_chunk

FEE = 0.001
SYMBOL_API = "BCH-USDT"
SYMBOL_DB = "BCHUSDT"
OOS_START = 1704067200  # 2024-01-01 00:00 UTC

TIMEFRAMES = {"1hour": 3600, "4hour": 14400, "1day": 86400}


# =========================
# Donnees multi-timeframes
# =========================
def ensure_table():

    db.execute("""
        CREATE TABLE IF NOT EXISTS candles_multi (
            timestamp BIGINT,
            symbol VARCHAR,
            timeframe VARCHAR,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            PRIMARY KEY(timestamp, symbol, timeframe)
        )
    """)
    db.commit()


def update_tf(timeframe, tf_seconds, target):

    last = db.execute(
        "SELECT MAX(timestamp) FROM candles_multi "
        "WHERE symbol = ? AND timeframe = ?",
        [SYMBOL_DB, timeframe]
    ).fetchone()[0]

    now = int(time.time())
    start_at = last if last else now - target * tf_seconds

    collected = {}
    end_at = now
    n_req = 0

    while True:

        window_start = max(end_at - 1500 * tf_seconds, start_at)
        data = fetch_chunk(SYMBOL_API, timeframe, window_start, end_at)
        n_req += 1

        if data is None:
            break

        for row in data:
            ts = int(row[0])
            if ts >= start_at:
                collected[ts] = row

        if len(data) == 0 or window_start <= start_at:
            break

        end_at = min(int(r[0]) for r in data) - 1
        time.sleep(0.2)

    values = [
        (int(r[0]), SYMBOL_DB, timeframe, float(r[1]), float(r[3]),
         float(r[4]), float(r[2]), float(r[5]))
        for r in (collected[ts] for ts in sorted(collected))
    ]

    if values:
        db.executemany(
            "INSERT OR REPLACE INTO candles_multi VALUES (?,?,?,?,?,?,?,?)",
            values
        )
        db.commit()

    return len(values), n_req


def load_tf(timeframe):

    rows = db.execute(
        "SELECT timestamp, open, high, low, close, volume "
        "FROM candles_multi WHERE symbol = ? AND timeframe = ? "
        "ORDER BY timestamp",
        [SYMBOL_DB, timeframe]
    ).fetchall()

    return [(int(r[0]), float(r[1]), float(r[2]),
             float(r[3]), float(r[4]), float(r[5])) for r in rows]


def load_1h():

    rows = db.execute(
        "SELECT timestamp, open, high, low, close, volume "
        "FROM candles WHERE symbol = ? ORDER BY timestamp",
        [SYMBOL_DB]
    ).fetchall()

    return [(int(r[0]), float(r[1]), float(r[2]),
             float(r[3]), float(r[4]), float(r[5])) for r in rows]


# =========================
# Indicateurs (listes)
# =========================
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
    out = [50.0] * n

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


def atr_series(highs, lows, closes, period):

    n = len(closes)
    tr = [highs[0] - lows[0]] + [0.0] * (n - 1)

    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )

    pt = [0.0] * (n + 1)
    for i in range(n):
        pt[i + 1] = pt[i] + tr[i]

    out = [None] * n
    for i in range(period - 1, n):
        out[i] = (pt[i + 1] - pt[i + 1 - period]) / period

    return out


# =========================
# Moteur v2
# =========================
def engine(candles, *, ema_fast, ema_slow, ema_trend, rsi_period, rsi_min,
           sl_pct=None, tp_pct=None, sl_atr=None, tp_atr=None, atr_period=14,
           risk_pct=None, regime=None, initial=1000.0, fee=FEE):
    """Meme logique de signal que l'application, avec en plus :
    stops en ATR, sizing a risque fixe, filtre de regime.
    Trading au comptant : la position ne depasse jamais l'equite.
    """

    n = len(candles)
    if n < max(ema_slow, ema_trend) + 2:
        return None

    closes = [c[4] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    times = [c[0] for c in candles]

    ef = ema_series(closes, ema_fast)
    es = ema_series(closes, ema_slow)
    et = ema_series(closes, ema_trend)
    rv = rsi_series(closes, rsi_period)
    atrs = atr_series(highs, lows, closes, atr_period) if sl_atr else None
    reg = ema_series(closes, regime) if regime else None

    cash = initial
    qty = 0.0
    entry_price = None
    stop_price = None
    target_price = None
    alloc = 0.0

    trades = 0
    wins = 0
    curve = []
    bars_in = 0

    def close_position(price, i, reason):

        nonlocal cash, qty, trades, wins

        proceeds = qty * price * (1 - fee)
        profit = proceeds - alloc
        if profit > 0:
            wins += 1
        trades += 1
        cash += proceeds
        qty = 0.0

    for i in range(1, n):

        price = closes[i]

        # --- gestion de position ouverte ---
        if qty > 0:

            exit_price = None

            if lows[i] <= stop_price:
                exit_price = stop_price
            elif highs[i] >= target_price:
                exit_price = target_price
            elif ef[i - 1] >= es[i - 1] and ef[i] < es[i]:
                exit_price = price  # croisement EMA inverse

            if exit_price is not None:
                close_position(exit_price, i, None)

        # --- signal d'entree (identique a l'application) ---
        if qty == 0.0:

            buy = (
                ef[i - 1] <= es[i - 1]
                and ef[i] > es[i]
                and rv[i] > rsi_min
                and price > et[i]
                and (reg is None or price > reg[i])
            )

            if buy:

                can_enter = True

                if sl_atr is not None:
                    if not atrs[i]:
                        can_enter = False  # ATR pas encore calculable
                    else:
                        stop_price = price - sl_atr * atrs[i]
                        target_price = price + tp_atr * atrs[i]
                else:
                    stop_price = price * (1 - sl_pct / 100)
                    target_price = price * (1 + tp_pct / 100)

                if can_enter:

                    if stop_price <= 0:
                        stop_price = price * 0.5

                    stop_dist = price - stop_price

                    if risk_pct is not None:
                        # risque fixe : allocation telle que si le stop est
                        # touche, la perte vaut environ risk_pct * cash
                        # (plafonnee au comptant : jamais plus que l'equite)
                        alloc = min(
                            cash,
                            risk_pct * cash * price / (stop_dist * (1 - fee))
                        )
                    else:
                        alloc = cash

                    qty = alloc * (1 - fee) / price
                    cash -= alloc
                    entry_price = price

        if qty > 0:
            bars_in += 1
            curve.append(cash + qty * price * (1 - fee))
        else:
            curve.append(cash)

    if qty > 0:
        close_position(closes[-1], n - 1, None)
        curve[-1] = cash

    peak = curve[0] if curve else initial
    max_dd = 0.0
    for e in curve:
        if e > peak:
            peak = e
        dd = (peak - e) / peak
        if dd > max_dd:
            max_dd = dd

    return {
        "equity_end": round(cash, 2),
        "profit_pct": round((cash - initial) / initial * 100, 2),
        "trades": trades,
        "win_rate": round(wins / trades * 100, 1) if trades else 0,
        "max_dd_pct": round(max_dd * 100, 2),
        "exposure_pct": round(bars_in / (n - 1) * 100, 1),
    }


def buy_hold(candles):

    if len(candles) < 2:
        return 0.0
    return round((candles[-1][4] / candles[0][4] - 1) * 100, 2)


def median(values):

    if not values:
        return 0.0
    s = sorted(values)
    m = len(s) // 2
    return s[m] if len(s) % 2 else round((s[m - 1] + s[m]) / 2, 2)


# =========================
# Etude
# =========================
CONFIGS = [
    # (libelle, timeframe, options moteur)
    ("A. 1h  avant (SL/TP %, all-in)", "1hour", {}),
    ("B. 4h  avant (SL/TP %, all-in)", "4hour", {}),
    ("C. 1h  + regime EMA200",         "1hour", dict(regime=200)),
    ("D. 4h  + ATR + risque 1%",       "4hour",
     dict(sl_atr=2.0, tp_atr=4.0, risk_pct=0.01)),
    ("E. 4h  + ATR + risque 1% + regime", "4hour",
     dict(sl_atr=2.0, tp_atr=4.0, risk_pct=0.01, regime=200)),
    ("F. 1d  + ATR + risque 1% + regime", "1day",
     dict(sl_atr=2.0, tp_atr=4.0, risk_pct=0.01, regime=200)),
]

PERIODS = [
    ("complet", None, None),
    ("in-sample 2020-2023", None, OOS_START),
    ("out-of-sample 2024+", OOS_START, None),
]


def slice_candles(candles, start=None, end=None):

    return [
        c for c in candles
        if (start is None or c[0] >= start)
        and (end is None or c[0] < end)
    ]


def main():

    ensure_table()

    for tf in ("4hour", "1day"):
        inserted, n_req = update_tf(tf, TIMEFRAMES[tf], 20000)
        print(f"[data] {tf} : {inserted} bougies ({n_req} requetes)")

    data = {"1hour": load_1h()}
    data["4hour"] = load_tf("4hour")
    data["1day"] = load_tf("1day")

    strategies = get_strategies()
    print(f"[data] 1h={len(data['1hour'])} 4h={len(data['4hour'])} "
          f"1d={len(data['1day'])} strategies={len(strategies)}\n")

    # resultats[config][periode] = liste de dicts
    results = {label: {} for label, _, _ in CONFIGS}

    for label, tf, extra in CONFIGS:

        for pname, pstart, pend in PERIODS:

            candles = slice_candles(data[tf], pstart, pend)
            rows = []

            for st in strategies:

                params = dict(
                    ema_fast=st[2], ema_slow=st[3], ema_trend=st[4],
                    rsi_period=st[5], rsi_min=st[6],
                    sl_pct=None, tp_pct=None, sl_atr=None, tp_atr=None,
                    risk_pct=None, regime=None,
                )
                # configs A/B/C : SL/TP en % propres a la strategie
                if "sl_atr" not in extra:
                    params.update(sl_pct=st[7], tp_pct=st[8])
                params.update(extra)

                r = engine(candles, **params)
                if r:
                    r["name"] = st[1]
                    rows.append(r)

            results[label][pname] = {
                "candles": len(candles),
                "buy_hold": buy_hold(candles),
                "rows": rows,
            }

        print(f"[done] {label}")

    # =========================
    # Synthese
    # =========================
    print("\n=== PERIODE COMPLETE : synthese par configuration ===")
    print(f"{'Configuration':<38} {'mediane%':>9} {'meilleure strategie':<22} "
          f"{'trades':>7} {'DD%':>6}")

    summary = []
    for label, _, _ in CONFIGS:
        rows = results[label]["complet"]["rows"]
        best = max(rows, key=lambda r: r["profit_pct"])
        med = median([r["profit_pct"] for r in rows])
        med_tr = median([r["trades"] for r in rows])
        med_dd = median([r["max_dd_pct"] for r in rows])
        summary.append((label, med, best, med_tr, med_dd))
        print(f"{label:<38} {med:>9} {best['name']:<22} "
              f"{med_tr:>7} {med_dd:>6}")

    print(f"\n{'Buy & Hold':<38} "
          f"1h: {results['A. 1h  avant (SL/TP %, all-in)']['complet']['buy_hold']}%  "
          f"4h: {results['B. 4h  avant (SL/TP %, all-in)']['complet']['buy_hold']}%  "
          f"1d: {results['F. 1d  + ATR + risque 1% + regime']['complet']['buy_hold']}%")

    # meilleure config : mediane out-of-sample la plus elevee
    best_label = max(
        (label for label, _, _ in CONFIGS),
        key=lambda l: median(
            r["profit_pct"] for r in results[l]["out-of-sample 2024+"]["rows"])
    )

    print(f"\n=== MEILLEURE CONFIG (mediane OOS) : {best_label} ===")
    print(f"{'Strategie':<18} {'IS%':>8} {'OOS%':>8} {'COMPLET%':>9} "
          f"{'DD%':>6} {'trades':>7} {'expo%':>6}")

    oos_rows = {r["name"]: r for r in results[best_label]["out-of-sample 2024+"]["rows"]}
    is_rows = {r["name"]: r for r in results[best_label]["in-sample 2020-2023"]["rows"]}

    detail = []
    for r in results[best_label]["complet"]["rows"]:
        name = r["name"]
        is_r = is_rows.get(name, {})
        oos_r = oos_rows.get(name, {})
        row = {
            "name": name,
            "is": is_r.get("profit_pct"),
            "oos": oos_r.get("profit_pct"),
            "full": r["profit_pct"],
            "dd": r["max_dd_pct"],
            "trades": r["trades"],
            "expo": r["exposure_pct"],
        }
        detail.append(row)
        print(f"{name:<18} {str(row['is']):>8} {str(row['oos']):>8} "
              f"{row['full']:>9} {row['dd']:>6} {row['trades']:>7} "
              f"{row['expo']:>6}")

    # =========================
    # Rapport markdown
    # =========================
    lines = [
        "# Etude backtest v2 - BCH-USDT",
        "",
        "Meme signal d'entree que l'application ; on isole l'effet du "
        "timeframe, du regime, des stops ATR et du sizing. Trading au "
        "comptant, frais 0.1%, risque 1% du capital par trade quand "
        "actif. In-sample : 2020-11 -> 2023-12 ; out-of-sample : 2024 -> "
        f"aujourd'hui. Genere le "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.",
        "",
        "| Configuration | Mediane % | Meilleure strategie | Trades (med) "
        "| DD med % |",
        "|---|---|---|---|---|",
    ]
    for label, med, best, med_tr, med_dd in summary:
        lines.append(f"| {label} | {med} | {best['name']} ({best['profit_pct']}%) "
                     f"| {med_tr} | {med_dd} |")

    lines += [
        "",
        f"Buy & Hold complet : 1h "
        f"{results['A. 1h  avant (SL/TP %, all-in)']['complet']['buy_hold']}%, "
        f"4h {results['B. 4h  avant (SL/TP %, all-in)']['complet']['buy_hold']}%, "
        f"1d {results['F. 1d  + ATR + risque 1% + regime']['complet']['buy_hold']}%",
        "",
        f"## Detail {best_label} (mediane OOS la plus elevee)",
        "",
        "| Strategie | IS % | OOS % | Complet % | DD max % | Trades | Expo % |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in detail:
        lines.append(
            f"| {row['name']} | {row['is']} | {row['oos']} | {row['full']} "
            f"| {row['dd']} | {row['trades']} | {row['expo']} |")

    with open("backtest_v2_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("\nRapport : backtest_v2_report.md")


if __name__ == "__main__":
    main()
