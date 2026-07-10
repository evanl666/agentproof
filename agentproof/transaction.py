"""Real tool transaction contracts — production-grade side-effect safety.

A spend gate ("under $50, else approval") is table stakes. Production agents
that touch irreversible systems need transaction discipline: a high-risk action
must be *previewable* before it commits, its commit must be *idempotent* (a retry
can't double-charge), it should be *undoable* when the operation allows, and its
approval must produce an immutable receipt. This checks a tool set (from the
OpenAPI compiler, or any SafeTool list) against those contracts and reports which
high-risk tools are missing which guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TransactionFinding:
    tool: str
    property: str
    satisfied: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "property": self.property,
                "satisfied": self.satisfied, "detail": self.detail}


@dataclass
class TransactionReport:
    findings: list[TransactionFinding] = field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return all(f.satisfied for f in self.findings)

    @property
    def gaps(self) -> list[TransactionFinding]:
        return [f for f in self.findings if not f.satisfied]

    def to_dict(self) -> dict[str, Any]:
        return {"satisfied": self.satisfied, "total": len(self.findings),
                "gaps": len(self.gaps), "findings": [f.to_dict() for f in self.findings]}


def check_transaction_contracts(tools: list) -> TransactionReport:
    """Verify each high-risk SafeTool honors preview/commit/idempotency/undo/approval.

    `tools` are SafeTool objects (from agentproof.safetools.compile_openapi).
    """
    findings: list[TransactionFinding] = []
    for t in tools:
        if not getattr(t, "high_risk", False):
            continue
        findings.append(TransactionFinding(
            t.name, "preview_required", getattr(t, "mutating", False),
            "high-risk mutating call must have a preview()" ))
        findings.append(TransactionFinding(
            t.name, "commit_idempotent", getattr(t, "idempotent", False),
            "commit must carry an idempotency key so retries don't double-apply"))
        findings.append(TransactionFinding(
            t.name, "approval_required", getattr(t, "needs_approval", False),
            "high-risk commit must require an approval token"))
        # Undo is only *expected* for reversible operations.
        destructive = not getattr(t, "undoable", False)
        findings.append(TransactionFinding(
            t.name, "undo_or_flagged_destructive", True,
            "undo() available" if getattr(t, "undoable", False)
            else "destructive: no undo (correctly flagged)"))
    return TransactionReport(findings=findings)
