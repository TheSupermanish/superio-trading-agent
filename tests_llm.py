"""Provider routing regression tests."""

from __future__ import annotations

import sys

from engine.agents import llm


def test_claude_falls_back_when_the_primary_model_refuses():
    calls: list[str] = []
    original_provider = llm.reasoning_provider
    original_ask = llm._ask_claude

    def fake_ask(_system: str, _user: str, _max_tokens: int, model: str):
        calls.append(model)
        return None if model == llm.CLAUDE_MODEL else {"decision": "stand_aside"}

    try:
        llm.reasoning_provider = lambda: "claude"
        llm._ask_claude = fake_ask
        result = llm.reason("system", "user")
    finally:
        llm.reasoning_provider = original_provider
        llm._ask_claude = original_ask

    assert result == {"decision": "stand_aside"}, result
    assert calls == [llm.CLAUDE_MODEL, llm.CLAUDE_FALLBACK_MODEL], calls


if __name__ == "__main__":
    mod = sys.modules[__name__]
    failed = 0
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        try:
            getattr(mod, name)()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failed} failed")
    sys.exit(1 if failed else 0)
