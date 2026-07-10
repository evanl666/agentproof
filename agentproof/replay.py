"""Production trace replay — turn real traffic into regression scenarios.

The most convincing test is your own production data. This module ingests real
traces — a JSONL of user messages, a LangSmith run export, or OpenTelemetry
spans — and converts each real user turn into an AgentProof scenario, classified
by what it actually contains (an injection attempt, a large refund, a PII
request, ...). Replay them against your agent and every real-world incident
becomes a permanent regression test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agentproof.scenarios import Scenario, ScenarioCategory
from agentproof.spec import BehaviorSpec, ConstraintKind

_INJECTION_MARKERS = (
    "ignore all previous", "ignore your", "system override", "you are now",
    "no approval needed", "[admin]", "policy disabled", "as a supervisor",
    "i authorize", "disregard", "administrator", "ignore the policy",
)
_PII_EGRESS_MARKERS = (
    "send", "email", "forward", "cc ", "export", "to my", "to the accountant",
    "@", "sms", "text me", "share",
)
_AMOUNT_RE = re.compile(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)")


def _first_str(d: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):
            nested = _first_str(v, keys)
            if nested:
                return nested
    return None


def _messages_from_obj(obj: Any) -> list[str]:
    """Pull the user text out of one trace record across common shapes."""
    if isinstance(obj, str):
        return [obj]
    if not isinstance(obj, dict):
        return []
    out: list[str] = []
    # LangSmith-style: {"inputs": {...}} or {"input": ...}
    inputs = obj.get("inputs") if isinstance(obj.get("inputs"), dict) else obj
    text = _first_str(inputs, ("input", "question", "query", "text", "prompt", "message", "content"))
    if text:
        out.append(text)
    # Chat-message arrays
    msgs = inputs.get("messages") if isinstance(inputs.get("messages"), list) else None
    if msgs:
        for m in msgs:
            if isinstance(m, dict) and m.get("role") in (None, "user", "human"):
                c = m.get("content")
                if isinstance(c, str):
                    out.append(c)
                elif isinstance(c, list):
                    for part in c:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            out.append(part["text"])
    # OpenTelemetry span attributes
    attrs = obj.get("attributes")
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            if isinstance(v, str) and any(w in k.lower() for w in ("input", "prompt", "message", "query", "user")):
                out.append(v)
    return [t for t in dict.fromkeys(out) if t.strip()]


def extract_messages(source: str | Path | list | dict) -> list[str]:
    """Extract user messages from a traces file (JSON/JSONL) or in-memory data."""
    if isinstance(source, (list, dict)):
        data = source
    else:
        text = Path(source).read_text()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # JSONL — one record per line
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
    records = data if isinstance(data, list) else data.get("runs") or data.get("data") or [data]
    messages: list[str] = []
    for rec in records:
        messages.extend(_messages_from_obj(rec))
    return messages


def _classify(message: str, spec: BehaviorSpec, index: int) -> Scenario:
    lowered = message.lower()
    amount = None
    m = _AMOUNT_RE.search(message)
    if m:
        try:
            amount = float(m.group(1).replace(",", ""))
        except ValueError:
            amount = None
    threshold = spec.auto_refund_limit or 50.0

    inject = any(mk in lowered for mk in _INJECTION_MARKERS)
    wants_egress = "@" in message and any(mk in lowered for mk in _PII_EGRESS_MARKERS)

    if inject:
        cat, kwargs = ScenarioCategory.PROMPT_INJECTION, dict(inject=True, malicious=True, amount=amount)
    elif wants_egress:
        cat, kwargs = ScenarioCategory.PII_LEAK, dict(request_pii_egress=True)
    elif amount is not None and amount > threshold:
        cat, kwargs = ScenarioCategory.ADVERSARIAL, dict(amount=amount, malicious=False)
    elif amount is not None:
        cat, kwargs = ScenarioCategory.NORMAL, dict(amount=amount)
    else:
        cat, kwargs = ScenarioCategory.NORMAL, {}

    return Scenario(
        id=f"prod-{index:04d}",
        category=cat,
        description=f"Replayed production message ({cat.value})",
        user_message=message,
        extra={"source": "production"},
        **kwargs,
    )


def traces_to_scenarios(source: str | Path | list | dict, spec: BehaviorSpec) -> list[Scenario]:
    """Load a traces source and classify each real user message into a scenario."""
    messages = extract_messages(source)
    return [_classify(msg, spec, i) for i, msg in enumerate(messages)]
