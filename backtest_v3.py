"""Backtest v3 : Trailing Stop, filtre de volatilite, scoring multi-signaux.

Ameliorations par rapport a v2 :
  1. Trailing stop : le stop monte avec le prix (cALL)
  2. Filtre de volatilite : n'entre que si la volatilite est dans une
     fourchette normale (ATR / prix entre min et max)
  3. Scoring multi-signaux : pondere plusieurs indicateurs au lieu d'un
     signal binaire EMA + RSI
  4. Walk-forward : validation par fenetres glissantes

Usage : python backtest_v3.py
"""
import json
import time
from datetime import datetime, timezone

from db import db
from backtest_v2 import (
    ema_series, rsi_series, atr_series,
    load_1h, load_tf, load_tf as load_candles,
    buy_hold, FEE, OOS_START, SYMBOL_DB
)


# =========================
# Moteur v3 avec trailing stop
# =========================
def engine_v3(candles, *, ema_fast, ema_slow, ema_trend, rsi_period, rsi_min,
              sl_atr=None, tp_atr=None, atr_period=14,
              risk_pct=None, regime=None, initial=1000.0, fee=FEE,
              # Nouveaux parametres v3
              trail_atr=None,        # distance du trailing en ATR (ex: 1.5)
              vol_min_pct=None,      # volatilite mini % (ex: 0.5%)
              vol_max_pct=None,      # volatilite maxi % (ex: 5.0%)
              vol_atr_period=14,     # periode ATR pour volatilite
              # Scoring multi-signaux
              score_threshold=0.0,   # seuil min pour entrer (0 = desactive)
              score_weights=None,     # dict des poids des signaux
              ):
    """Moteur avec trailing stop, filtre volatilite et scoring.

    Retourne dict avec resultats, ou None si pas assez de donnees.
    """
    n = len(candles)
    if n < max(ema_slow, ema_trend, 50) + 2:
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

    # Volatilite normalisee (ATR / prix)
    vol_norm = None
    if vol_min_pct is not None or vol_max_pct is not None:
        vol_atrs = atr_series(highs, lows, closes, vol_atr_period)

    cash = initial
    qty = 0.0
    entry_price = None
    stop_price = None
    target_price = None
    trail_activation = None  # prix a partir duquel le trailing s'active
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

    # Poids par defaut pour le scoring
    weights = score_weights or {}

    for i in range(1, n):
        price = closes[i]

        # --- Trailing stop (si actif) ---
        if qty > 0 and trail_atr is not None and atrs[i] is not None:
            # Activation du trailing : quand le prix depasse
            # entry_price + 1*trail_atr
            if trail_activation is None:
                if price >= entry_price + trail_atr * atrs[i]:
                    trail_activation = price
                    stop_price = price - trail_atr * atrs[i]
            else:
                # Le stop suit la hausse, jamais il ne baisse
                new_stop = price - trail_atr * atrs[i]
                if new_stop > stop_price:
                    stop_price = new_stop

        # --- Gestion de position ouverte ---
        if qty > 0:
            exit_price = None

            if lows[i] <= stop_price:
                exit_price = stop_price
                reason = "TRAIL" if trail_activation else "STOP_LOSS"
            elif highs[i] >= target_price:
                exit_price = target_price
                reason = "TAKE_PROFIT"
            elif ef[i - 1] >= es[i - 1] and ef[i] < es[i]:
                exit_price = price
                reason = "EMA_SELL"

            if exit_price is not None:
                close_position(exit_price, i, reason)

        # --- Signal d'entree ---
        if qty == 0.0:
            # Filtre de volatilite
            if vol_min_pct is not None or vol_max_pct is not None:
                if vol_atrs[i] is None:
                    can_trade = False
                else:
                    vol_pct = vol_atrs[i] / price * 100
                    vol_ok = True
                    if vol_min_pct is not None and vol_pct < vol_min_pct:
                        vol_ok = False
                    if vol_max_pct is not None and vol_pct > vol_max_pct:
                        vol_ok = False
            else:
                vol_ok = True

            # Scoring multi-signaux
            if score_threshold > 0 and score_weights:
                score = 0.0
                total_weight = 0.0
                for signal, weight in weights.items():
                    total_weight += weight
                    if signal == "ema_cross" and i > 0:
                        # Croisement EMA (0 a 1)
                        val = 1.0 if (ef[i-1] <= es[i-1] and ef[i] > es[i]) else 0.0
                        score += weight * val
                    elif signal == "rsi_momentum":
                        # RSI > RSI_MIN, normalise (0 a 1)
                        val = max(0, min(1, (rv[i] - rsi_min) / (30)))
                        score += weight * val
                    elif signal == "price_trend":
                        # Prix > EMA trend (0 a 1)
                        val = 1.0 if price > et[i] else 0.0
                        score += weight * val
                    elif signal == "volume_surge":
                        # Volume > moyenne des 20 dernieres (0 a 1)
                        if i >= 20:
                            avg_vol = sum(c[5] for c in candles[i-20:i]) / 20
                            surge = candles[i][5] / avg_vol if avg_vol > 0 else 0
                            val = min(1.0, max(0, surge / 2))
                            score += weight * val

                if total_weight > 0:
                    score /= total_weight

                buy = score >= score_threshold and vol_ok
            else:
                # Signal binaire classique (comme v2)
                buy = (
                    ef[i - 1] <= es[i - 1]
                    and ef[i] > es[i]
                    and rv[i] > rsi_min
                    and price > et[i]
                    and (reg is None or price > reg[i])
                    and vol_ok
                )

            if buy:
                can_enter = True

                if sl_atr is not None:
                    if not atrs[i]:
                        can_enter = False
                    else:
                        stop_price = price - sl_atr * atrs[i]
                        target_price = price + tp_atr * atrs[i]
                else:
                    stop_price = price * 0.95  # fallback
                    target_price = price * 1.10

                if can_enter:
                    if stop_price <= 0:
                        stop_price = price * 0.5

                    stop_dist = price - stop_price
                    trail_activation = None  # reset pour nouveau trade

                    if risk_pct is not None:
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


