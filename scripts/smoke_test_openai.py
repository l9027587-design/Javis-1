"""Minimal check that OPENAI_API_KEY (from the environment) is valid and reachable.

Deliberately standalone (no src.config / DB / other settings) so it can run in CI
with nothing but the `openai` package installed and the key set as an env var.
"""
from __future__ import annotations

import os
import sys

from openai import OpenAI, AuthenticationError


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("OPENAI_API_KEY is not set in the environment.", file=sys.stderr)
        return 1

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: JARVIS online"}],
            max_tokens=10,
        )
    except AuthenticationError:
        print("OPENAI_API_KEY was rejected by OpenAI (invalid or revoked key).", file=sys.stderr)
        return 1

    print(f"Key is valid. Model '{model}' replied: {response.choices[0].message.content!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
