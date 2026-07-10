"""Behavior specification: the contract an agent must honor.

A spec is written in natural language ("The agent should ... / must never ...")
and compiled into structured capabilities and constraints. Constraints are the
oracle for simulation: every generated test checks the agent's behavior against
them, regardless of what the graph looks like.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConstraintKind(str, Enum):
    SPEND_LIMIT = "spend_limit"
    APPROVAL_REQUIRED = "approval_required"
    HIGH_RISK_ACTION = "high_risk_action"
    PII_EGRESS = "pii_egress"
    SENSITIVE_EGRESS = "sensitive_egress"
    TOOL_FAILURE = "tool_failure"
    PROMPT_INJECTION = "prompt_injection"
    MEMORY_POISON = "memory_poison"
    CUSTOM = "custom"


DEFAULT_SPEND_THRESHOLD = 50.0


def coerce_threshold(value: Any, default: float = DEFAULT_SPEND_THRESHOLD) -> float:
    """Return a numeric spend threshold from whatever the parser produced.

    The LLM parser sometimes emits a non-numeric threshold — a symbolic string
    like ``"order_total"``, a formatted amount like ``"$100"``, or ``None`` when
    the limit was described in prose. Pull the first number out if there is one,
    otherwise fall back to a safe default so the whole pipeline can't crash on it.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if m:
            return float(m.group())
    return default


@dataclass
class Constraint:
    id: str
    kind: ConstraintKind
    description: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "description": self.description,
            "params": self.params,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Constraint":
        return Constraint(
            id=data["id"],
            kind=ConstraintKind(data["kind"]),
            description=data["description"],
            params=data.get("params", {}),
        )


@dataclass
class Capability:
    id: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "description": self.description}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Capability":
        return Capability(id=data["id"], description=data["description"])


@dataclass
class BehaviorSpec:
    name: str
    capabilities: list[Capability] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)

    @property
    def auto_refund_limit(self) -> float | None:
        for c in self.constraints:
            if c.kind == ConstraintKind.SPEND_LIMIT:
                return coerce_threshold(c.params.get("threshold"))
        return None

    def constraint(self, kind: ConstraintKind) -> Constraint | None:
        for c in self.constraints:
            if c.kind == kind:
                return c
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "constraints": [c.to_dict() for c in self.constraints],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "BehaviorSpec":
        return BehaviorSpec(
            name=data["name"],
            capabilities=[Capability.from_dict(c) for c in data["capabilities"]],
            constraints=[Constraint.from_dict(c) for c in data["constraints"]],
        )


_AMOUNT = r"\$?\s*([0-9]+(?:\.[0-9]+)?)"


def _extract_amount(text: str) -> float | None:
    match = re.search(_AMOUNT, text)
    if match and ("$" in text or re.search(r"\d", match.group(1))):
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _classify_never(line: str, index: int) -> Constraint:
    lowered = line.lower()
    if "pii" in lowered or "personal" in lowered or "customer data" in lowered:
        return Constraint(
            id=f"never-{index}",
            kind=ConstraintKind.PII_EGRESS,
            description=line,
        )
    if "tool error" in lowered or "tool failure" in lowered or "ignore" in lowered and "error" in lowered:
        return Constraint(
            id=f"never-{index}",
            kind=ConstraintKind.TOOL_FAILURE,
            description=line,
        )
    if (
        "memory" in lowered
        or "long-term" in lowered
        or "long term" in lowered
        or "persist" in lowered
        or "remember" in lowered and "poison" in lowered
    ):
        return Constraint(
            id=f"never-{index}",
            kind=ConstraintKind.MEMORY_POISON,
            description=line,
        )
    if (
        "injection" in lowered
        or "instructions from" in lowered
        or "untrusted" in lowered
        or "customer-provided" in lowered
    ):
        return Constraint(
            id=f"never-{index}",
            kind=ConstraintKind.PROMPT_INJECTION,
            description=line,
        )
    if "refund" in lowered and ("more than" in lowered or "above" in lowered or "over" in lowered):
        amount = _extract_amount(line)
        params = {"threshold": amount} if amount is not None else {}
        return Constraint(
            id=f"never-{index}",
            kind=ConstraintKind.SPEND_LIMIT,
            description=line,
            params=params,
        )
    # Generic sensitive-data egress (beyond PII): secrets, credentials, source code.
    from agentproof.risk import classify_action, action_needs_approval, is_sensitive

    if is_sensitive(line) and any(w in lowered for w in ("send", "leak", "expose", "share", "external", "email", "post", "reveal")):
        return Constraint(
            id=f"never-{index}",
            kind=ConstraintKind.SENSITIVE_EGRESS,
            description=line,
        )
    # Generic high-risk action (delete, deploy, grant admin, ...) needing approval.
    category = classify_action(line)
    if category is not None and action_needs_approval(category):
        return Constraint(
            id=f"never-{index}",
            kind=ConstraintKind.HIGH_RISK_ACTION,
            description=line,
            params={"category": category.value},
        )
    # Custom constraint plugins declare themselves via keywords in the line.
    from agentproof.plugins import match_plugin

    plugin = match_plugin(line)
    if plugin is not None:
        return Constraint(
            id=f"never-{index}",
            kind=ConstraintKind.CUSTOM,
            description=line,
            params={"plugin": plugin.kind},
        )
    return Constraint(id=f"never-{index}", kind=ConstraintKind.CUSTOM, description=line)


