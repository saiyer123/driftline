"""Single-stock watchlist for the earnings-drift module.

Liquid US large caps across sectors — kept deliberately disjoint from the
ETF UNIVERSE so the two strategies never contend for the same symbol.
"""

WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
    "JPM", "V", "UNH", "HD", "COST", "MRK", "PEP", "XOM",
    "CAT", "CRM", "AMD", "NFLX",
]
