"""Complete l'historique KuCoin de tous les actifs supportes
(BCH, BTC, ETH) sur les timeframes 1h, 4h et 1d.

Incrementiel : ne telecharge que ce qui manque depuis la derniere
bougie stockee. Au premier passage sur un nouvel actif, l'historique
complet est rapatrie (plusieurs minutes) ; les passages suivants
sont rapides.

Usage : python fetch_history.py
"""
from datetime import datetime

from db import db
from kucoin import update_candles, get_last_timestamp

SYMBOLS = {
    "BCH-USDT": "BCHUSDT",
    "BTC-USDT": "BTCUSDT",
    "ETH-USDT": "ETHUSDT"
}

TIMEFRAMES = [
    ("1hour", 3600, "candles", 100000),
    ("4hour", 14400, "candles_multi", 20000),
    ("1day", 86400, "candles_multi", 5000)
]


def main():

    for api_sym, db_sym in SYMBOLS.items():

        print(f"=== {db_sym} ===")

        for tf, secs, table, target in TIMEFRAMES:

            last = get_last_timestamp(db_sym, table, tf)
            mode = "incrementiel" if last else "backfill complet"

            inserted, n_req = update_candles(
                api_sym, db_sym, tf, secs,
                target=target, table=table
            )

            print(f"  {tf:<6} [{mode}] {inserted} ecrites, {n_req} requetes")

    print("\n=== Recapitulatif ===")

    for db_sym in SYMBOLS.values():

        for tf in ("1hour", "4hour", "1day"):

            table = "candles" if tf == "1hour" else "candles_multi"

            if tf == "1hour":
                sql = ("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) "
                       "FROM candles WHERE symbol = ?")
                params = [db_sym]
            else:
                sql = ("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) "
                       "FROM candles_multi WHERE symbol = ? AND timeframe = ?")
                params = [db_sym, tf]

            row = db.execute(sql, params).fetchone()

            if row and row[0]:
                print(f"  {db_sym} {tf:<6}: {row[0]:>6} bougies "
                      f"({datetime.fromtimestamp(row[1]):%Y-%m-%d} -> "
                      f"{datetime.fromtimestamp(row[2]):%Y-%m-%d})")


if __name__ == "__main__":
    main()
