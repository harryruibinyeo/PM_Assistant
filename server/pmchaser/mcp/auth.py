"""Optional bearer-token gate on every HTTP route this service exposes
(the MCP endpoint and /internal/peek), except /healthz - see
BearerAuthMiddleware's own docstring for why.

Deliberately NOT built on the mcp SDK's own auth framework
(MCPServer(auth=..., token_verifier=...)) - that machinery targets full
OAuth 2.1 (issuer metadata, a resource-server discovery document,
WWW-Authenticate challenge flows). What Hermes actually sends, confirmed
against its own docs, is a single static header configured once in
config.yaml - `headers: Authorization: "Bearer ${ENV_VAR}"` - not an OAuth
token exchange. Plain ASGI middleware comparing that header against one
configured secret is the right amount of machinery for what's actually
being verified here; reaching for the OAuth framework to check a static
shared secret would be solving a harder, differently-shaped problem than
the one this service has.

Off by default (see main.py: only installed when PM_CHASER_MCP_TOKEN is
set) so adopting this is an opt-in step on the real deployment, not a
silent behavior change the moment this code ships - see finding #4 in the
refactor plan and docs/MIGRATIONS.md-style rollout notes in
BRANCH_TESTING.md for why an auth change against the live bot needs a
deliberate, confirmed rollout rather than an automatic one.
"""

from __future__ import annotations

import secrets

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# /healthz is intentionally never gated: Docker's own HEALTHCHECK (and any
# future orchestrator's liveness probe) calls it with no Authorization
# header at all, from inside the same container/host - gating it would
# make the container report unhealthy the moment auth is turned on, for
# a route that only ever reveals "can this process reach its own SQLite
# file", nothing sensitive.
EXEMPT_PATHS = frozenset({"/healthz"})


class BearerAuthMiddleware:
    """Rejects any request outside EXEMPT_PATHS whose Authorization header
    isn't exactly `Bearer <token>` for the one configured token.

    `secrets.compare_digest` (not `==`) for the same reason
    domain/validation.py's generate_link_code moved off `random` to
    `secrets.choice` (see PROJECT_MANAGEMENT.md/the refactor plan's
    finding #3) - a real credential comparison shouldn't leak timing
    information about how many leading characters matched.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in EXEMPT_PATHS:
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        header = request.headers.get("authorization", "")
        prefix = "Bearer "
        supplied = header[len(prefix):] if header.startswith(prefix) else ""

        if not secrets.compare_digest(supplied, self._token):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


def wrap_if_configured(app: Starlette, token: str | None) -> ASGIApp:
    """Returns `app` unchanged if `token` is falsy (the default, current
    behavior - see this module's docstring), otherwise wrapped in
    BearerAuthMiddleware."""
    if not token:
        return app
    return BearerAuthMiddleware(app, token)
