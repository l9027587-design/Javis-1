"""Client for free, unauthenticated tennis rankings/results datasets on GitHub, in the
column format popularized by Jeff Sackmann's tennis_atp / tennis_wta repos. Used for
rankings and finished-match backfill (see ingest.py) -- both barely change intra-day, so
a dataset with a few days' lag costs nothing in accuracy and zero of the live stats API's
metered quota.

JeffSackmann/tennis_atp and tennis_wta (https://github.com/JeffSackmann/tennis_atp) are
the original, canonical source, but started 404ing via the GitHub API even with quota to
spare (X-RateLimit-Remaining well above 0) -- consistent with the repos having gone
private, which GitHub's unauthenticated API reports as 404 rather than 403 specifically so
it can't be used to confirm a private repo's existence. Each tour therefore tries a list of
candidate repos in order; Tennismylife/TML-Database is an actively maintained successor
explicitly modeled on Sackmann's format (CC BY-NC-SA, non-commercial use only, like the
original).

The exact file layout in any of these repos is a documented convention, not a versioned
API contract, so filenames are resolved against each candidate's real directory listing at
fetch time (via the GitHub Contents API) rather than hardcoded -- self-corrects if a repo's
layout differs from what's assumed, or a repo goes away entirely and a later candidate
takes over.
"""
from __future__ import annotations

from functools import lru_cache
from io import StringIO

import pandas as pd
import requests

REPO_CANDIDATES: dict[str, list[str]] = {
    "atp": ["JeffSackmann/tennis_atp", "Tennismylife/TML-Database"],
    "wta": ["JeffSackmann/tennis_wta"],
}


def _raise_with_body(response: requests.Response) -> None:
    """requests' default HTTPError message omits the response body, which for the GitHub
    API usually explains *why* (e.g. rate limiting vs. a genuine not-found) far better
    than a bare status code."""
    if response.ok:
        return
    rate_limit_remaining = response.headers.get("X-RateLimit-Remaining")
    raise requests.HTTPError(
        f"{response.status_code} for {response.url}"
        f"{f' (X-RateLimit-Remaining={rate_limit_remaining})' if rate_limit_remaining is not None else ''}"
        f": {response.text[:300]}",
        response=response,
    )


@lru_cache(maxsize=8)
def _repo_file_index(repo: str) -> tuple[tuple[str, str], ...]:
    """(name, download_url) for every file at one repo's root, paginated. download_url
    (from the API response itself) points at the right branch without having to guess
    it (master vs. main varies by repo)."""
    entries: list[tuple[str, str]] = []
    page = 1
    while True:
        response = requests.get(
            f"https://api.github.com/repos/{repo}/contents",
            params={"per_page": 100, "page": page},
            headers={"Accept": "application/vnd.github+json"},
            timeout=15,
        )
        _raise_with_body(response)
        batch = response.json()
        if not batch:
            break
        entries.extend((item["name"], item["download_url"]) for item in batch if item.get("type") == "file")
        if len(batch) < 100:
            break
        page += 1
    return tuple(entries)


def _resolve(tour: str, exact: str, prefix: str) -> str:
    """Try each candidate repo for this tour in turn; return the download URL of the
    first matching file (by exact name, then by prefix)."""
    errors: list[str] = []
    for repo in REPO_CANDIDATES.get(tour, []):
        try:
            names = dict(_repo_file_index(repo))
        except requests.RequestException as exc:
            errors.append(f"{repo}: {exc}")
            continue
        if exact in names:
            return names[exact]
        matches = sorted(n for n in names if n.startswith(prefix))
        if matches:
            return names[matches[0]]
        errors.append(f"{repo}: no file matching '{prefix}*' among {len(names)} files")
    raise FileNotFoundError(f"No source found for tour={tour} prefix='{prefix}': " + "; ".join(errors))


def _fetch_csv(url: str) -> pd.DataFrame:
    response = requests.get(url, timeout=30)
    _raise_with_body(response)
    return pd.read_csv(StringIO(response.text), low_memory=False)


def get_players(tour: str) -> pd.DataFrame:
    """Master player list: player_id, name_first, name_last, hand, dob, ioc, height."""
    return _fetch_csv(_resolve(tour, f"{tour}_players.csv", f"{tour}_players"))


def get_current_rankings(tour: str) -> pd.DataFrame:
    """Latest available rankings snapshot: ranking_date, rank, player (id), points."""
    return _fetch_csv(_resolve(tour, f"{tour}_rankings_current.csv", f"{tour}_rankings_current"))


def get_matches(tour: str, year: int) -> pd.DataFrame:
    """Tour-level main-draw match results for one season."""
    return _fetch_csv(_resolve(tour, f"{tour}_matches_{year}.csv", f"{tour}_matches_{year}"))
