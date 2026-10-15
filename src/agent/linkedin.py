"""LinkedIn sign-in (OAuth 2.0) and publishing through the official Posts API.

The owner connects once through the approval site; the access token (valid about 60 days, with no
refresh token for self-serve apps) lives in a Secrets Manager secret. Only standard library HTTP is
used so the approval Lambda needs no extra packages.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL = "https://api.linkedin.com/rest/posts"
SCOPES = "openid profile w_member_social"
CONNECT_PURPOSE = "linkedin-connect"
STATE_TTL_SECONDS = 600


class LinkedInError(RuntimeError):
    pass


# ---------- Signed links and OAuth state ----------


def connect_token(secret: str) -> str:
    """Token for the emailed "Connect LinkedIn" link, so strangers cannot start the flow."""
    return hmac.new(secret.encode(), CONNECT_PURPOSE.encode(), hashlib.sha256).hexdigest()


def connect_link(base_url: str, secret: str) -> str:
    return f"{base_url.rstrip('/')}/oauth/linkedin/start?t={connect_token(secret)}"


def make_state(secret: str, now: float | None = None) -> str:
    ts = str(int(now if now is not None else time.time()))
    mac = hmac.new(secret.encode(), f"state:{ts}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{ts}.{mac}"


def check_state(state: str, secret: str, now: float | None = None) -> bool:
    ts, _, mac = state.partition(".")
    if not ts.isdigit():
        return False
    expected = make_state(secret, int(ts)).partition(".")[2]
    fresh = (now if now is not None else time.time()) - int(ts) <= STATE_TTL_SECONDS
    return fresh and hmac.compare_digest(mac, expected)


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": SCOPES,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


# ---------- HTTP ----------


def _request(
    url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    form = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
    ).encode()
    status, _, body = _request(
        TOKEN_URL, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    if status != 200:
        raise LinkedInError(f"token exchange failed ({status}): {body[:300]!r}")
    return json.loads(body)


def userinfo(access_token: str) -> dict:
    status, _, body = _request(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    if status != 200:
        raise LinkedInError(f"userinfo failed ({status}): {body[:300]!r}")
    return json.loads(body)


def token_record(token: dict, info: dict, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    return {
        "access_token": token["access_token"],
        "expires_at": (now + timedelta(seconds=int(token.get("expires_in", 0)))).isoformat(),
        "person_urn": f"urn:li:person:{info['sub']}",
        "name": info.get("name", ""),
        "connected_at": now.isoformat(),
    }


def parse_token_record(raw: str | None) -> dict[str, Any] | None:
    """The secret starts as a random placeholder; anything that isn't a token record means
    "not connected"."""
    try:
        record = json.loads(raw or "")
    except ValueError:
        return None
    return record if isinstance(record, dict) and record.get("access_token") else None


def days_left(record: dict[str, Any] | None, now: datetime | None = None) -> float | None:
    if not record:
        return None
    delta = datetime.fromisoformat(record["expires_at"]) - (now or datetime.now(UTC))
    return delta.total_seconds() / 86400


# ---------- Publishing ----------

# Characters the Posts API "little text" format reserves; unescaped they break or reshape a post.
_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")
_HASHTAG = re.compile(r"(?<![\w#])#(\w+)")


def to_little_text(text: str) -> str:
    """Escapes reserved characters and keeps #hashtags as real LinkedIn hashtags."""
    tags: list[str] = []

    def keep(m: re.Match) -> str:
        tags.append(m.group(1))
        return f"\x00{len(tags) - 1}\x00"

    marked = _HASHTAG.sub(keep, text)
    escaped = _RESERVED.sub(r"\\\1", marked)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"{{hashtag|\\#|{tags[int(m.group(1))]}}}", escaped)


def _previous_version(version: str) -> str:
    year, month = int(version[:4]), int(version[4:6])
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return f"{year}{month:02d}"


def publish_post(access_token: str, person_urn: str, text: str, version: str) -> str:
    """Publishes a public text post and returns its URN. LinkedIn retires API versions after about
    a year, so on "version not active" (426) this steps back a month, up to six times."""
    body = json.dumps(
        {
            "author": person_urn,
            "commentary": to_little_text(text),
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
    ).encode()
    for _ in range(7):
        status, headers, resp = _request(
            POSTS_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "LinkedIn-Version": version,
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
        if status == 426:
            version = _previous_version(version)
            continue
        if status != 201:
            raise LinkedInError(f"publish failed ({status}): {resp[:300]!r}")
        lower = {k.lower(): v for k, v in headers.items()}
        return lower.get("x-restli-id") or lower.get("x-linkedin-id", "")
    raise LinkedInError("no active LinkedIn API version found; set LINKEDIN_API_VERSION")


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"
