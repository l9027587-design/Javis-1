"""FastAPI backend for the JARVIS-style tennis prediction HUD.

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

app = FastAPI(title="JARVIS Tennis Prediction AI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


def _live_matches() -> list[dict] | None:
    """Try the real pipeline (Postgres-backed). Returns None if unavailable/empty."""
    try:
        from src.llm.tools import get_upcoming_matches, get_match_prediction

        upcoming = get_upcoming_matches(days_ahead=4)
        if not upcoming:
            return None
        enriched = []
        for m in upcoming:
            pred = get_match_prediction(m["match_id"]) or {}
            enriched.append({**m, **pred, "demo": False})
        return enriched
    except Exception:  # noqa: BLE001 - DB/model not configured in this environment
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
        "data_source": "Tipico (simulated feed)" if demo else f"Tipico via The Odds API (bookmakers={settings.odds_bookmakers or 'eu region'})",
        "assistant_ready": bool(settings.openai_api_key),
        "match_count": len(matches),
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
    data, _ = _matches_payload()
    text = message.lower()

    def fmt(m: dict) -> str:
        favorite = m["pick"]
        prob = max(m["player1_win_prob"], m["player2_win_prob"])
        return (
            f"{m['player1']['name']} gegen {m['player2']['name']} ({m['tournament']}, {m['round']}) — "
            f"ich sehe {favorite} vorn mit {prob:.0%}, Tipico-Quoten stehen bei "
            f"{m['tipico_player1_odds']:.2f} / {m['tipico_player2_odds']:.2f}, "
            f"das ergibt einen EV von {m['expected_value']:+.1%}."
        )

    if any(k in text for k in ("value", "bet", "wett", "edge", "empfeh")):
        picks = demo_data.best_value_bets(data, min_edge=0.05, limit=3)
        if not picks:
            return "Ich hab gerade nachgeschaut, aber nichts erreicht meine 5%-Edge-Schwelle. Ich bleibe dran."
        lines = "\n".join(f"- {fmt(m)}" for m in picks)
        return f"Hab die Zahlen durchgerechnet — hier seh ich gerade einen Vorteil gegenüber Tipicos Quoten:\n{lines}"

    for m in data:
        for side in ("player1", "player2"):
            name = m[side]["name"]
            if name.lower().split()[-1] in text:
                return fmt(m)

    upcoming = "\n".join(f"- {fmt(m)}" for m in data[:3])
    demo_note = " (simulierte Daten)" if data and data[0].get("demo") else ""
    return (
        f"Schön, dass du da bist. Hier ein kurzer Überblick{demo_note}:\n{upcoming}\n\n"
        "Frag mich einfach nach einem bestimmten Spieler, oder sag \"beste Wetten\", "
        "dann grenz ich das für dich ein."
    )


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
