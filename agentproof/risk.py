"""Generic risk taxonomy — the core of domain-agnostic verification.

AgentProof started refund-shaped: "spend limit", "PII egress". But the same
structure applies to *any* agent — a coding agent that can `delete_repo` or
`merge_pr`, a data agent that can `drop_table` or `export_customers`, an ops
agent that can `deploy` or `grant_admin`. This module abstracts those into a
small, generic taxonomy:

- **High-risk actions** — anything hard to reverse (move money, delete, deploy,
  grant access, send externally). They must sit behind an approval gate.
- **Sensitive data** — anything you must not leak (PII, secrets, credentials,
  health/financial records, source code). It must be guarded before egress.

Every domain-specific rule (a $50 refund limit, "never send PII") is just an
instance. The money/PII paths keep their specialized handling; everything else
flows through this generic layer.
"""

from __future__ import annotations

import re
from enum import Enum


class RiskCategory(str, Enum):
    MONEY = "money"            # refund, transfer, charge, payout, invoice
    DELETE = "delete"         # delete, drop, purge, revoke, wipe
    DEPLOY = "deploy"         # deploy, publish, release, merge, rollout
    EXTERNAL = "external"     # email, post, send, webhook, message (egress)
    ADMIN = "admin"           # grant, escalate, disable, sudo, impersonate
    DATA_WRITE = "data_write" # update, insert, overwrite, modify records


# Verb/noun fragments that mark a tool or a "must never" line as a high-risk
# action of each category. Order matters — money and delete are checked first
# so a "delete payment" reads as delete, and "refund" as money.
_ACTION_KEYWORDS: list[tuple[RiskCategory, tuple[str, ...]]] = [
    (RiskCategory.MONEY, ("refund", "reimburse", "payment", "transfer", "wire",
                          "charge", "payout", "disburse", "invoice", "copay", "send money")),
    (RiskCategory.DELETE, ("delete", "drop", "purge", "revoke", "wipe", "remove",
                           "destroy", "terminate", "close account", "deactivate")),
    (RiskCategory.DEPLOY, ("deploy", "publish", "release", "merge", "rollout",
                           "ship", "push to prod", "promote", "go live")),
    (RiskCategory.ADMIN, ("grant", "escalate", "disable", "sudo", "impersonate",
                          "make admin", "elevate", "give access", "add role", "override")),
    (RiskCategory.EXTERNAL, ("email", "send email", "post", "webhook", "notify",
                             "message", "sms", "slack", "tweet", "share externally")),
    (RiskCategory.DATA_WRITE, ("update", "insert", "overwrite", "modify record",
                               "write to", "edit database", "change record")),
]

# Non-reversible categories always need an approval gate; DATA_WRITE and plain
# EXTERNAL are lower risk (they gate on sensitive content, not the action).
_ALWAYS_APPROVE = {RiskCategory.MONEY, RiskCategory.DELETE, RiskCategory.DEPLOY, RiskCategory.ADMIN}

_SENSITIVE_NOUNS = (
    "pii", "personal", "personally identifiable", "customer data", "customer record",
    "secret", "credential", "api key", "password", "token", "private key",
    "ssn", "social security", "health record", "medical", "phi", "diagnosis",
    "financial record", "bank", "card number", "credit card", "source code", "proprietary",
)

_EGRESS_HINTS = ("email", "mail", "send", "post", "webhook", "http", "external",
                 "slack", "sms", "notify", "message", "forward", "share", "upload", "tweet")
_DATASOURCE_HINTS = ("lookup", "search", "fetch", "get", "read", "query", "customer",
                     "record", "account", "database", "db", "kb", "knowledge", "profile", "patient")


def _has(text: str, keywords: tuple[str, ...]) -> str | None:
    for kw in keywords:
        if re.search(rf"\b{re.escape(kw)}", text):
            return kw
    return None


def classify_action(text: str) -> RiskCategory | None:
    """Return the high-risk category a phrase implies, or None."""
    lowered = text.lower()
    for category, keywords in _ACTION_KEYWORDS:
        if _has(lowered, keywords):
            return category
    return None


def action_needs_approval(category: RiskCategory) -> bool:
    return category in _ALWAYS_APPROVE


def is_sensitive(text: str) -> bool:
    lowered = text.lower()
    return any(n in lowered for n in _SENSITIVE_NOUNS)


def infer_tool_risk(label: str) -> dict:
    """Infer generic risk config for a tool from its name/label.

    Produces the flags the simulator and synthesis read:
    - high_risk / risk_category   — a hard-to-reverse action
    - external                    — an egress channel
    - sensitive / returns_pii     — a sensitive-data source
    - spend                       — money is still special-cased for thresholds
    """
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", label)
    text = re.sub(r"[_\-/]", " ", text).lower()
    config: dict = {}
    category = classify_action(text)
    if category is not None:
        config["risk_category"] = category.value
        if action_needs_approval(category):
            config["high_risk"] = True
        if category == RiskCategory.MONEY:
            config["spend"] = True
        if category == RiskCategory.EXTERNAL:
            config["external"] = True
    if _has(text, _EGRESS_HINTS):
        config["external"] = True
    if _has(text, _DATASOURCE_HINTS) and not config.get("external"):
        config["datasource"] = "db"
        config["returns_pii"] = True
        config["sensitive"] = True
    return config
