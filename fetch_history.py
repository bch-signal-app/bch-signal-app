"""Complete l'historique horaire BCH-USDT dans DuckDB.

Par defaut : incrementiel — telecharge uniquement les bougies
manquantes depuis la derniere stockee (rapide).
Avec --full : retélécharge tout l'historique disponible (~51000
bougies, quelques minutes).

Usage : python fetch_history.py [--full]
"""
import sys
from datetime import datetime

from db import db, count_candles
from kucoin import update_candles, get_last_timestamp

SYMBOL_API = "BCH-USDT"
SYMBOL_DB = "BCHUSDT"
TIMEFRAME = "1hour"
TF_SECONDS = 3600
TARGET = 100000


def main():

    full = "--full" in sys.argv

    before = count_candles()
    last = get_last_timestamp(SYMBOL_DB)

    print(f"Avant : {before} bougies", end="")
    if last:
        print(f", derniere du {datetime.fromtimestamp(last):%Y-%m-%d %H:%M}")
    else:
        print(" (base vide)")

    if full:
        print("Mode complet : telechargement de tout l'historique...")
        inserted, n_req = update_candles(
            SYMBOL_API, SYMBOL_DB, TIMEFRAME, TF_SECONDS,
            target=TARGET
        )
    else:
        print("Mode incrementiel...")
        inserted, n_req = update_candles(
            SYMBOL_API, SYMBOL_DB, TIMEFRAME, TF_SECONDS,
            target=1000
        )

    after = count_candles()
    print(f"Requetes KuCoin : {n_req}")
    print(f"Inserees/remplacees : {inserted}")
    print(f"Apres : {after} bougies ({after - before} ajoutees)")

    row = db.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM candles "
        "WHERE symbol = ?",
        [SYMBOL_DB]
    ).fetchone()

    if row and row[0]:
        print(
            f"Periode : {datetime.fromtimestamp(row[0]):%Y-%m-%d %H:%M} "
            f"-> {datetime.fromtimestamp(row[1]):%Y-%m-%d %H:%M}"
        )


if __name__ == "__main__":
    main()
