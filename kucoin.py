"""Acces a l'API KuCoin et mise a jour incrementielle des bougies.

La base DuckDB est la source de verite : on ne telecharge que les
bougies manquantes depuis la derniere stockee, jamais tout a chaque fois.
"""
import time

import requests

from db import db

BASE_URL = "https://api.kucoin.com/api/v1/market/candles"


def fetch_chunk(symbol_api, timeframe, start_at, end_at):

    url = (
        f"{BASE_URL}?type={timeframe}&symbol={symbol_api}"
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

        time.sleep(1 + attempt)

    return None


def get_last_timestamp(symbol):

    row = db.execute(
        "SELECT MAX(timestamp) FROM candles WHERE symbol = ?",
        [symbol]
    ).fetchone()

    return row[0] if row and row[0] is not None else None


def save_candles(symbol_db, kucoin_rows):

    # KuCoin : [time, open, close, high, low, volume, turnover]
    values = [
        (int(r[0]), symbol_db, float(r[1]), float(r[3]),
         float(r[4]), float(r[2]), float(r[5]))
        for r in kucoin_rows
    ]

    if values:
        db.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?, ?, ?, ?, ?, ?, ?)",
            values
        )
        db.commit()

    return len(values)


def update_candles(symbol_api, symbol_db, timeframe, tf_seconds,
                   target=1000, chunk_hours=1500):
    """Complete la base avec les bougies manquantes de KuCoin.

    - base vide        : telecharge les `target` dernieres bougies
    - base remplie     : telecharge uniquement ce qui manque depuis
                         la derniere bougie stockee (1 requete si
                         l'ecart est petit)
    Renvoie (bougies inserees/remplacees, requetes envoyees).
    """

    now = int(time.time())
    last = get_last_timestamp(symbol_db)

    if last is None:
        start_at = now - target * tf_seconds
    else:
        start_at = last  # reprend la derniere bougie (valeur finale)

    collected = {}
    end_at = now
    n_req = 0

    while True:

        window_start = max(end_at - chunk_hours * tf_seconds, start_at)
        data = fetch_chunk(symbol_api, timeframe, window_start, end_at)
        n_req += 1

        if data is None:
            break

        for row in data:
            ts = int(row[0])
            if ts >= start_at:
                collected[ts] = row

        if len(data) == 0 or window_start <= start_at:
            break

        # recule vers le passe tant que le trou a combler depasse
        # la taille max d'une requete
        end_at = min(int(row[0]) for row in data) - 1
        time.sleep(0.2)

    inserted = save_candles(
        symbol_db, [collected[ts] for ts in sorted(collected)]
    )

    return inserted, n_req
