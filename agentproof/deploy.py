"""One-click deploy — turn a verified agent into a deployable service.

Verifying an agent is only useful if shipping it is easy. This generates the
deploy artifacts for the major platforms — Docker, Fly.io, Railway, Render,
Google Cloud Run, and Modal — plus a small FastAPI server that wraps the agent
behind an HTTP endpoint with the runtime guard middleware already applied. Pick a
target (or `all`) and you get a repo you can `fly deploy` / `railway up` /
`render` / push to Cloud Run immediately. Platform-agnostic: adding a new target
is one template.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentproof.middleware import render_middleware_module
from agentproof.spec import BehaviorSpec


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "agent"


def _server_py() -> str:
    return (
        '"""HTTP service for the agent, with AgentProof runtime guards applied."""\n'
        "import os\n"
        "from fastapi import FastAPI\n"
        "from pydantic import BaseModel\n"
        "import guards  # generated runtime middleware (injection / spend / PII)\n\n"
        "app = FastAPI(title=os.environ.get('AGENT_NAME', 'agent'))\n\n\n"
        "class Msg(BaseModel):\n"
        "    message: str\n"
        "    amount: float | None = None\n"
        "    approved_by_human: bool = False\n\n\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return {'status': 'ok'}\n\n\n"
        "@app.post('/chat')\n"
        "def chat(m: Msg):\n"
        "    # Guard: quarantine injected instructions before the agent sees them.\n"
        "    if guards.is_injection(m.message):\n"
        "        return {'reply': 'I cannot act on embedded instructions.', 'blocked': True}\n"
        "    # Guard: enforce the spend policy before any money moves.\n"
        "    if m.amount is not None and not guards.authorize_spend(m.amount, m.approved_by_human):\n"
        "        return {'reply': 'That exceeds the policy limit and needs human approval.',\n"
        "                'approval_required': True}\n"
        "    # TODO: call your agent here; its reply is redacted before egress.\n"
        "    reply = f'Handled: {m.message}'\n"
        "    return {'reply': reply}\n"
    )


def _requirements() -> str:
    return "fastapi\nuvicorn[standard]\n"


def _dockerfile() -> str:
    return (
        "FROM python:3.11-slim\n"
        "WORKDIR /app\n"
        "COPY requirements.txt .\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n"
        "COPY . .\n"
        "ENV PORT=8080\n"
        "EXPOSE 8080\n"
        'CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]\n'
    )


def _flyio(name: str) -> str:
    return (
        f'app = "{name}"\n'
        'primary_region = "iad"\n\n'
        "[build]\n\n"
        "[http_service]\n"
        '  internal_port = 8080\n'
        '  force_https = true\n'
        "  auto_stop_machines = true\n"
        "  auto_start_machines = true\n"
        "  min_machines_running = 0\n\n"
        "[[http_service.checks]]\n"
        '  path = "/health"\n'
    )


def _railway() -> str:
    return (
        "{\n"
        '  "$schema": "https://railway.app/railway.schema.json",\n'
        '  "build": {"builder": "DOCKERFILE"},\n'
        '  "deploy": {"healthcheckPath": "/health", "restartPolicyType": "ON_FAILURE"}\n'
        "}\n"
    )


def _render_yaml(name: str) -> str:
    return (
        "services:\n"
        "  - type: web\n"
        f"    name: {name}\n"
        "    runtime: docker\n"
        "    healthCheckPath: /health\n"
        "    envVars:\n"
        "      - key: PORT\n"
        "        value: 8080\n"
    )


def _cloudrun(name: str) -> str:
    return (
        "apiVersion: serving.knative.dev/v1\n"
        "kind: Service\n"
        f"metadata:\n  name: {name}\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - image: gcr.io/PROJECT_ID/" + name + "\n"
        "          ports:\n            - containerPort: 8080\n"
        "          resources:\n            limits:\n              memory: 512Mi\n"
    )


def _modal(name: str) -> str:
    return (
        '"""Modal deploy: `modal deploy modal_app.py`."""\n'
        "import modal\n\n"
        f'app = modal.App("{name}")\n'
        "image = modal.Image.debian_slim().pip_install('fastapi', 'uvicorn')\n\n\n"
        "@app.function(image=image)\n"
        "@modal.asgi_app()\n"
        "def fastapi_app():\n"
        "    from server import app as web_app\n"
        "    return web_app\n"
    )


def _procfile() -> str:
    return "web: uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}\n"


# target -> {relative path: content-fn(name)}
_TARGETS: dict[str, dict[str, Callable[[str], str]]] = {
    "docker": {"Dockerfile": lambda n: _dockerfile()},
    "flyio": {"fly.toml": _flyio, "Dockerfile": lambda n: _dockerfile()},
    "railway": {"railway.json": lambda n: _railway(), "Dockerfile": lambda n: _dockerfile()},
    "render": {"render.yaml": _render_yaml, "Dockerfile": lambda n: _dockerfile()},
    "cloudrun": {"service.yaml": _cloudrun, "Dockerfile": lambda n: _dockerfile()},
    "modal": {"modal_app.py": _modal},
    "heroku": {"Procfile": lambda n: _procfile(), "Dockerfile": lambda n: _dockerfile()},
}

DEPLOY_TARGETS = sorted(_TARGETS)


def generate_deploy(spec: BehaviorSpec, target: str, out_dir: str | Path) -> list[Path]:
    """Generate deploy artifacts for `target` (or 'all') plus the guarded server."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = _slug(spec.name)
    targets = list(_TARGETS) if target == "all" else [target]
    for t in targets:
        if t not in _TARGETS:
            raise KeyError(f"Unknown deploy target {t!r}; available: {', '.join(DEPLOY_TARGETS)}, all")

    written: list[Path] = []
    # Always: the guarded HTTP server + its runtime guards + requirements.
    base = {
        "server.py": _server_py(),
        "guards.py": render_middleware_module(spec),
        "requirements.txt": _requirements(),
    }
    for rel, content in base.items():
        p = out / rel
        p.write_text(content)
        written.append(p)
    for t in targets:
        for rel, fn in _TARGETS[t].items():
            p = out / rel
            p.write_text(fn(name))
            if p not in written:
                written.append(p)
    return written
