"""Researcher job: read the news, publish bounded signals.

Runs pre-market (and optionally intraday) as a separate process from the
engine. Gathers recent market news (Alpaca news API) plus a compact price
summary from the ledger's stored bars, asks Claude for a structured
ResearchReport, and writes ResearchSignal events + a journal entry to the
ledger. The engine picks them up through the SignalStore (clamped, staleness-
decayed) at its next decision point. Nothing here can place an order.

    uv run python -m driftline.cognition.research
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from ..config import settings
from ..core.bus import EventBus
from ..core.events import JournalEntry, ResearchSignal
from ..ledger.repo import LedgerRepo
from ..strategy.baseline_momentum import UNIVERSE
from .claude import MODEL, make_client, structured_call
from .schemas import ResearchReport

log = logging.getLogger(__name__)

SYSTEM = """You are the research analyst for Driftline, a conservative long-only \
swing-trading system holding US-listed ETFs for days to weeks. Your output is a \
bounded risk parameter and per-ETF tilts — deterministic code makes all trading \
decisions and hard risk limits apply downstream regardless of what you output.

Ground every judgment in the news and price data provided in the prompt — do not \
rely on what you believe about markets from training, and never assume today's \
date or prices from memory. If the evidence is thin or mixed, say so and stay \
near neutral (risk_appetite 0.9-1.0, tilts near 0). Reserve low risk_appetite \
(0.3-0.6) for clear, current, broad-market stress visible in the provided \
material. You are rewarded for calibration, not boldness."""


def gather_news(hours: int = 36, max_items: int = 40) -> list[dict]:
    client = NewsClient(settings.alpaca_api_key, settings.alpaca_secret_key)
    req = NewsRequest(
        start=datetime.now(timezone.utc) - timedelta(hours=hours),
        symbols=",".join(UNIVERSE),
        include_content=False,
        limit=max_items,
    )
    news = client.get_news(req)
    items = []
    for article in news.data.get("news", []):
        items.append({
            "at": str(article.created_at),
            "headline": article.headline,
            "summary": (article.summary or "")[:300],
            "symbols": list(article.symbols or []),
            "source": article.source,
        })
    return items


def price_summary(repo: LedgerRepo) -> list[str]:
    lines = []
    for symbol in UNIVERSE:
        bars = repo.daily_bars(symbol, limit=64)
        if len(bars) < 6:
            continue
        last = bars[-1]["close"]
        d5 = last / bars[-6]["close"] - 1 if len(bars) >= 6 else 0
        d63 = last / bars[0]["close"] - 1
        lines.append(f"{symbol}: last {last:.2f}, 5d {d5:+.1%}, {len(bars)}d {d63:+.1%}")
    return lines


def build_prompt(news: list[dict], prices: list[str]) -> str:
    news_lines = [
        f"- [{n['at']}] ({', '.join(n['symbols'])}) {n['headline']}"
        + (f" — {n['summary']}" if n["summary"] else "")
        for n in news
    ] or ["(no news items returned in the window)"]
    return (
        f"Current UTC time: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"Universe: {', '.join(UNIVERSE)}\n\n"
        "Recent price action (from our own stored bars):\n" + "\n".join(prices) +
        "\n\nNews from the last 36 hours:\n" + "\n".join(news_lines) +
        "\n\nProduce your ResearchReport for this universe."
    )


async def run_once() -> ResearchReport | None:
    repo = LedgerRepo(settings.db_path)
    bus = EventBus(log_path=settings.event_log_path)
    bus.subscribe("*", repo.on_event)

    news = gather_news()
    prices = price_summary(repo)
    log.info("gathered %d news items, %d price lines", len(news), len(prices))

    client = make_client()
    report = structured_call(client, SYSTEM, build_prompt(news, prices), ResearchReport)

    regime_value = max(0.3, min(1.0, report.risk_appetite))
    await bus.publish(ResearchSignal(
        kind="regime", key="market", value=regime_value,
        confidence=report.regime_confidence, reasoning=report.regime_reasoning,
        source_model=MODEL,
    ))
    for a in report.symbols:
        if a.symbol not in UNIVERSE:
            continue  # never let a hallucinated ticker into the ledger
        await bus.publish(ResearchSignal(
            kind="symbol_tilt", key=a.symbol,
            value=max(-1.0, min(1.0, a.tilt)),
            confidence=a.confidence, reasoning=a.reasoning, source_model=MODEL,
        ))
    await bus.publish(JournalEntry(
        strategy="researcher", strategy_version=MODEL, kind="research",
        text=(
            f"Regime: {report.regime} (risk appetite {regime_value:.2f}, "
            f"confidence {report.regime_confidence:.2f}). {report.regime_reasoning}"
            + (f" Risks: {'; '.join(report.notable_risks)}" if report.notable_risks else "")
        ),
        payload={"news_items": len(news)},
    ))
    bus.close()
    log.info("published regime=%s risk_appetite=%.2f and %d symbol tilts",
             report.regime, regime_value, len(report.symbols))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
