"""UI helpers that must hold without a browser."""

from __future__ import annotations

import pytest

from ui.console import DEFAULT_CALLBACK_PATH, callback_path_for


@pytest.mark.parametrize(
    "redirect_uri,expected",
    [
        ("http://localhost:8080/auth/callback", "/auth/callback"),
        ("http://127.0.0.1:8080/upstox/return", "/upstox/return"),
        ("https://my.host/callback?x=1", "/callback"),
        ("http://localhost:8080", DEFAULT_CALLBACK_PATH),
        ("http://localhost:8080/", DEFAULT_CALLBACK_PATH),
        ("", DEFAULT_CALLBACK_PATH),
    ],
)
def test_callback_route_follows_the_registered_redirect_uri(redirect_uri, expected) -> None:
    """Upstox redirects to the URI registered on your app: serve that exact path."""
    assert callback_path_for(redirect_uri) == expected