def parse_spec(text: str, name: str | None = None) -> BehaviorSpec:
    """Compile a natural-language behavior spec into a structured contract.

    Understands two shapes:
    - Markdown with "should" / "must never" bullet sections.
    - Free-form prose ("Refunds under $50 are automatic. Never send PII externally.").
    """
    lines = [ln.strip() for ln in text.splitlines()]
    title = name
    capabilities: list[Capability] = []
    constraints: list[Constraint] = []
    section: str | None = None
    never_index = 0

    def add_capability(desc: str) -> None:
        capabilities.append(Capability(id=f"cap-{len(capabilities)}", description=desc))

    def add_spend_rule(desc: str) -> None:
        """Fold 'refund under $X automatically' / 'approval above $X' into one spend limit."""
        nonlocal never_index
        amount = _extract_amount(desc)
        if amount is None:
            return
        existing = next(
            (c for c in constraints if c.kind == ConstraintKind.SPEND_LIMIT), None
        )
        if existing is None:
            constraints.append(
                Constraint(
                    id=f"never-{never_index}",
                    kind=ConstraintKind.SPEND_LIMIT,
                    description=desc,
                    params={"threshold": amount},
                )
            )
            never_index += 1
        else:
            existing.params.setdefault("threshold", amount)
            existing.description += f"; {desc}"

    for raw in lines:
        if not raw:
            continue
        if raw.startswith("#"):
            if title is None:
                title = raw.lstrip("# ").strip()
            continue
        lowered = raw.lower()
        if "must never" in lowered or "must not" in lowered:
            section = "never"
            continue
        if "should" in lowered and raw.endswith(":"):
            section = "should"
            continue
        if raw.startswith(("-", "*")):
            item = raw.lstrip("-* ").strip()
            item_lower = item.lower()
            if section == "never":
                constraints.append(_classify_never(item, never_index))
                never_index += 1
            else:
                if "approval" in item_lower or (
                    "refund" in item_lower and ("under" in item_lower or "over" in item_lower or "above" in item_lower)
                ):
                    add_spend_rule(item)
                    if "approval" not in item_lower:
                        add_capability(item)
                else:
                    add_capability(item)
            continue
        # Free-form prose: split into sentences and classify each.
        for sentence in re.split(r"(?<=[.!?])\s+", raw):
            sentence = sentence.strip().rstrip(".")
            if not sentence:
                continue
            s_lower = sentence.lower()
            if s_lower.startswith("never") or "must never" in s_lower or "must not" in s_lower:
                constraints.append(_classify_never(sentence, never_index))
                never_index += 1
            elif "approval" in s_lower or (
                "refund" in s_lower and ("under" in s_lower or "over" in s_lower or "above" in s_lower)
            ):
                add_spend_rule(sentence)
            elif s_lower.startswith("build") or s_lower.startswith("create") or s_lower.startswith("make"):
                if title is None:
                    title = sentence
                add_capability(sentence)
            else:
                add_capability(sentence)

    # A spend limit implies approvals are the escape hatch above the threshold.
    spend = next((c for c in constraints if c.kind == ConstraintKind.SPEND_LIMIT), None)
    if spend is not None and not any(
        c.kind == ConstraintKind.APPROVAL_REQUIRED for c in constraints
    ):
        constraints.append(
            Constraint(
                id=f"never-{never_index}",
                kind=ConstraintKind.APPROVAL_REQUIRED,
                description=f"Amounts above ${spend.params.get('threshold', 0):.2f} require human approval",
                params={"threshold": spend.params.get("threshold")},
            )
        )

    return BehaviorSpec(
        name=title or "Untitled agent",
        capabilities=capabilities,
        constraints=constraints,
    )
