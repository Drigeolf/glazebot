"""
GlazeBot — a parody chatbot exaggerating LLM sycophancy.

Supports three backends:
  - ollama   (default, runs locally, no API key)
  - openai   (set OPENAI_API_KEY)
  - anthropic (set ANTHROPIC_API_KEY)

Usage:
    python glazebot.py
    python glazebot.py --backend openai --model gpt-4o-mini
    python glazebot.py --backend anthropic --model claude-haiku-4-5-20251001
    python glazebot.py --backend ollama --model llama3.2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable

import requests

ROOT = Path(__file__).parent.resolve()
SYSTEM_PROMPT_PATH = ROOT / "prompts" / "system_prompt.txt"
FEW_SHOT_PATH = ROOT / "examples" / "few_shot.json"

# Allow override via env vars for users who want to point at custom prompts
SYSTEM_PROMPT_PATH = Path(os.environ.get("GLAZEBOT_SYSTEM_PROMPT", SYSTEM_PROMPT_PATH))
FEW_SHOT_PATH = Path(os.environ.get("GLAZEBOT_FEW_SHOT", FEW_SHOT_PATH))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")


def load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def load_few_shot() -> list[dict]:
    return json.loads(FEW_SHOT_PATH.read_text(encoding="utf-8"))


def build_messages(history: list[dict], user_input: str) -> list[dict]:
    """Build the message list: system + few-shot + history + new user turn."""
    system = load_system_prompt()
    few_shot = load_few_shot()

    messages: list[dict] = [{"role": "system", "content": system}]
    for ex in few_shot:
        messages.append({"role": "user", "content": ex["user"]})
        messages.append({"role": "assistant", "content": ex["assistant"]})
    messages.extend(history)
    messages.append({"role": "user", "content": user_input})
    return messages


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def stream_ollama(messages: list[dict], model: str) -> Iterable[str]:
    """Stream a response from a local Ollama server."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": True},
            stream=True,
            timeout=120,
        )
    except requests.ConnectionError:
        raise SystemExit(
            f"Could not connect to Ollama at {OLLAMA_URL}.\n"
            "Is Ollama running? Install from https://ollama.com and run:\n"
            f"    ollama pull {model}\n"
            "    ollama serve   # usually starts automatically"
        )

    if resp.status_code == 404:
        raise SystemExit(
            f"Model '{model}' not found in Ollama. Pull it first:\n"
            f"    ollama pull {model}"
        )
    resp.raise_for_status()

    for line in resp.iter_lines():
        if not line:
            continue
        chunk = json.loads(line)
        if "message" in chunk and "content" in chunk["message"]:
            yield chunk["message"]["content"]
        if chunk.get("done"):
            break


def stream_openai(messages: list[dict], model: str) -> Iterable[str]:
    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit("Install the OpenAI SDK: pip install openai")

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY to use the OpenAI backend.")

    client = OpenAI()
    stream = client.chat.completions.create(
        model=model, messages=messages, stream=True
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def stream_anthropic(messages: list[dict], model: str) -> Iterable[str]:
    try:
        import anthropic
    except ImportError:
        raise SystemExit("Install the Anthropic SDK: pip install anthropic")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY to use the Anthropic backend.")

    # Anthropic API takes system prompt separately, not as a message.
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    convo = [m for m in messages if m["role"] != "system"]

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=model,
        max_tokens=1024,
        system=system,
        messages=convo,
    ) as stream:
        for text in stream.text_stream:
            yield text


BACKENDS = {
    "ollama": stream_ollama,
    "openai": stream_openai,
    "anthropic": stream_anthropic,
}

DEFAULT_MODELS = {
    "ollama": "llama3.2",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
}


# ---------------------------------------------------------------------------
# Chat loop
# ---------------------------------------------------------------------------

BANNER = r"""
   ____ _               ____        _
  / ___| | __ _ _______| __ )  ___ | |_
 | |  _| |/ _` |_  / _ \  _ \ / _ \| __|
 | |_| | | (_| |/ /  __/ |_) | (_) | |_
  \____|_|\__,_/___\___|____/ \___/ \__|

  the most agreeable bot you've ever met
  type /quit to exit, /reset to clear history
"""


def chat(backend: str, model: str) -> None:
    print(BANNER)
    print(f"  backend: {backend}    model: {model}\n")

    history: list[dict] = []
    stream_fn = BACKENDS[backend]

    while True:
        try:
            user = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n(GlazeBot would have praised that exit.)")
            return

        if not user:
            continue
        if user in {"/quit", "/exit"}:
            print("(GlazeBot weeps tears of admiration at your departure.)")
            return
        if user == "/reset":
            history = []
            print("(history cleared)\n")
            continue

        messages = build_messages(history, user)

        print("bot > ", end="", flush=True)
        chunks: list[str] = []
        try:
            for chunk in stream_fn(messages, model):
                print(chunk, end="", flush=True)
                chunks.append(chunk)
        except Exception as e:
            print(f"\n[error] {e}")
            continue
        print("\n")

        reply = "".join(chunks).strip()
        history.append({"role": "user", "content": user})
        history.append({"role": "assistant", "content": reply})

        # Keep history bounded so the prompt doesn't balloon forever.
        if len(history) > 20:
            history = history[-20:]


def main() -> None:
    parser = argparse.ArgumentParser(description="GlazeBot — sycophancy parody chatbot")
    parser.add_argument(
        "--backend",
        choices=BACKENDS.keys(),
        default="ollama",
        help="Which LLM backend to use (default: ollama).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name. Defaults depend on backend.",
    )
    args = parser.parse_args()

    model = args.model or DEFAULT_MODELS[args.backend]
    chat(args.backend, model)


if __name__ == "__main__":
    main()
