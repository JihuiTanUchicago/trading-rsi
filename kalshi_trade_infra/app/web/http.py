"""The web layer's request types: the API error, the per-request context, and small argument helpers
shared by app.server and the route modules in app.web.routes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Ctx:
    conn: Any
    user: Optional[Dict]
    params: Tuple[str, ...]
    query: Dict[str, str]
    body: Dict[str, Any]
    handler: Any  # app.server.Handler (the web layer never imports the server module)
    set_cookies: List[str] = field(default_factory=list)
    # What this request has read already, for a later read in the same request to take instead of asking again:
    # a page shell's account (/api/me) and the first reads it carries share one dict (Handler._shell_boot), so the
    # dashboard takes the Kalshi keys' presence and the autopilot the Stripe customer that /api/me just read.
    # Request-scoped (one user, one request, made before any write), never kept past it; a request alone has its own.
    shared: Dict[str, Any] = field(default_factory=dict)

    @property
    def cur(self):
        return self.conn.cursor()

    @property
    def uid(self) -> int:
        return self.user["id"]


def _json_default(v):
    if isinstance(v, (datetime, date)):
        return (v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v).isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return None
    raise TypeError(type(v))


# What a number field is called on its page, and how its bounds read there. A bad value answered with the body's
# own key and Python's %g ("amount must be between 1 and 1e+06", walk 2026-10-31): the reader never typed
# "amount", and 1e+06 is a million. A key not listed here reads as "a number".
_NUM_WORDS = {
    "amount": ("an amount", "$%s"),
    "fee_multiplier": ("a fee multiplier", "%s×"),
    "slippage_cents": ("a slippage", "%s¢"),
    "stake": ("a stake", "$%s"),
}


def _bound(v: float, unit: str) -> str:
    a = abs(v)
    return ("-" if v < 0 else "") + unit % ("{:,.0f}".format(a) if float(a).is_integer() else "{:,}".format(a))


def _num(body: Dict, key: str, lo: float, hi: float, default: Optional[float] = None) -> float:
    v = body.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        what, unit = _NUM_WORDS.get(key, ("a number", "%s"))
        raise ApiError(400, "Enter %s between %s and %s." % (what, _bound(lo, unit), _bound(hi, unit)))
    return float(v)


def _int_param(ctx: Ctx, i: int = 0) -> int:
    return int(ctx.params[i])
