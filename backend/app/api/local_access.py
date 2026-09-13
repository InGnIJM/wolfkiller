"""Keep the unauthenticated desktop API on the local machine."""

from ipaddress import ip_address
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


LOCAL_ORIGINS = tuple(
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1", "[::1]")
    for port in (5173, 4173, 8000)
)


def _is_loopback(address: str) -> bool:
    try:
        return ip_address(address).is_loopback
    except ValueError:
        return False


class LocalAccessMiddleware:
    """Reject remote peers, DNS rebinding and untrusted browser origins.

    CORS alone only controls response visibility; it does not prevent a
    browser from submitting a simple request or opening a WebSocket.
    """

    def __init__(self, app: ASGIApp, allowed_origins: tuple[str, ...] = LOCAL_ORIGINS):
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        peer = scope.get("client")
        host = headers.get("host", "")
        try:
            hostname = urlsplit(f"http://{host}").hostname or ""
        except ValueError:
            hostname = ""
        local_host = hostname == "localhost" or _is_loopback(hostname)
        scheme = "https" if scope.get("scheme") in {"https", "wss"} else "http"
        origin = headers.get("origin")
        trusted_origin = (
            origin is None or origin in self.allowed_origins
            or origin == f"{scheme}://{host}"
        )
        if not peer or not _is_loopback(peer[0]) or not local_host or not trusted_origin:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = JSONResponse({"detail": "Local access from a trusted origin is required"}, status_code=403)
                await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
