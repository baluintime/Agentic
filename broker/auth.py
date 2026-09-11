"""Upstox authentication: daily token, stored outside the repo with 0600.

Docs: https://upstox.com/developer/api-documentation/authentication
Access tokens are valid for one trading day and must be renewed daily. Only the
official login/approval flow is used — no headless scraping of the login page
with stored passwords or TOTP.

The token is never logged and never leaves this module except as a Bearer header.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx

from core import clock
from core.store import runtime_dir

TOKEN_FILENAME = "upstox_token.json"


@dataclass
class TokenState:
    access_token: str | None = None
    issued_on: date | None = None
    user_name: str = ""
    user_id: str = ""
    email: str = ""
    valid: bool = False
    message: str = ""

    @property
    def stale(self) -> bool:
        """Upstox tokens expire daily; anything not issued today needs a refresh."""
        return self.issued_on != clock.now().date()

    def redacted(self) -> dict:
        return {
            "has_token": bool(self.access_token),
            "issued_on": self.issued_on.isoformat() if self.issued_on else None,
            "user_name": self.user_name,
            "valid": self.valid,
            "message": self.message,
        }


class AuthManager:
    """Loads, validates and stores the daily access token."""

    def __init__(self, token_path: Path | None = None, env: dict[str, str] | None = None) -> None:
        self.env = env if env is not None else dict(os.environ)
        self.token_path = token_path or (runtime_dir() / TOKEN_FILENAME)
        self.state = TokenState()

    # -- env -----------------------------------------------------------------
    @property
    def api_key(self) -> str:
        return self.env.get("UPSTOX_API_KEY", "")

    @property
    def api_secret(self) -> str:
        return self.env.get("UPSTOX_API_SECRET", "")

    @property
    def redirect_uri(self) -> str:
        return self.env.get("UPSTOX_REDIRECT_URI", "http://localhost:8080/auth/callback")

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    # -- storage -------------------------------------------------------------
    def load(self) -> TokenState:
        if not self.token_path.exists():
            self.state = TokenState(message="no stored token")
            return self.state
        try:
            data = json.loads(self.token_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            self.state = TokenState(message=f"token file unreadable: {exc.__class__.__name__}")
            return self.state
        issued = data.get("issued_on")
        self.state = TokenState(
            access_token=data.get("access_token"),
            issued_on=date.fromisoformat(issued) if issued else None,
            user_name=data.get("user_name", ""),
            user_id=data.get("user_id", ""),
            email=data.get("email", ""),
        )
        if self.state.stale:
            self.state.message = "token is from an earlier day — log in again"
        return self.state

    def save(self, access_token: str, profile: dict | None = None) -> TokenState:
        profile = profile or {}
        payload = {
            "access_token": access_token,
            "issued_on": clock.now().date().isoformat(),
            "saved_at": clock.now().isoformat(),
            "user_name": profile.get("user_name", ""),
            "user_id": profile.get("user_id", ""),
            "email": profile.get("email", ""),
        }
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(payload))
        os.chmod(self.token_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        self.state = TokenState(
            access_token=access_token,
            issued_on=clock.now().date(),
            user_name=payload["user_name"],
            user_id=payload["user_id"],
            email=payload["email"],
            valid=True,
        )
        return self.state

    def clear(self) -> None:
        self.token_path.unlink(missing_ok=True)
        self.state = TokenState(message="token cleared")

    # -- OAuth ---------------------------------------------------------------
    def login_url(self) -> str:
        query = urlencode(
            {
                "client_id": self.api_key,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
            }
        )
        return f"https://api.upstox.com/v2/login/authorization/dialog?{query}"

    async def exchange_code(self, code: str, client: httpx.AsyncClient | None = None) -> TokenState:
        owns = client is None
        client = client or httpx.AsyncClient(timeout=15.0)
        try:
            response = await client.post(
                "https://api.upstox.com/v2/login/authorization/token",
                data={
                    "code": code,
                    "client_id": self.api_key,
                    "client_secret": self.api_secret,
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"accept": "application/json"},
            )
            payload = response.json()
        finally:
            if owns:
                await client.aclose()
        token = payload.get("access_token")
        if not token:
            self.state = TokenState(message=f"login failed: {payload.get('error', 'no token')}")
            return self.state
        return self.save(token, payload)

    # -- validation ----------------------------------------------------------
    async def validate(self, rest) -> TokenState:
        """Confirm the token by calling the user-profile endpoint."""
        if not self.state.access_token:
            self.state.valid = False
            self.state.message = self.state.message or "no token"
            return self.state
        rest.access_token = self.state.access_token
        try:
            profile = await rest.profile()
        except Exception as exc:
            self.state.valid = False
            self.state.message = f"token rejected: {exc}"
            return self.state
        self.state.valid = True
        self.state.user_name = profile.get("user_name", self.state.user_name)
        self.state.user_id = profile.get("user_id", self.state.user_id)
        self.state.email = profile.get("email", self.state.email)
        self.state.message = "token valid"
        return self.state

    def expiry_warning(self, now: datetime | None = None) -> str:
        now = now or clock.now()
        if not self.state.access_token:
            return "not logged in"
        if self.state.stale:
            return "token expired — daily login required"
        return ""
