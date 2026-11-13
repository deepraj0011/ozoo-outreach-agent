"""Test double for the LLM port: returns scripted outputs or raises scripted errors, no network."""
from __future__ import annotations

from typing import Any, Dict, List


class FakeLLM:
    """FakeLLM(out1, out2, ...): each structured() call takes the next item (the last one repeats).
    An exception item (class or instance) is raised; anything else is returned as-is, so tests can
    script malformed outputs too. Every call's kwargs are recorded in `calls`."""

    def __init__(self, *outputs: Any) -> None:
        if not outputs:
            raise ValueError("FakeLLM needs at least one scripted output")
        self._outputs = list(outputs)
        self.calls: List[Dict[str, Any]] = []

    def structured(self, *, system: str, messages: List[Dict[str, str]], tool_name: str,
                   tool_description: str, schema: Dict[str, Any], max_tokens: int,
                   model: str) -> Dict[str, Any]:
        self.calls.append(dict(system=system, messages=[dict(m) for m in messages], tool_name=tool_name,
                               tool_description=tool_description, schema=schema, max_tokens=max_tokens,
                               model=model))
        out = self._outputs[min(len(self.calls), len(self._outputs)) - 1]
        if isinstance(out, BaseException) or (isinstance(out, type) and issubclass(out, BaseException)):
            raise out
        return out
