"""Token-on-localhost auth for the dashboard.

The dashboard binds to 127.0.0.1 by default, so the network boundary is the
first line of defence. The token is the second: every request must present it,
which stops other local users (or a stray browser tab hitting localhost) from
reading your fleet. It is deliberately simple — a shared secret over loopback,
not a user system.

A request is authorised if the token appears in any of (most → least explicit):
  - Authorization: Bearer <token>
  - X-Lodestone-Token: <token>      (header, for API clients / curl)
  - ?token=<token>                  (query, for the first browser visit)
  - lodestone_token cookie          (set after a successful query-token visit)
"""

import secrets

COOKIE_NAME = "lodestone_token"


def generate_token() -> str:
    return secrets.token_urlsafe(24)


def _present(value, token: str) -> bool:
    return bool(value) and secrets.compare_digest(str(value), token)


def request_token(request) -> str:
    """Pull whatever token the request offers, or '' if none."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = request.headers.get("x-lodestone-token")
    if header:
        return header.strip()
    q = request.query_params.get("token")
    if q:
        return q.strip()
    return request.cookies.get(COOKIE_NAME, "")


def is_authorized(request, token: str) -> bool:
    return _present(request_token(request), token)
