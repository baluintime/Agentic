"""Well-known Upstox URLs that more than one module needs.

Kept in core so `broker/instruments.py` and `broker/rest.py` agree on one value.
Docs: https://upstox.com/developer/api-documentation/instruments/
"""

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
MARKET_FEED_WS = "wss://api.upstox.com/v3/feed/market-data-feed"
PORTFOLIO_FEED_WS = "wss://api.upstox.com/v2/feed/portfolio-stream-feed"
