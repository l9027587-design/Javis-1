"""Terminal chat REPL for asking the assistant questions.

    python -m src.cli
"""
from __future__ import annotations

from src.llm.assistant import ask


def main() -> None:
    print("Football betting assistant. Ask a question, or type 'quit'.")
    history: list[dict] = []
    while True:
        question = input("\n> ").strip()
        if question.lower() in {"quit", "exit"}:
            break
        if not question:
            continue
        answer = ask(question, history)
        print(f"\n{answer}")
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
