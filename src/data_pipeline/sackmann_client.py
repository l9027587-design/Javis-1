"""Client for Jeff Sackmann's tennis_atp / tennis_wta datasets on GitHub
(https://github.com/JeffSackmann/tennis_atp, https://github.com/JeffSackmann/tennis_wta):
free, unlimited, no API key, plain CSV files updated periodically. Used for rankings and
finished-match backfill (see ingest.py) -- both barely change intra-day, so this dataset's
lag of a few days is no real loss, and it costs zero of the live stats API's metered quota.

License: CC BY-NC-SA 4.0 (non-commercial use only) -- see each repo's README.

The exact file layout is a documented convention, not a versioned API contract, and has
shifted over the years (e.g. files added/renamed). Rather than hardcode filenames that can
silently go stale, resolve them against the repo's real directory listing at fetch time via
the GitHub Contents API.
"""
from __future__ import annotations

from functools import lru_cache
from io import StringIO

import pandas as pd
import requests

RAW_BASE = {
    "atp": "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master",
    "wta": "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master",
}
CONTENTS_API = "https://api.github.com/repos/JeffSackmann/tennis_{tour}/contents"


@lru_cache(maxsize=4)
def _repo_file_listing(tour: str) -> tuple[str, ...]:
    """All file names at the repo root, paginated (there are a few hundred)."""
    names: list[str] = []
    page = 1
    while True:
        response = requests.get(
            CONTENTS_API.format(tour=tour),
            params={"per_page": 100, "page": page},
            headers={"Accept": "application/vnd.github+json"},
            timeout=15,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        names.extend(item["name"] for item in batch if item.get("type") == "file")
        if len(batch) < 100:
            break
        page += 1
    return tuple(names)


def _resolve_filename(tour: str, exact: str, prefix: str) -> str:
    names = _repo_file_listing(tour)
    if exact in names:
        return exact
    matches = sorted(n for n in names if n.startswith(prefix))
    if not matches:
        raise FileNotFoundError(f"No file matching '{prefix}*' found in JeffSackmann/tennis_{tour}")
    return matches[0]


def _fetch_csv(tour: str, filename: str) -> pd.DataFrame:
    url = f"{RAW_BASE[tour]}/{filename}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return pd.read_csv(StringIO(response.text), low_memory=False)


def get_players(tour: str) -> pd.DataFrame:
    """Master player list: player_id, name_first, name_last, hand, dob, ioc, height."""
    filename = _resolve_filename(tour, f"{tour}_players.csv", f"{tour}_players")
    return _fetch_csv(tour, filename)


def get_current_rankings(tour: str) -> pd.DataFrame:
    """Latest available rankings snapshot: ranking_date, rank, player (id), points."""
    filename = _resolve_filename(tour, f"{tour}_rankings_current.csv", f"{tour}_rankings_current")
    return _fetch_csv(tour, filename)


def get_matches(tour: str, year: int) -> pd.DataFrame:
    """Tour-level main-draw match results for one season."""
    filename = _resolve_filename(tour, f"{tour}_matches_{year}.csv", f"{tour}_matches_{year}")
    return _fetch_csv(tour, filename)
