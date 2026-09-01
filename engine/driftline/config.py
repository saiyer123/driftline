"""Engine configuration.

Loaded from environment / .env at the repo root. The engine is paper-only in
phase 1: it refuses to start unless ALPACA_PAPER is true, so going live later
is a deliberate code + config change, never an accident.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True

    db_path: Path = REPO_ROOT / "driftline.db"
    event_log_path: Path = REPO_ROOT / "events.jsonl"
    kill_switch_path: Path = REPO_ROOT / "KILL"

    api_host: str = "127.0.0.1"
    api_port: int = 8484

    # Risk gate limits (fractions of account equity unless noted).
    # max_position_pct is sized for the ETF-rotation baseline (3 x ~30% sleeves);
    # single-stock strategies in later phases get a tighter per-name budget.
    max_position_pct: float = 0.35
    max_gross_exposure_pct: float = 1.0
    max_daily_loss_pct: float = 0.03
    max_orders_per_minute: int = 10

    def require_paper(self) -> None:
        if not self.alpaca_paper:
            raise RuntimeError(
                "ALPACA_PAPER must be true. Live trading is not enabled in this build; "
                "promotion to live is a deliberate later-phase change."
            )


settings = Settings()
