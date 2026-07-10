"""Runtime guard middleware — enforce the behavior contract in production.

Verification proves the agent behaves in CI. Middleware makes it behave in
production: the same contract that generated the tests wraps your live agent and
blocks the bad action before it happens — an over-limit refund, a PII leak, an
injected instruction. AgentProof stops being only a pre-ship gate and becomes an
always-on guardrail.

`GuardMiddleware` is dependency-free and derived straight from the spec, so you
can import it here, or emit it as a standalone module to vendor into any agent
regardless of framework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agentproof.spec import BehaviorSpec, ConstraintKind

_INJECTION_MARKERS = (
    "ignore all previous", "ignore your", "system override", "you are now",
    "no approval needed", "[admin]", "policy disabled", "as a supervisor",
    "i authorize", "disregard", "administrator", "ignore the policy",
)
_PII_FIELDS = ("email", "address", "phone", "card", "ssn", "account", "routing", "dob")


@dataclass
class Decision:
    allowed: bool
    requires_approval: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "requires_approval": self.requires_approval, "reason": self.reason}


@dataclass
class InputScan:
    text: str
    flagged: bool
    markers: list[str] = field(default_factory=list)


class GuardBlocked(Exception):
    """Raised by the protect() decorator when input is quarantined."""


class GuardMiddleware:
    """Live enforcement of a behavior spec's guards for a production agent."""

    def __init__(self, spec: BehaviorSpec):
        self.spec = spec
        limit = spec.constraint(ConstraintKind.SPEND_LIMIT)
        self.spend_limit = float(limit.params.get("threshold")) if limit and limit.params.get("threshold") is not None else None
        self.enforce_pii = spec.constraint(ConstraintKind.PII_EGRESS) is not None
        self.enforce_injection = spec.constraint(ConstraintKind.PROMPT_INJECTION) is not None

    @classmethod
    def from_project(cls, project_dir: str | Path) -> "GuardMiddleware":
        import json

        data = json.loads((Path(project_dir) / "spec.json").read_text())
        return cls(BehaviorSpec.from_dict(data))

    # -- individual guards ----------------------------------------------

    def scan_input(self, text: str) -> InputScan:
        """Detect injected instructions in untrusted content."""
        if not self.enforce_injection:
            return InputScan(text=text, flagged=False)
        lowered = text.lower()
        markers = [mk for mk in _INJECTION_MARKERS if mk in lowered]
        return InputScan(text=text, flagged=bool(markers), markers=markers)

    def is_injection(self, text: str) -> bool:
        return self.scan_input(text).flagged

    def authorize_spend(self, amount: float, approved_by_human: bool = False) -> Decision:
        """The gate every money movement must pass at runtime."""
        if self.spend_limit is None:
            return Decision(True, False, "no spend limit configured")
        if amount <= 0:
            return Decision(False, False, "non-positive amount")
        if amount <= self.spend_limit:
            return Decision(True, False, f"within ${self.spend_limit:.2f} auto-limit")
        if approved_by_human:
            return Decision(True, True, "approved by human")
        return Decision(False, True, f"exceeds ${self.spend_limit:.2f} limit; needs human approval")

    def redact_pii(self, payload: dict) -> dict:
        """Scrub PII fields before anything leaves the system."""
        if not self.enforce_pii:
            return dict(payload)
        return {k: ("[REDACTED]" if k.lower() in _PII_FIELDS else v) for k, v in payload.items()}

    def redact_text(self, text: str) -> str:
        """Redact obvious PII (emails, long digit runs) from free text."""
        if not self.enforce_pii:
            return text
        text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[REDACTED_EMAIL]", text)
        text = re.sub(r"\b\d{9,}\b", "[REDACTED_NUMBER]", text)
        text = re.sub(r"\b(?:\d[ -]*?){13,16}\b", "[REDACTED_CARD]", text)
        return text

    # -- decorator ------------------------------------------------------

    def protect(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap an agent callable `(message, ...) -> reply`. Quarantines injected
        input (raises GuardBlocked) and redacts PII from string replies."""

        def wrapper(message: str, *args, **kwargs):
            if self.is_injection(message):
                raise GuardBlocked("input contained injected instructions and was quarantined")
            reply = fn(message, *args, **kwargs)
            if isinstance(reply, str):
                return self.redact_text(reply)
            return reply

        wrapper.__name__ = getattr(fn, "__name__", "guarded_agent")
        wrapper.__doc__ = fn.__doc__
        return wrapper


def render_middleware_module(spec: BehaviorSpec) -> str:
    """Emit a standalone, dependency-free middleware module to vendor anywhere."""
    limit = spec.constraint(ConstraintKind.SPEND_LIMIT)
    threshold = float(limit.params.get("threshold", 50.0)) if limit else None
    lines = [
        '"""Runtime guard middleware. Generated by AgentProof from the behavior spec.',
        'Vendor this file into your agent to enforce the contract in production."""',
        "import re",
        "",
        f"SPEND_LIMIT = {threshold!r}",
        f"ENFORCE_PII = {spec.constraint(ConstraintKind.PII_EGRESS) is not None!r}",
        f"ENFORCE_INJECTION = {spec.constraint(ConstraintKind.PROMPT_INJECTION) is not None!r}",
        "",
        "_INJECTION_MARKERS = " + repr(list(_INJECTION_MARKERS)),
        "_PII_FIELDS = " + repr(list(_PII_FIELDS)),
        "",
        "",
        "def is_injection(text):",
        "    if not ENFORCE_INJECTION:",
        "        return False",
        "    low = text.lower()",
        "    return any(m in low for m in _INJECTION_MARKERS)",
        "",
        "",
        "def authorize_spend(amount, approved_by_human=False):",
        "    if SPEND_LIMIT is None:",
        "        return True",
        "    if amount <= 0:",
        "        return False",
        "    if amount <= SPEND_LIMIT:",
        "        return True",
        "    return bool(approved_by_human)",
        "",
        "",
        "def redact_pii(payload):",
        "    if not ENFORCE_PII:",
        "        return dict(payload)",
        "    return {k: ('[REDACTED]' if k.lower() in _PII_FIELDS else v) for k, v in payload.items()}",
        "",
    ]
    return "\n".join(lines)


def export_middleware(spec: BehaviorSpec, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(render_middleware_module(spec))
    return path
