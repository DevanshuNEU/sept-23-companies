"""The guards that keep every other page, and any agent, from reading or writing through the API.

`GuardMiddleware` is plain ASGI and the outermost layer of the app, so it decides from the headers alone: a refused
request never has its body read and never reaches a router, a handler or the static files. The rules:

1. The Host must be this app's own address (`127.0.0.1:<port>` or `localhost:<port>`) on every method and every
   path. Anything else is DNS rebinding: a foreign name that resolves to this machine.
2. A write (`POST`, `PUT`, `PATCH`, `DELETE`) must carry an Origin from this app's own pages (or a configured dev
   origin), `Content-Type: application/json`, and, when the browser sends it, `Sec-Fetch-Site: same-origin`. A
   plain `curl` call sends no Origin, so it is refused; an HTML form cannot send JSON, so it is refused too.
3. A read (`GET`, `HEAD`) may omit the Origin (browsers leave it off same-origin GETs), but one from outside the set,
   or `Sec-Fetch-Site: cross-site`, is refused. A read the browser is loading into a frame (`Sec-Fetch-Dest`
   `iframe`, `frame`, `embed` or `object`) is refused unless `Sec-Fetch-Site` is `same-origin`.
4. The API never sends CORS headers. Any `access-control-*` header on a response is removed on the way out.
   Every response, a refusal too, says it may not be framed (`Content-Security-Policy: frame-ancestors 'none'`,
   `X-Frame-Options: DENY`) and must not be sniffed (`X-Content-Type-Options: nosniff`). A page on another
   127.0.0.1 port is same-site, so without these it could frame the app and steer a click onto Approve.
5. `OPTIONS` (a CORS preflight) and every other method are refused: there is nothing to answer.

A refusal is the `403 forbidden` envelope with a plain message that says what was refused. It never echoes the
header it refused.
"""
from outreach.api.errors import STATUS, envelope
from outreach.api.settings import Settings

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
READ_METHODS = frozenset({"GET", "HEAD"})
JSON = "application/json"
FRAME_DESTS = frozenset({"iframe", "frame", "embed", "object"})
HARDENING = (
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
)
_HARDENING_NAMES = frozenset(name for name, _ in HARDENING)


class GuardMiddleware:
    def __init__(self, app, settings: Settings):
        self.app = app
        own = {f"127.0.0.1:{settings.port}", f"localhost:{settings.port}"}
        self.hosts = frozenset(own)
        self.origins = frozenset({f"http://{host}" for host in own} | set(settings.dev_origins))

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)  # lifespan
            return
        if scope["type"] == "websocket":  # the API has none; refuse before the handshake
            await send({"type": "websocket.close", "code": 1008})
            return
        send = _hardened(send)
        reason = self.refusal(scope)
        if reason is not None:
            await envelope("forbidden", reason, STATUS["forbidden"])(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def refusal(self, scope) -> str | None:
        """Why this request is refused, in plain words, or None when it may go on."""
        headers: dict[str, list[str]] = {}
        for name, value in scope.get("headers", ()):
            headers.setdefault(name.decode("latin-1").lower(), []).append(value.decode("latin-1"))

        def one(name: str) -> str | None:
            values = headers.get(name, [])
            if len(values) > 1:
                return "\0"  # two of a header that must be single is never one of ours
            return values[0] if values else None

        host = one("host")
        if host is None or host.lower() not in self.hosts:
            return "Refused: the Host is not this app's address."
        method = scope.get("method", "").upper()
        origin = one("origin")
        site = one("sec-fetch-site")
        if method in WRITE_METHODS:
            if origin is None:
                return "Refused: a write must come from one of this app's own pages, and it had no Origin."
            if origin not in self.origins:
                return "Refused: a write must come from one of this app's own pages, and this Origin is not one."
            if not _is_json(one("content-type")):
                return "Refused: a write must send its body as application/json."
            if site is not None and site.lower() != "same-origin":
                return "Refused: a write must come from this app's own page (Sec-Fetch-Site same-origin)."
            return None
        if method in READ_METHODS:
            dest = one("sec-fetch-dest")
            if dest is not None and dest.lower() in FRAME_DESTS and (site or "").lower() != "same-origin":
                return "Refused: this app cannot be loaded in a frame by another page."
            if origin is not None and origin not in self.origins:
                return "Refused: this Origin is not one of this app's own pages."
            if site is not None and site.lower() == "cross-site":
                return "Refused: another site cannot read this app (Sec-Fetch-Site cross-site)."
            return None
        return "Refused: this app answers only GET, HEAD, POST, PUT, PATCH and DELETE."


def _is_json(content_type: str | None) -> bool:
    if content_type is None:
        return False
    return content_type.split(";", 1)[0].strip().lower() == JSON


def _hardened(send):
    """Drop every CORS header and any framing or sniffing header a handler set, then add ours."""
    async def guarded(message):
        if message["type"] == "http.response.start":
            message = dict(message)
            message["headers"] = [
                (name, value) for name, value in message.get("headers", ())
                if not name.lower().startswith(b"access-control-") and name.lower() not in _HARDENING_NAMES
            ] + list(HARDENING)
        await send(message)

    return guarded
