# Etude backtest v2 - BCH-USDT

Meme signal d'entree que l'application ; on isole l'effet du timeframe, du regime, des stops ATR et du sizing. Trading au comptant, frais 0.1%, risque 1% du capital par trade quand actif. In-sample : 2020-11 -> 2023-12 ; out-of-sample : 2024 -> aujourd'hui. Genere le 2026-09-13 22:16 UTC.

| Configuration | Mediane % | Meilleure strategie | Trades (med) | DD med % |
|---|---|---|---|---|
| A. 1h  avant (SL/TP %, all-in) | -41.86 | Tendance 200 (11.88%) | 386 | 59.16 |
| B. 4h  avant (SL/TP %, all-in) | -9.7 | Macro Tendance (63.92%) | 87 | 37.6 |
| C. 1h  + regime EMA200 | -41.86 | Tendance 200 (11.88%) | 325 | 59.16 |
| D. 4h  + ATR + risque 1% | 11.13 | Equilibre 120 (16.5%) | 87 | 9.56 |
| E. 4h  + ATR + risque 1% + regime | 11.67 | Default (26.61%) | 75 | 7.72 |
| F. 1d  + ATR + risque 1% + regime | -1.29 | Scalp Rapide (5.11%) | 15 | 5.05 |

Buy & Hold complet : 1h -9.7%, 4h -10.59%, 1d -11.4%

## Detail E. 4h  + ATR + risque 1% + regime (mediane OOS la plus elevee)

| Strategie | IS % | OOS % | Complet % | DD max % | Trades | Expo % |
|---|---|---|---|---|---|---|
| Default | 10.41 | 15.02 | 26.61 | 3.73 | 80 | 8.0 |
| Scalp Rapide | -7.51 | -7.46 | -15.34 | 23.63 | 147 | 8.7 |
| Turbo | -7.95 | -4.19 | -13.06 | 23.16 | 176 | 8.0 |
| Swing Classique | 0.6 | 11.98 | 14.75 | 5.45 | 73 | 8.8 |
| Tendance 200 | -0.22 | 12.6 | 9.94 | 7.72 | 58 | 7.8 |
| Momentum Fort | 6.3 | 6.94 | 15.79 | 5.46 | 44 | 5.5 |
| Prudent | 2.5 | 8.08 | 11.67 | 8.23 | 77 | 9.5 |
| Long Terme | 1.16 | -7.17 | -3.25 | 11.15 | 44 | 6.9 |
| RSI Nerveux | -0.92 | 10.54 | 9.19 | 8.42 | 104 | 9.9 |
| Equilibre 120 | 4.11 | 9.67 | 12.99 | 5.75 | 75 | 7.0 |
| Macro Tendance | 6.03 | 4.48 | 14.13 | 7.56 | 53 | 7.1 |