"""Model access.

Two tiers, deliberately split by cost and by consequence.

* **Claude** handles judgement: which of several vetted structures best fits the
  regime, and why. Decisions that touch money go here.
* **Featherless** hosts small open-source models that handle volume: reducing a
  stream of headlines to a sentiment label so Claude only ever reads the few
  that matter. Cheap, disposable, and never trusted with a decision.

Both are optional. With no keys configured the engine runs fully deterministic,
which is also how it runs its regression tests.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from engine.config import SETTINGS

log = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-sonnet-5"
FEATHERLESS_MODEL = "Qwen/Qwen2.5-7B-Instruct"
FEATHERLESS_BASE = "https://api.featherless.ai/v1"


def claude_available() -> bool:
    return bool(SETTINGS.anthropic_api_key)


def featherless_available() -> bool:
    return bool(SETTINGS.featherless_api_key)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model response."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def ask_claude(
    system: str, user: str, max_tokens: int = 1200, model: str = CLAUDE_MODEL
) -> dict[str, Any] | None:
    """One structured call to Claude. Returns parsed JSON, or None on any failure."""
    if not claude_available():
        return None
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=SETTINGS.anthropic_api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        parsed = _extract_json(text)
        if parsed is None:
            log.warning("Claude returned unparseable output: %s", text[:200])
        return parsed
    except Exception as exc:  # noqa: BLE001 - the loop must survive a model outage
        log.warning("Claude call failed: %s", exc)
        return None


def classify_headlines(headlines: list[str]) -> list[dict[str, Any]]:
    """Score headlines with a small open-source model on Featherless.

    Runs on every headline, which is why it uses the cheap tier. The output is
    a filter, not a decision: it decides what Claude bothers reading.
    """
    if not headlines or not featherless_available():
        return []

    numbered = "\n".join(f"{i}. {h}" for i, h in enumerate(headlines))
    prompt = (
        "Classify each market headline. Reply with JSON only, in the form "
        '{"items":[{"i":0,"sentiment":"bullish|bearish|neutral",'
        '"relevance":0.0-1.0,"topic":"macro|earnings|policy|other"}]}.\n\n'
        f"{numbered}"
    )
    try:
        response = httpx.post(
            f"{FEATHERLESS_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {SETTINGS.featherless_api_key}"},
            json={
                "model": FEATHERLESS_MODEL,
                "messages": [
                    {"role": "system", "content": "You output strict JSON and nothing else."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 700,
            },
            timeout=30,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = _extract_json(content) or {}
        items = parsed.get("items", [])
        return items if isinstance(items, list) else []
    except Exception as exc:  # noqa: BLE001
        log.warning("Featherless classification failed: %s", exc)
        return []


def summarise_tone(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce classified headlines to a single tone reading."""
    if not items:
        return {"tone": "unknown", "score": 0.0, "n": 0}
    weight = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}
    total, mass = 0.0, 0.0
    for item in items:
        relevance = float(item.get("relevance", 0.5) or 0.5)
        total += weight.get(str(item.get("sentiment", "neutral")), 0.0) * relevance
        mass += relevance
    score = total / mass if mass else 0.0
    tone = "bullish" if score > 0.2 else "bearish" if score < -0.2 else "neutral"
    return {"tone": tone, "score": round(score, 3), "n": len(items)}
