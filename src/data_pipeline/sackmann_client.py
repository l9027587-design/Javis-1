"""Client for Jeff Sackmann's tennis_atp / tennis_wta datasets on GitHub
(https://github.com/JeffSackmann/tennis_atp, https://github.com/JeffSackmann/tennis_wta):
free, unlimited, no API key, plain CSV files updated periodically. Used for rankings and
finished-match backfill (see ingest.py) -- both barely change intra-day, so this dataset's
lag of a few days is no real loss, and it costs zero of the live stats API's metered quota.

License: CC BY-NC-SA 4.0 (non-commercial use only) -- see each repo's README.
"""
from __future__ import annotations

from io import StringIO

import pandas as pd
import requests

REPO_BASE = {
    "atp": "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master",
    "wta": "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master",
}


def _fetch_csv(tour: str, filename: str) -> pd.DataFrame:
    url = f"{REPO_BASE[tour]}/{filename}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return pd.read_csv(StringIO(response.text), low_memory=False)


def get_players(tour: str) -> pd.DataFrame:
    """Master player list: player_id, name_first, name_last, hand, dob, ioc, height."""
    return _fetch_csv(tour, f"{tour}_players.csv")


def get_current_rankings(tour: str) -> pd.DataFrame:
    """Latest available rankings snapshot: ranking_date, rank, player (id), points."""
    return _fetch_csv(tour, f"{tour}_rankings_current.csv")


def get_matches(tour: str, year: int) -> pd.DataFrame:
    """Tour-level main-draw match results for one season."""
    return _fetch_csv(tour, f"{tour}_matches_{year}.csv")
