"""Claude client for the cognition plane.

Credentials resolve automatically: ANTHROPIC_API_KEY env var, or the profile
from `ant auth login` — no key handling in this codebase. All calls use
structured outputs (messages.parse with a Pydantic schema): free text never
leaves this module.
"""

from __future__ import annotations

import logging

import anthropic
from dotenv import load_dotenv

from ..config import REPO_ROOT

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"


def make_client() -> anthropic.Anthropic:
    # export .env into the process env so the SDK's credential resolution
    # (ANTHROPIC_API_KEY -> ant auth profile) sees a key placed there
    load_dotenv(REPO_ROOT / ".env")
    return anthropic.Anthropic()


def structured_call(client: anthropic.Anthropic, system: str, prompt: str, schema, max_tokens: int = 16000):
    """One structured call; returns a validated `schema` instance.

    Raises anthropic.* exceptions to the caller — cognition jobs log-and-skip,
    they never crash anything (and the engine runs fine without them).
    """
    response = client.messages.parse(
        model=MODEL,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_format=schema,
    )
    usage = response.usage
    log.info("claude call: %d in / %d out tokens", usage.input_tokens, usage.output_tokens)
    return response.parsed_output


def credentials_hint() -> str:
    return (
        "No Claude credentials found. Either run `ant auth login`, or add "
        "ANTHROPIC_API_KEY=... to the repo .env / your shell environment."
    )
