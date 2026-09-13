"""Telecharge l'historique horaire BCH-USDT depuis KuCoin (pagine)
et le stocke dans la table candles de DuckDB.

Usage : python fetch_history.py
"""
import time
from datetime import datetime

import requests

from db import db

SYMBOL_API = "BCH-USDT"
SYMBOL_DB = "BCHUSDT"
TARGET = 100000
CHUNK_HOURS = 1500  # maximum KuCoin par requete
SLEEP = 0.3


def fetch_chunk(start_at, end_at):

    url = (
        "https://api.kucoin.com/api/v1/market/candles"
        f"?type=1hour&symbol={SYMBOL_API}"
        f"&startAt={start_at}&endAt={end_at}"
    )

    for attempt in range(3):

        try:
            r = requests.get(url, timeout=15)

            if r.status_code == 200 and r.json().get("code") == "200000":
                return r.json().get("data", [])

            print("  HTTP", r.status_code, "- tentative", attempt + 1)

        except requests.RequestException as e:
            print("  Erreur reseau:", e, "- tentative", attempt + 1)

        time.sleep(2 * (attempt + 1))

    return None


def main():

    end_at = int(time.time())
    collected = {}
    n_req = 0

    while len(collected) < TARGET:

        start_at = end_at - CHUNK_HOURS * 3600
        data = fetch_chunk(start_at, end_at)
        n_req += 1

        if data is None:
            print("Echec reseau apres 3 tentatives, arret.")
            break

        new = 0
        oldest = None

        # KuCoin renvoie du plus recent au plus ancien, au format
        # [time, open, close, high, low, volume, turnover]
        for row in data:
            ts = int(row[0])
            if ts not in collected:
                collected[ts] = (ts, row[1], row[3], row[4], row[2], row[5])
                new += 1
            if oldest is None or ts < oldest:
                oldest = ts

        print(
            f"[{n_req:3d}] fenetre {start_at} -> {end_at} : "
            f"{len(data)} recues, {new} nouvelles, total {len(collected)}"
        )

        if not data or oldest is None:
            print("Debut de l'historique KuCoin atteint.")
            break

        if new == 0:
            print("Plus aucune donnee nouvelle.")
            break

        end_at = oldest - 1
        time.sleep(SLEEP)

    rows = [
        (ts, SYMBOL_DB, float(o), float(h), float(l), float(c), float(v))
        for (ts, o, h, l, c, v)
        in (collected[ts] for ts in sorted(collected))
    ]

    if rows:
        db.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows
        )
        db.commit()

    first = datetime.fromtimestamp(rows[0][0])
    last = datetime.fromtimestamp(rows[-1][0])
    print(f"Inserees : {len(rows)} bougies")
    print(f"Periode  : {first} -> {last}")


if __name__ == "__main__":
    main()
