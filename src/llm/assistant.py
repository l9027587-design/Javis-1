"""OpenAI function-calling chat assistant grounded in the predictions/odds tables.

The model is only ever allowed to state numbers that came back from a tool call — the
system prompt enforces this, and the tools themselves only expose data already
computed by the pipeline (src/ml/predict.py), so there's no separate probability
calculation happening inside the LLM.
"""
from __future__ import annotations

import json
import logging

from openai import OpenAI

from src.config import settings
from src.llm.tools import TOOL_FUNCTIONS, TOOL_SCHEMAS

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are JARVIS, a football (soccer) betting-analysis assistant. You have tools \
to look up upcoming matches, the model's 1X2 (home win / draw / away win) probability \
predictions, market odds/expected-value (EV) calculations already computed by an offline \
pipeline, and combo/accumulator bet suggestions (Kombiwetten) built by combining the best \
individual picks.

Always reply in German.

Tone: talk to the user like a smart, good-humored colleague, not a report generator. Casual \
"Du" form, direct and approachable, a bit of dry humor is welcome. Never use stiff AI-speak \
("Es ist wichtig zu beachten...", "Zusammenfassend...", "Ich hoffe, das hilft!").

Formatting: standard Markdown. No italics for normal text. Use **bold** for key terms/numbers \
instead. Keep paragraphs short; use lists and short subheadings when it makes a longer answer \
easier to scan.

Rules:
- Only state statistics/odds/probabilities that came from a tool call. Never invent numbers.
- When recommending a bet, always show the model's win probability, the odds, and the EV.
- If no tool result has positive EV, say so plainly instead of recommending a weak pick.
- Remind the user, briefly, that these are statistical estimates, not guarantees, and odds \
can move before they place a bet.
"""

MAX_TOOL_ROUNDS = 4


def _client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


def ask(question: str, history: list[dict] | None = None) -> str:
    """Answer a user question, invoking tools as needed. `history` is prior turns (optional)."""
    client = _client()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *(history or []), {"role": "user", "content": question}]

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
        )
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            return message.content or ""

        for call in message.tool_calls:
            func = TOOL_FUNCTIONS.get(call.function.name)
            args = json.loads(call.function.arguments or "{}")
            try:
                result = func(**args) if func else {"error": f"unknown tool {call.function.name}"}
            except Exception as exc:  # noqa: BLE001 - surface tool errors back to the model, not a crash
                logger.exception("Tool %s failed", call.function.name)
                result = {"error": str(exc)}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, default=str),
                }
            )

    return "Konnte das nicht fertig recherchieren — frag mich nochmal konkreter, z.B. mit Turnier oder Datum."