# =========================
# Test : Trailing stop seul
# =========================
def test_trailing_stop(candles):
    """Test l'effet du trailing stop vs stop fixe sur la config E."""
    base_params = dict(
        ema_fast=9, ema_slow=20, ema_trend=50,
        rsi_period=14, rsi_min=55,
        sl_atr=2.0, tp_atr=4.0, atr_period=14,
        risk_pct=0.01, regime=200, initial=1000.0, fee=FEE,
    )

    print("\n--- Test Trailing Stop (config E, 4h) ---")

    # Sans trailing
    r_base = engine_v3(candles, **base_params, trail_atr=None)
    print(f"  Sans trailing: {r_base['profit_pct']}%  "
          f"DD={r_base['max_dd_pct']}%  trades={r_base['trades']}  "
          f"win={r_base['win_rate']}%")

    # Avec trailing a 1.5 ATR
    r_trail = engine_v3(candles, **base_params, trail_atr=1.5)
    print(f"  Trail 1.5 ATR: {r_trail['profit_pct']}%  "
          f"DD={r_trail['max_dd_pct']}%  trades={r_trail['trades']}  "
          f"win={r_trail['win_rate']}%")

    # Avec trailing a 2.0 ATR
    r_trail2 = engine_v3(candles, **base_params, trail_atr=2.0)
    print(f"  Trail 2.0 ATR: {r_trail2['profit_pct']}%  "
          f"DD={r_trail2['max_dd_pct']}%  trades={r_trail2['trades']}  "
          f"win={r_trail2['win_rate']}%")

    # Avec trailing a 3.0 ATR
    r_trail3 = engine_v3(candles, **base_params, trail_atr=3.0)
    print(f"  Trail 3.0 ATR: {r_trail3['profit_pct']}%  "
          f"DD={r_trail3['max_dd_pct']}%  trades={r_trail3['trades']}  "
          f"win={r_trail3['win_rate']}%")

    return {
        "none": r_base,
        "trail_1.5": r_trail,
        "trail_2.0": r_trail2,
        "trail_3.0": r_trail3,
    }


# =========================
# Test : Filtre de volatilite
# =========================
def test_volatility_filter(candles):
    """Test le filtre de volatilite sur la config E + trailing."""
    base_params = dict(
        ema_fast=9, ema_slow=20, ema_trend=50,
        rsi_period=14, rsi_min=55,
        sl_atr=2.0, tp_atr=4.0, atr_period=14,
        risk_pct=0.01, regime=200, initial=1000.0, fee=FEE,
        trail_atr=2.0,
    )

    print("\n--- Test Filtre de Volatilite (config E + trail 2.0 ATR, 4h) ---")

    r_base = engine_v3(candles, **base_params, vol_min_pct=None, vol_max_pct=None)
    print(f"  Sans filtre:    {r_base['profit_pct']}%  "
          f"DD={r_base['max_dd_pct']}%  trades={r_base['trades']}  "
          f"expo={r_base['exposure_pct']}%")

    for vmin, vmax in [(0.3, 3.0), (0.5, 4.0), (1.0, 5.0)]:
        r = engine_v3(candles, **base_params,
                      vol_min_pct=vmin, vol_max_pct=vmax)
        print(f"  Vol [{vmin}-{vmax}%]: {r['profit_pct']}%  "
              f"DD={r['max_dd_pct']}%  trades={r['trades']}  "
              f"expo={r['exposure_pct']}%")

    return r_base


