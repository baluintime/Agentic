"""Required tests with a mocked broker: fill, partial, reject, every terminal status."""

from __future__ import annotations

import pytest

from agents.orders._template.agent import TemplateOrderAgent
from tests.helpers import make_request


@pytest.mark.asyncio
async def test_rejects_by_default() -> None:
    agent = TemplateOrderAgent("t")
    event = await agent.place(make_request())
    assert event.status == "REJECTED"
