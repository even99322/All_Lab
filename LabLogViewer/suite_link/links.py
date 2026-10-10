"""Build and read suite links such as ``labbrowser://open?v=1&file=...&trace=...``.

Only two actions exist: ``open`` (bring the program up and show something) and ``fill`` (put
values into the program's fields). There is deliberately no action that starts a measurement
or runs anything; a received link can never do more than show or fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

VERSION = 1
APPS = {"browser": "labbrowser", "measurement": "labmeasure", "latex": "lablatex", "timedomain": "labtime"}
ACTIONS = ("open", "fill")
MAX_LENGTH = 8192
_SCHEMES = {scheme: app for app, scheme in APPS.items()}


class LinkError(ValueError):
    """The text is not a valid suite link (unknown program, action or version, or unsafe)."""


@dataclass(frozen=True)
class Link:
    app: str
    action: str
    params: Mapping[str, str] = field(default_factory=dict)
    version: int = VERSION

    @property
    def scheme(self) -> str:
        return APPS[self.app]

    def url(self) -> str:
        return build_link(self.app, self.action, self.params, self.version)


def scheme_of(app: str) -> str:
    try:
        return APPS[app]
    except KeyError:
        raise LinkError(f"unknown program: {app!r}") from None


def _clean(text: object) -> str:
    value = str(text)
    if any(ord(ch) < 32 for ch in value):
        raise LinkError("control characters are not allowed")
    return value


def build_link(app: str, action: str, params: Mapping[str, object] | None = None, version: int = VERSION) -> str:
    scheme = scheme_of(app)
    if action not in ACTIONS:
        raise LinkError(f"unknown action: {action!r}")
    items = {"v": str(int(version))}
    for key, value in (params or {}).items():
        key = _clean(key)
        if not key or key == "v":
            raise LinkError(f"bad parameter name: {key!r}")
        items[key] = _clean(value)
    url = f"{scheme}://{action}?{urlencode(items, quote_via=quote)}"
    if len(url) > MAX_LENGTH:
        raise LinkError("link too long")
    return url


def parse_link(url: str) -> Link:
    if not isinstance(url, str) or len(url) > MAX_LENGTH:
        raise LinkError("not a link")
    parts = urlsplit(url.strip())
    app = _SCHEMES.get(parts.scheme.lower())
    if app is None:
        raise LinkError(f"unknown scheme: {parts.scheme!r}")
    action = parts.netloc.lower()
    if action not in ACTIONS:
        raise LinkError(f"unknown action: {action!r}")
    items = dict(parse_qsl(parts.query, keep_blank_values=True))
    try:
        version = int(items.pop("v", VERSION))
    except ValueError:
        raise LinkError("bad version") from None
    if version != VERSION:
        raise LinkError(f"unsupported link version: {version}")
    if any(any(ord(ch) < 32 for ch in text) for pair in items.items() for text in pair):
        raise LinkError("control characters are not allowed")
    return Link(app, action, items, version)