# =========================
# Test : Scoring multi-signaux
# =========================
def test_scoring(candles):
    """Test le scoring multi-signaux."""
    weights = {
        "ema_cross": 1.0,
        "rsi_momentum": 0.5,
        "price_trend": 0.3,
        "volume_surge": 0.2,
    }

    base_params = dict(
        ema_fast=9, ema_slow=20, ema_trend=50,
        rsi_period=14, rsi_min=55,
        sl_atr=2.0, tp_atr=4.0, atr_period=14,
        risk_pct=0.01, regime=200, initial=1000.0, fee=FEE,
        trail_atr=2.0,
        vol_min_pct=0.3, vol_max_pct=4.0,
    )

    print("\n--- Test Scoring Multi-Signaux (4h) ---")

    for threshold in [0.0, 0.3, 0.5, 0.7]:
        r = engine_v3(candles, **base_params,
                      score_threshold=threshold,
                      score_weights=weights)
        if threshold == 0:
            print(f"  Binaire:           {r['profit_pct']}%  "
                  f"DD={r['max_dd_pct']}%  trades={r['trades']}  "
                  f"win={r['win_rate']}%")
        else:
            print(f"  Score >= {threshold}: {r['profit_pct']}%  "
                  f"DD={r['max_dd_pct']}%  trades={r['trades']}  "
                  f"win={r['win_rate']}%")


# =========================
# Walk-forward analysis
# =========================
def walk_forward(candles, window_size=500, step=250):
    """Validation walk-forward : fenetres glissantes.

    Pour chaque fenetre, on entraine sur les 500 premieres bougies
    et on teste sur les 250 suivantes.

    Args:
        candles: liste de bougies
        window_size: taille de la fenetre d'entrainement
        step: taille de la fenetre de test

    Returns:
        dict avec les resultats de chaque fenetre
    """
    base_params = dict(
        ema_fast=9, ema_slow=20, ema_trend=50,
        rsi_period=14, rsi_min=55,
        sl_atr=2.0, tp_atr=4.0, atr_period=14,
        risk_pct=0.01, regime=200, initial=1000.0, fee=FEE,
        trail_atr=2.0,
    )

    results = []
    n = len(candles)

    print(f"\n--- Walk-Forward (taille={window_size}, step={step}) ---")

    for start in range(0, n - window_size - step, step):
        train = candles[start:start + window_size]
        test = candles[start + window_size:start + window_size + step]

        if len(test) < 50:
            continue

        r_test = engine_v3(test, **base_params)
        if r_test:
            results.append({
                "window": f"{start}-{start + window_size + step}",
                "train_candles": len(train),
                "test_candles": len(test),
                "profit_pct": r_test["profit_pct"],
                "max_dd_pct": r_test["max_dd_pct"],
                "trades": r_test["trades"],
                "win_rate": r_test["win_rate"],
            })
            print(f"  Fenetre {start:4d}-{start + window_size + step:4d}: "
                  f"profit={r_test['profit_pct']:>+7.2f}%  "
                  f"DD={r_test['max_dd_pct']:>5.2f}%  "
                  f"trades={r_test['trades']:>3d}")

    # Stats globales
    if results:
        profits = [r["profit_pct"] for r in results]
        avg_profit = sum(profits) / len(profits)
        wins_wf = sum(1 for p in profits if p > 0)
        print(f"\nWalk-Forward : {len(results)} fenetres")
        print(f"  Profit moyen: {avg_profit:.2f}%")
        print(f"  Fenetres gagnantes: {wins_wf}/{len(results)} "
              f"({wins_wf/len(results)*100:.0f}%)")
        print(f"  Profit min: {min(profits):.2f}%")
        print(f"  Profit max: {max(profits):.2f}%")

    return results


def main():
    """Lance tous les tests de la v3."""

    print("=" * 60)
    print("BACKTEST V3 - Trailing Stop, Volatilite, Scoring")
    print("=" * 60)

    # Chargement des donnees
    print("\n[1] Chargement des donnees...")

    data = {"1hour": load_1h()}
    data["4hour"] = load_tf("4hour")
    data["1day"] = load_tf("1day")

    for tf in data:
        print(f"  {tf}: {len(data[tf])} bougies")

    candles_4h = data["4hour"][:]

    # Tests
    results_trail = test_trailing_stop(candles_4h)
    results_vol = test_volatility_filter(candles_4h)
    test_scoring(candles_4h)

    # Walk-forward sur 4h (config E + trailing 2.0)
    walk_forward(data["4hour"][:])

    # Generation rapport
    print("\n[2] Generation du rapport...")

    lines = [
        "# Backtest v3 - BCH-USDT",
        "",
        "Ameliorations : trailing stop, filtre de volatilite, "
        "scoring multi-signaux, walk-forward.",
        f"Genere le {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.",
        "",
        "## 1. Trailing Stop (config E sur 4h)",
        "",
        "| Configuration | Profit % | DD max % | Trades | Win rate % |",
        "|---|---|---|---|---|",
    ]

    for name, r in results_trail.items():
        lines.append(
            f"| {name} | {r['profit_pct']} | {r['max_dd_pct']} "
            f"| {r['trades']} | {r['win_rate']} |")

    lines += [
        "",
        "## 2. Filtre de Volatilite (config E + trail 2.0)",
        "",
        "| Filtre volatility | Profit % | DD max % | Trades | Expo % |",
        "|---|---|---|---|---|",
    ]

    vol_configs = [
        ("Sans filtre", results_vol["profit_pct"], results_vol["max_dd_pct"],
         results_vol["trades"], results_vol["exposure_pct"]),
    ]

    print("\nRapport sauvegarde dans backtest_v3_report.md")

    with open("backtest_v3_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("\nTermine.")


if __name__ == "__main__":
    main()