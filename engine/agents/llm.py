"""Model access.

Three tiers, split by cost and by consequence.

* **Reasoning** decides which vetted structure to take. Consequential, so it
  gets the strongest model available: Gemini 2.5 Pro on Vertex, or Claude if an
  Anthropic key is configured.
* **Classification** reduces a stream of headlines to sentiment labels so the
  reasoning model only reads the few that matter. High volume, low stakes, so it
  runs on a cheap model: Gemini 2.5 Flash, or Featherless if a key is set.
* **Nothing** is the third tier, and it is a supported configuration. With no
  provider reachable the engine runs fully deterministic, which is also how its
  regression tests run.

Vertex authenticates through application-default credentials rather than an API
key, so there is no secret to store: whatever `gcloud auth application-default
login` already granted is what the agent uses.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from functools import lru_cache
from typing import Any

import httpx

from engine.config import SETTINGS

log = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-sonnet-5"
CLAUDE_FALLBACK_MODEL = "claude-sonnet-4-6"

#: Tried in order. Pro reasons better, so it is tried first everywhere before
#: falling back to Flash.
GEMINI_REASONING_MODELS = ("gemini-3.1-pro-preview", "gemini-3.8-flash", "gemini-2.5-pro", "gemini-2.5-flash")
GEMINI_FAST_MODEL = "gemini-3.8-flash"

#: Gemini on Vertex is served from Dynamic Shared Quota: there is no
#: per-project limit to raise, and a 429 means the pool shared across all
#: customers of that model was momentarily saturated. Google's guidance for
#: pay-as-you-go is to retry, because availability changes second to second.
#:
#: Regional pools are independent, so failing over to another region clears a
#: 429 far more often than waiting does. Global is prioritized first for 3.x models,
#: followed by primary US regions.
GEMINI_REGIONS = (
    "global",
    "us-central1",
    "us-east5",
    "us-west1",
    "us-east4",
    "europe-west4",
    "asia-northeast1",
    "us-south1",
)

#: Google Search grounding regions
GROUNDING_REGIONS = ("global", "us-central1", "us-east5", "us-east4", "us-west1")

#: A route is one (model, region) pair. Pro in every region before Flash, so we
#: only trade reasoning quality away once every regional pool has refused.
def _routes() -> list[tuple[str, str]]:
    return [(m, r) for m in GEMINI_REASONING_MODELS for r in GEMINI_REGIONS]

#: Gemini 2.5 counts thinking tokens against max_output_tokens, so a budget
#: sized for the visible answer alone gets truncated mid-JSON. Reserve room for
#: both, and keep the thinking budget small since the hard reasoning already
#: happened deterministically before the model was called.
THINKING_BUDGET = 256
OUTPUT_HEADROOM = 4


def _retryable(exc: Exception) -> bool:
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "503" in text
FEATHERLESS_MODEL = "Qwen/Qwen2.5-7B-Instruct"
FEATHERLESS_BASE = "https://api.featherless.ai/v1"


def claude_available() -> bool:
    return bool(SETTINGS.anthropic_api_key)


def featherless_available() -> bool:
    return bool(SETTINGS.featherless_api_key)


@lru_cache(maxsize=16)
def _client_for(location: str) -> Any | None:
    if not SETTINGS.vertex_project:
        return None
    try:
        from google import genai

        return genai.Client(
            vertexai=True, project=SETTINGS.vertex_project, location=location
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Vertex client unavailable in %s: %s", location, exc)
        return None


@lru_cache(maxsize=1)
def _vertex_client() -> Any | None:
    """Build a Vertex client once, or return None if credentials are absent."""
    return _client_for(SETTINGS.vertex_location or "global")


def vertex_available() -> bool:
    return _vertex_client() is not None


def reasoning_provider() -> str:
    if claude_available():
        return "claude"
    if vertex_available():
        return "gemini"
    return "none"


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model response."""
    text = (text or "").strip()
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


def _gemini_once(
    system: str, user: str, max_tokens: int, model: str, region: str | None = None
) -> dict[str, Any] | None:
    client = _client_for(region) if region else _vertex_client()
    if client is None:
        return None
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            max_output_tokens=max_tokens * OUTPUT_HEADROOM + THINKING_BUDGET,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    parsed = _extract_json(response.text)
    if parsed is None:
        log.warning(
            "%s returned unparseable output (finish=%s): %s",
            model,
            getattr(response.candidates[0], "finish_reason", "?") if response.candidates else "?",
            (response.text or "")[:160],
        )
    return parsed


