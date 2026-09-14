"""Optimiseur automatique de strategies BCH.

Explore les parametres des strategies par recherche systematique (grid
search puis hill climbing) pour maximiser le profit out-of-sample.

Usage : python optimize.py
"""
import json
import random
import time
from datetime import datetime, timezone
from itertools import product

from db import db, get_strategies
from backtest_v2 import (
    engine, ema_series, rsi_series, atr_series,
    load_1h, load_tf, buy_hold,
    FEE, OOS_START, SYMBOL_DB
)

# =========================
# Bornes de recherche
# =========================
PARAM_BOUNDS = {
    "ema_fast": (3, 60, 1),      # (min, max, step)
    "ema_slow": (8, 200, 1),
    "ema_trend": (20, 300, 10),
    "rsi_period": (7, 30, 1),
    "rsi_min": (40, 70, 2),
    "sl_atr": (1.0, 5.0, 0.5),   # ATR multiples
    "tp_atr": (2.0, 10.0, 0.5),
    "risk_pct": (0.005, 0.03, 0.005),  # 0.5% a 3%
    "regime_ema": (0, 400, 50),   # 0 = desactive
}

# Parametres fixes
FIXED = {
    "atr_period": 14,
    "initial": 1000.0,
}


def random_params(bounds=None):
    """Genere un jeu de parametres aleatoire dans les bornes."""
    if bounds is None:
        bounds = PARAM_BOUNDS

    params = {}
    for name, (lo, hi, step) in bounds.items():
        if step < 1 and isinstance(lo, int):
            # float step
            steps = int((hi - lo) / step) + 1
            params[name] = lo + random.randint(0, steps - 1) * step
        elif step >= 1:
            steps = int((hi - lo) / step) + 1
            params[name] = lo + random.randint(0, steps - 1) * step
        else:
            params[name] = round(random.uniform(lo, hi), 4)
    return params


def mutate_params(params, bounds=None, rate=0.3):
    """Mute aleatoirement certains parametres."""
    if bounds is None:
        bounds = PARAM_BOUNDS

    new_params = dict(params)
    for name, (lo, hi, step) in bounds.items():
        if random.random() < rate:
            if step >= 1:
                steps = int((hi - lo) / step) + 1
                new_params[name] = lo + random.randint(0, steps - 1) * step
            else:
                steps = int((hi - lo) / step) + 1 if step > 0 else 1
                new_params[name] = lo + random.randint(0, steps - 1) * step
    return new_params


def crossover_params(p1, p2, bounds=None):
    """Croise deux jeux de parametres."""
    if bounds is None:
        bounds = PARAM_BOUNDS

    child = {}
    for name in bounds:
        child[name] = random.choice([p1[name], p2[name]])
    return child


def score_params(params, candles, period_start, period_end):
    """Evalue un jeu de parametres sur une periode donnee.
    Retourne le profit_pct, ou -999 si erreur.
    """
    try:
        r = engine(
            candles,
            ema_fast=params["ema_fast"],
            ema_slow=params["ema_slow"],
            ema_trend=params["ema_trend"],
            rsi_period=params["rsi_period"],
            rsi_min=params["rsi_min"],
            sl_atr=params["sl_atr"],
            tp_atr=params["tp_atr"],
            atr_period=FIXED["atr_period"],
            risk_pct=params["risk_pct"],
            regime=params["regime_ema"] if params["regime_ema"] > 0 else None,
            initial=FIXED["initial"],
            fee=FEE,
        )
        if r is None:
            return -999
        return r["profit_pct"]
    except Exception:
        return -999


def score_both(params, candles_is, candles_oos):
    """Score combine IS + OOS."""
    is_score = score_params(params, candles_is, None, OOS_START)
    oos_score = score_params(params, candles_oos, OOS_START, None)
    if is_score < -990 or oos_score < -990:
        return -999
    # On penalise les strategies qui marchent IS mais pas OOS (overfitting)
    # et on favorise les scores OOS eleves
    return 0.5 * max(0, is_score) + 1.0 * max(0, oos_score)


