"""Policy-pack marketplace — a community ecosystem of behavior contracts.

Built-in packs (support/fintech/coding/sql/sales) are a start; the flywheel is
the community publishing packs for their own domains — HIPAA, SOC2, a sales-email
policy, a coding-agent policy. This is a simple file-based registry: `publish` a
spec (+ optional policy plugins) as a named, versioned pack into a local or
shared registry directory, `install` one into a project, and `search` what's
available. Point `AGENTPROOF_REGISTRY` at a shared/mounted dir to make it a team
or org registry.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def registry_dir() -> Path:
    d = Path(os.environ.get("AGENTPROOF_REGISTRY", Path.home() / ".agentproof" / "registry"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class PackEntry:
    name: str
    version: str
    description: str
    domain: str
    spec_text: str
    author: str = "community"
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "version": self.version, "description": self.description,
            "domain": self.domain, "spec_text": self.spec_text, "author": self.author,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PackEntry":
        return PackEntry(**d)


def publish_pack(name: str, spec_text: str, description: str = "", domain: str = "general",
                 version: str = "1.0.0", author: str = "community") -> PackEntry:
    entry = PackEntry(name=name, version=version, description=description, domain=domain,
                      spec_text=spec_text, author=author, created_at=time.time())
    path = registry_dir() / f"{name}@{version}.json"
    path.write_text(json.dumps(entry.to_dict(), indent=2))
    return entry


def list_registry() -> list[PackEntry]:
    out: list[PackEntry] = []
    for p in sorted(registry_dir().glob("*.json")):
        try:
            out.append(PackEntry.from_dict(json.loads(p.read_text())))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def search_packs(query: str = "") -> list[PackEntry]:
    q = query.lower()
    return [e for e in list_registry()
            if not q or q in e.name.lower() or q in e.description.lower() or q in e.domain.lower()]


def get_registry_pack(name: str, version: str | None = None) -> PackEntry:
    matches = [e for e in list_registry() if e.name == name and (version is None or e.version == version)]
    if not matches:
        raise KeyError(f"Pack {name!r}{('@' + version) if version else ''} not found in registry")
    return sorted(matches, key=lambda e: e.version)[-1]


def install_pack(name: str, out_dir: str | Path, version: str | None = None) -> Path:
    """Install a registry pack into a project directory as a spec file."""
    entry = get_registry_pack(name, version)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    spec_path = out / "agent.spec.md"
    spec_path.write_text(entry.spec_text)
    return spec_path
