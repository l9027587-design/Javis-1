"""FastAPI backend for the JARVIS-style football prediction HUD.

Serves the static frontend (static/) and a small JSON API on top of the existing
pipeline (src/llm/tools.py, src/ml/predict.py). If Postgres or an OpenAI key isn't
configured — e.g. running this UI standalone without the full cloud stack — it
transparently falls back to src/web/demo_data.py so the interface is always fully
explorable, with every simulated response clearly flagged `"demo": true`.

Run with:  uvicorn src.web.app:app --reload --port 8000
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import settings
from src.web import demo_data

logger = logging.getLogger(__name__)

app = FastAPI(title="JARVIS Football Prediction AI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


@app.middleware("http")
async def no_cache_static_assets(request, call_next):
    """Force revalidation for the frontend's own files (not the JSON API).

    Without this, mobile browsers' heuristic caching can keep serving a stale
    static/js/app.js for a while after a deploy ships a fix -- confusing during active
    iteration, since the page looks like it didn't update at all.
    """
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.endswith((".js", ".css", ".html")):
        response.headers["Cache-Control"] = "no-cache"
    return response


_last_demo_reason: str | None = None


def _live_matches() -> list[dict] | None:
    """Try the real pipeline (Postgres-backed). Returns None if unavailable/empty."""
    global _last_demo_reason
    try:
        from src.llm.tools import get_matches_with_predictions

        matches = get_matches_with_predictions(days_ahead=7)
        if not matches:
            _last_demo_reason = "DB reachable, but no scheduled matches found in the next 7 days"
            return None
        _last_demo_reason = None
        return matches
    except Exception as exc:  # noqa: BLE001 - DB/model not configured in this environment
        # Surfaced via /api/status's debug_reason so this is checkable from a phone
        # browser without digging through Render's log UI.
        _last_demo_reason = f"{type(exc).__name__}: {exc}"
        logger.info("Live pipeline unavailable, falling back to demo data", exc_info=True)
        return None


def _matches_payload() -> tuple[list[dict], bool]:
    live = _live_matches()
    if live:
        return live, False
    return demo_data.generate_matches(), True


@app.get("/api/status")
def status() -> dict:
    matches, demo = _matches_payload()
    return {
        "online": True,
        "time": dt.datetime.utcnow().isoformat() + "Z",
        "demo_mode": demo,
        "data_source": "Tipico (simulierter Feed)" if demo else f"Tipico über The Odds API (Buchmacher={settings.odds_bookmakers or 'EU-Region'})",
        "assistant_ready": bool(settings.openai_api_key),
        "match_count": len(matches),
        "debug_reason": _last_demo_reason if demo else None,
    }


@app.get("/api/matches")
def matches() -> dict:
    data, demo = _matches_payload()
    return {"demo": demo, "matches": data}


@app.get("/api/value-bets")
def value_bets(min_edge: float = 0.05, limit: int = 5) -> dict:
    data, demo = _matches_payload()
    if demo:
        picks = demo_data.best_value_bets(data, min_edge=min_edge, limit=limit)
    else:
        try:
            from src.llm.tools import get_best_value_bets

            picks = get_best_value_bets(min_edge=min_edge, limit=limit)
        except Exception:  # noqa: BLE001
            picks = []
    return {"demo": demo, "value_bets": picks}


@app.get("/api/combo-bets")
def combo_bets(min_edge: float = 0.0, max_legs: int = 3) -> dict:
    data, demo = _matches_payload()
    if demo:
        combos = demo_data.combo_suggestions(data, min_edge=min_edge, max_legs=max_legs)
    else:
        try:
            from src.llm.tools import get_combo_suggestions

            combos = get_combo_suggestions(min_edge=min_edge, max_legs=max_legs)
        except Exception:  # noqa: BLE001
            combos = []
    return {"demo": demo, "combos": combos}


class ChatRequest(BaseModel):
    message: str
    history: list[dict] | None = None


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    if settings.openai_api_key:
        try:
            from src.llm.assistant import ask

            reply = ask(req.message, req.history)
            return {"reply": reply, "demo": False}
        except Exception:  # noqa: BLE001 - degrade to local responder rather than 500
            logger.exception("LLM assistant call failed, using offline responder")

    reply = _offline_reply(req.message)
    return {"reply": reply, "demo": True}


def _offline_reply(message: str) -> str:
    """Rule-based JARVIS-voiced fallback (German) so chat works with zero API keys configured."""
    data, demo = _matches_payload()
    text = message.lower()

    def fmt(m: dict) -> str:
        # Live matches the model hasn't scored yet (has_prediction: False -- freshly
        # ingested, no train-and-predict run over them) have no odds/EV to report.
        if not m.get("has_prediction", True):
            return (
                f"{m['home_team']['name']} gegen {m['away_team']['name']} ({m['league']}, {m.get('round') or '?'}) — "
                "das Modell hat das noch nicht durchgerechnet, frag gleich nochmal."
            )
        home_pct, draw_pct, away_pct = m["home_win_prob"], m["draw_prob"], m["away_win_prob"]
        best_prob = max(home_pct, draw_pct, away_pct)
        if best_prob == home_pct:
            favorite = m["home_team"]["name"]
        elif best_prob == draw_pct:
            favorite = "Unentschieden"
        else:
            favorite = m["away_team"]["name"]

        # has_prediction can be true with no Tipico odds yet (e.g. lower-tier matches
        # the bookmaker feed doesn't cover) -- report the model's read without EV/odds.
        if m.get("expected_value") is None:
            return (
                f"{m['home_team']['name']} gegen {m['away_team']['name']} ({m['league']}, {m.get('round') or '?'}) — "
                f"ich seh {favorite} vorn mit {best_prob:.0%}, aber noch keine Tipico-Quote dafür, kann also kein EV ausrechnen."
            )
        draw_odds_str = f"{m['draw_odds']:.2f}" if m.get("draw_odds") else "–"
        return (
            f"{m['home_team']['name']} gegen {m['away_team']['name']} ({m['league']}, {m.get('round') or '?'}) — "
            f"ich seh {favorite} vorn mit {best_prob:.0%}, mein Pick mit Kante ist **{m['value_pick']}** — "
            f"Tipico-Quoten (1/X/2): {m['home_odds']:.2f} / {draw_odds_str} / {m['away_odds']:.2f}, "
            f"das ergibt einen EV von {m['expected_value']:+.1%}."
        )

    if any(k in text for k in ("kombi", "combo", "parlay")):
        if demo:
            combos = demo_data.combo_suggestions(data, min_edge=0.0, max_legs=3)
        else:
            try:
                from src.llm.tools import get_combo_suggestions

                combos = get_combo_suggestions(min_edge=0.0, max_legs=3)
            except Exception:  # noqa: BLE001
                combos = []
        if not combos:
            return "Aktuell hab ich nicht genug Value-Picks für eine sinnvolle Kombi — frag nochmal, wenn mehr Spiele durchgerechnet sind."
        best_combo = combos[-1]  # the largest combo, built from the same ranked picks as the smaller ones
        legs_text = ", ".join(f"{leg['pick']} ({leg['odds']:.2f})" for leg in best_combo["legs"])
        return (
            f"Kombi-Vorschlag mit {len(best_combo['legs'])} Spielen: {legs_text} — "
            f"kombinierte Quote **{best_combo['combined_odds']:.2f}**, "
            f"kombinierte Trefferwahrscheinlichkeit {best_combo['combined_prob']:.0%}, "
            f"EV {best_combo['combined_ev']:+.1%}. "
            "Denk dran: bei Kombis multipliziert sich nicht nur die Quote, sondern auch das Risiko."
        )

    if any(k in text for k in ("value", "bet", "wett", "edge", "empfeh")):
        picks = demo_data.best_value_bets(data, min_edge=0.05, limit=3)
        if not picks:
            return "Ich hab gerade nachgeschaut, aber nichts erreicht meine 5%-Edge-Schwelle. Ich bleibe dran."
        lines = "\n".join(f"- {fmt(m)}" for m in picks)
        return f"Hab die Zahlen durchgerechnet — hier seh ich gerade einen Vorteil gegenüber Tipicos Quoten:\n{lines}"

    for m in data:
        for side in ("home_team", "away_team"):
            name = m[side]["name"]
            if name.lower().split()[-1] in text:
                return fmt(m)

    upcoming = "\n".join(f"- {fmt(m)}" for m in data[:3])
    demo_note = " (simulierte Daten)" if data and data[0].get("demo") else ""
    return (
        f"Schön, dass du da bist. Hier ein kurzer Überblick{demo_note}:\n{upcoming}\n\n"
        "Frag mich einfach nach einem bestimmten Team, oder sag \"beste Wetten\", "
        "dann grenz ich das für dich ein."
    )


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