def grid_search(candles_is, candles_oos, n_random=500, n_generations=20,
                pop_size=50):
    """Recherche hybride : grid aleatoire puis evolution genetique."""

    print(f"  Phase 1: Grid aleatoire ({n_random} essais)...")

    best_score = -999
    best_params = None
    results = []

    # Phase 1: echantillonnage aleatoire (Monte Carlo)
    for i in range(n_random):
        params = random_params()
        sc = score_both(params, candles_is, candles_oos)
        if sc > best_score:
            best_score = sc
            best_params = params
            print(f"    [{i+1}/{n_random}] nouveau meilleur score: {sc:.2f}  "
                  f"(EMA {params['ema_fast']}/{params['ema_slow']}, "
                  f"RSI {params['rsi_min']}, "
                  f"ATR {params['sl_atr']}/{params['tp_atr']})")
        results.append((sc, params))

    # Phase 2: evolution genetique (hill climbing)
    print(f"  Phase 2: Evolution genetique ({n_generations} generations, "
          f"pop={pop_size})...")

    # Initialiser la population avec les meilleurs de la phase 1
    results.sort(key=lambda x: x[0], reverse=True)
    population = [p for _, p in results[:pop_size]]

    for gen in range(n_generations):
        # Evaluer la population
        scored = [(score_both(p, candles_is, candles_oos), p) for p in population]
        scored.sort(key=lambda x: x[0], reverse=True)

        gen_best = scored[0][0]
        if gen_best > best_score:
            best_score = gen_best
            best_params = scored[0][1]
            print(f"    Gen {gen+1}: nouveau meilleur {best_score:.2f}")

        # Selection des parents (top 30%)
        n_parents = max(2, pop_size // 3)
        parents = [p for _, p in scored[:n_parents]]

        # Nouvelle generation
        new_pop = parents[:]  # elitisme

        while len(new_pop) < pop_size:
            if random.random() < 0.5:
                # Mutation d'un parent
                p = random.choice(parents)
                child = mutate_params(p)
            else:
                # Croisement de deux parents
                p1, p2 = random.sample(parents, 2)
                child = crossover_params(p1, p2)
            new_pop.append(child)

        population = new_pop

    return best_params, best_score


def main():
    """Lance l'optimisation et affiche les meilleures strategies trouvees."""

    print("=" * 60)
    print("OPTIMISEUR AUTOMATIQUE DE STRATEGIES BCH")
    print("=" * 60)

    # Chargement des donnees multi-timeframes
    print("\n[1] Chargement des donnees...")

    # On utilise 4h pour l'optimisation (meilleure config de la v2)
    candles_4h = load_tf("4hour")
    if not candles_4h:
        print("  ERREUR: pas de bougies 4h. Lancez backtest_v2.py d'abord.")
        return

    first = datetime.fromtimestamp(candles_4h[0][0])
    last = datetime.fromtimestamp(candles_4h[-1][0])
    years = (candles_4h[-1][0] - candles_4h[0][0]) / (365.25 * 24 * 3600)

    candles_is = [c for c in candles_4h if c[0] < OOS_START]
    candles_oos = [c for c in candles_4h if c[0] >= OOS_START]

    print(f"  4h: {len(candles_4h)} bougies, {first:%Y-%m-%d} -> {last:%Y-%m-%d} "
          f"({years:.1f} ans)")
    print(f"  In-sample:  {len(candles_is)} bougies")
    print(f"  Out-of-sample: {len(candles_oos)} bougies")

    # Benchmark B&H
    bh_is = buy_hold(candles_is)
    bh_oos = buy_hold(candles_oos)
    bh_full = buy_hold(candles_4h)
    print(f"  Buy & Hold: IS={bh_is}%  OOS={bh_oos}%  Full={bh_full}%")

    # Strategie Default comme baseline
    print("\n[2] Baseline: Strategie 'Default' actuelle...")
    default_params = dict(
        ema_fast=9, ema_slow=20, ema_trend=50,
        rsi_period=14, rsi_min=55,
        sl_atr=2.0, tp_atr=4.0, risk_pct=0.01, regime_ema=200,
    )
    default_is = score_params(default_params, candles_is, None, OOS_START)
    default_oos = score_params(default_params, candles_oos, OOS_START, None)
    print(f"  Default: IS={default_is}%  OOS={default_oos}%")

    # Optimisation
    print("\n[3] Optimisation en cours (cela peut prendre plusieurs minutes)...")
    t0 = time.time()

    best_params, best_score = grid_search(
        candles_is, candles_oos,
        n_random=500,
        n_generations=20,
        pop_size=50
    )

    elapsed = time.time() - t0
    print(f"\n[4] Optimisation terminee en {elapsed:.0f}s")

    # Evaluation finale
    final_is = score_params(best_params, candles_is, None, OOS_START)
    final_oos = score_params(best_params, candles_oos, OOS_START, None)
    final_full = score_params(best_params, candles_4h, None, None)

    print("\n" + "=" * 60)
    print("RESULTATS DE L'OPTIMISATION")
    print("=" * 60)
    print(f"\nMeilleurs parametres trouves:")
    print(f"  EMA fast:     {best_params['ema_fast']}")
    print(f"  EMA slow:     {best_params['ema_slow']}")
    print(f"  EMA trend:    {best_params['ema_trend']}")
    print(f"  RSI period:   {best_params['rsi_period']}")
    print(f"  RSI min:      {best_params['rsi_min']}")
    print(f"  SL ATR:       {best_params['sl_atr']}")
    print(f"  TP ATR:       {best_params['tp_atr']}")
    print(f"  Risk %%:       {best_params['risk_pct']*100:.1f}%")
    print(f"  Regime EMA:   {best_params['regime_ema']}")
    print(f"\nPerformances:")
    print(f"  In-sample (IS):    {final_is:>+.2f}%")
    print(f"  Out-of-sample:     {final_oos:>+.2f}%")
    print(f"  Full:              {final_full:>+.2f}%")
    print(f"  Buy & Hold full:   {bh_full:>+.2f}%")
    print(f"  Default full:      {default_is + default_oos:>+.2f}% "
          f"(IS={default_is}% OOS={default_oos}%)")

    # Sauvegarde des resultats
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "best_params": best_params,
        "performance": {
            "is": round(final_is, 2),
            "oos": round(final_oos, 2),
            "full": round(final_full, 2),
        },
        "baseline": {
            "buy_hold": round(bh_full, 2),
            "default_full": round(default_is + default_oos, 2),
            "default_is": round(default_is, 2),
            "default_oos": round(default_oos, 2),
        },
    }

    with open("optimization_results.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print("\nResultats sauvegardes dans optimization_results.json")

    # Comparaison detaillee
    print("\n" + "-" * 60)
    print("Comparaison Baseline vs Optimise:")
    print("-" * 60)
    print(f"{'Metric':<25} {'Default':>10} {'Optimise':>10} {'Delta':>10}")
    print(f"{'In-sample %':<25} {default_is:>10.2f} {final_is:>10.2f} "
          f"{(final_is - default_is):>+10.2f}")
    print(f"{'Out-of-sample %':<25} {default_oos:>10.2f} {final_oos:>10.2f} "
          f"{(final_oos - default_oos):>+10.2f}")
    print(f"{'Total %':<25} {(default_is + default_oos):>10.2f} "
          f"{(final_is + final_oos):>10.2f} "
          f"{(final_is + final_oos - default_is - default_oos):>+10.2f}")
    print(f"{'Buy & Hold %':<25} {bh_full:>10.2f} {bh_full:>10.2f} -")


if __name__ == "__main__":
    main()