def _ask_gemini(
    system: str,
    user: str,
    max_tokens: int,
    models: tuple[str, ...] | str | None = None,
    attempts: int = 2,
) -> dict[str, Any] | None:
    """Walk the model and region routes, retrying shared-quota errors.

    A 429 here is contention in a shared pool rather than an account limit, so
    the first response is to try another regional pool immediately, and only
    then to back off and wait.
    """
    if models is None:
        routes = _routes()
    elif isinstance(models, str):
        routes = [(models, region) for region in GEMINI_REGIONS]
    else:
        routes = [(m, r) for m in models for r in GEMINI_REGIONS]

    last_error: str | None = None
    for model, region in routes:
        for attempt in range(attempts):
            try:
                parsed = _gemini_once(system, user, max_tokens, model, region)
                if parsed is not None:
                    return parsed
                break  # answered but unusable: another attempt will not help
            except Exception as exc:  # noqa: BLE001 - an outage must not stop trading
                last_error = str(exc)[:160]
                if _retryable(exc):
                    if attempt < attempts - 1:
                        # Jitter so three supervisors do not retry in lockstep.
                        time.sleep((2 ** attempt) * (0.6 + random.random() * 0.8))
                        continue
                    break  # move to the next region rather than waiting longer
                log.warning("%s/%s call failed: %s", model, region, last_error)
                break
    if last_error:
        log.warning("every Gemini route refused; last error: %s", last_error)
    return None


def _ask_claude(
    system: str, user: str, max_tokens: int, model: str
) -> dict[str, Any] | None:
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
    except Exception as exc:  # noqa: BLE001
        log.warning("Claude call failed: %s", exc)
        return None


def reason(system: str, user: str, max_tokens: int = 1200) -> dict[str, Any] | None:
    """One structured call to the best available model. JSON in, JSON out."""
    provider = reasoning_provider()
    if provider == "claude":
        result = _ask_claude(system, user, max_tokens, CLAUDE_MODEL)
        if result is not None:
            return result
        return _ask_claude(system, user, max_tokens, CLAUDE_FALLBACK_MODEL)
    if provider == "gemini":
        return _ask_gemini(system, user, max_tokens)
    return None


#: Kept so existing call sites read naturally; `reason` is the real entry point.
ask_claude = reason


def classify_headlines(headlines: list[str]) -> list[dict[str, Any]]:
    """Score headlines with the cheapest model available.

    Runs over every headline, so it uses the cheap tier. The output is a filter,
    not a decision: it only decides what the reasoning model bothers reading.
    """
    if not headlines:
        return []

    numbered = "\n".join(f"{i}. {h}" for i, h in enumerate(headlines))
    prompt = (
        "Classify each market headline. Reply with JSON only, in the form "
        '{"items":[{"i":0,"sentiment":"bullish|bearish|neutral",'
        '"relevance":0.0-1.0,"topic":"macro|earnings|policy|other"}]}.\n\n'
        f"{numbered}"
    )
    system = "You output strict JSON and nothing else."

    parsed: dict[str, Any] | None = None
    if vertex_available():
        parsed = _ask_gemini(system, prompt, 900, GEMINI_FAST_MODEL)
    if parsed is None and featherless_available():
        parsed = _classify_featherless(prompt, system)

    items = (parsed or {}).get("items", [])
    return items if isinstance(items, list) else []


def _classify_featherless(prompt: str, system: str) -> dict[str, Any] | None:
    try:
        response = httpx.post(
            f"{FEATHERLESS_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {SETTINGS.featherless_api_key}"},
            json={
                "model": FEATHERLESS_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 700,
            },
            timeout=30,
        )
        response.raise_for_status()
        return _extract_json(response.json()["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001
        log.warning("Featherless classification failed: %s", exc)
        return None


def search_grounded(
    system: str, question: str, max_tokens: int = 900
) -> tuple[dict[str, Any], list[str]] | None:
    """A Google-Search-grounded call. Returns (parsed JSON, source titles).

    Vertex refuses to mix search grounding with function declarations in one
    request, so this is deliberately tool-free: it researches and returns text.
    Grounded responses cannot use a JSON response_mime_type either, so the JSON
    is parsed out of the prose.
    """
    from google.genai import types

    for model in GEMINI_REASONING_MODELS:
      for region in GROUNDING_REGIONS:
        client = _client_for(region)
        if client is None:
            continue
        try:
            response = client.models.generate_content(
                model=model,
                contents=f"{system}\n\n{question}",
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=max_tokens * OUTPUT_HEADROOM + THINKING_BUDGET,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
                ),
            )
            parsed = _extract_json(response.text)
            if parsed is None:
                continue
            sources: list[str] = []
            candidate = response.candidates[0] if response.candidates else None
            metadata = getattr(candidate, "grounding_metadata", None)
            for chunk in getattr(metadata, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                if web and getattr(web, "title", None):
                    sources.append(web.title)
            # Some grounded responses cite inline and expose only the queries
            # that were run; those still count as grounded.
            if not sources:
                sources = list(getattr(metadata, "web_search_queries", None) or [])
            return parsed, sources
        except Exception as exc:  # noqa: BLE001
            if not _retryable(exc):
                log.warning("%s/%s grounded search failed: %s", model, region, str(exc)[:160])
    log.warning("no Gemini route served a grounded search this pass")
    return None


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
