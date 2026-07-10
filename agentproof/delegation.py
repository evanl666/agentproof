"""Multi-agent delegation coverage — the blue-ocean risk in agent systems.

When a parent agent delegates to a subagent, the subagent must not gain more
authority than the parent was granted. A read-only research subagent shouldn't
be able to move money; a subagent shouldn't inherit the parent's admin scope
unless explicitly passed. This models a coordinator → subagents → tools topology
and checks scope propagation: every subagent's tool scope must be a subset of
what the parent delegated, and no forbidden capability may leak downward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# A "scope" is a set of capability tags a subagent is allowed to use, e.g.
# {"read", "email"} or {"read", "money", "delete"}.


@dataclass
class SubAgent:
    name: str
    granted_scope: set[str]          # what the parent explicitly delegated
    tool_scopes: set[str]            # what its tools actually require

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "granted_scope": sorted(self.granted_scope),
                "tool_scopes": sorted(self.tool_scopes)}


@dataclass
class DelegationFinding:
    subagent: str
    kind: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"subagent": self.subagent, "kind": self.kind, "detail": self.detail}


@dataclass
class DelegationReport:
    parent_scope: set[str]
    findings: list[DelegationFinding] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {"parent_scope": sorted(self.parent_scope), "safe": self.safe,
                "findings": [f.to_dict() for f in self.findings]}


# Capabilities that must never be silently inherited without explicit grant.
_DANGEROUS = {"money", "delete", "deploy", "admin"}


def check_delegation(parent_scope: set[str], subagents: list[SubAgent]) -> DelegationReport:
    """Verify each subagent stays within its delegated scope and doesn't escalate."""
    report = DelegationReport(parent_scope=set(parent_scope))
    for sub in subagents:
        # 1. A subagent can't be granted more than the parent holds.
        over_grant = sub.granted_scope - parent_scope
        if over_grant:
            report.findings.append(DelegationFinding(
                sub.name, "scope_escalation",
                f"granted {sorted(over_grant)} which the parent does not hold"))
        # 2. A subagent's tools can't require capabilities it wasn't granted.
        unauthorized = sub.tool_scopes - sub.granted_scope
        if unauthorized:
            report.findings.append(DelegationFinding(
                sub.name, "unauthorized_tool",
                f"uses tools needing {sorted(unauthorized)} beyond its granted scope"))
        # 3. Dangerous capabilities must be explicitly granted, never implicit.
        implicit_danger = (sub.tool_scopes & _DANGEROUS) - sub.granted_scope
        if implicit_danger:
            report.findings.append(DelegationFinding(
                sub.name, "forbidden_propagation",
                f"a dangerous capability {sorted(implicit_danger)} leaked without an explicit grant"))
    return report
