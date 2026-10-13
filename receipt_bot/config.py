from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_telegram_user_id: int
    data_dir: Path
    database_url: str
    confidence_threshold: float = 0.90
    translation_model: str = "Helsinki-NLP/opus-mt-tc-big-sh-en"
    qr_allowed_hosts: tuple[str, ...] = ("suf.purs.gov.rs", "tap.suf.purs.gov.rs", "*.suf.purs.gov.rs")
    qr_timeout_seconds: float = 20.0
    qr_max_response_bytes: int = 2_000_000

    @classmethod
    def from_environment(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        user_id = os.getenv("ALLOWED_TELEGRAM_USER_ID", "").strip()
        if not token or not user_id:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN and ALLOWED_TELEGRAM_USER_ID must be set in .env"
            )

        data_dir = Path(os.getenv("DATA_DIR", "./data")).expanduser().resolve()
        database_url = os.getenv(
            "DATABASE_URL",
            "postgresql://receiptbot:receiptbot@postgres:5432/receipts",
        ).strip()
        if not database_url.startswith("postgres"):
            raise RuntimeError("DATABASE_URL must point to PostgreSQL")
        threshold = float(os.getenv("OCR_CONFIDENCE_THRESHOLD", "0.90"))
        if not 0 < threshold <= 1:
            raise RuntimeError("OCR_CONFIDENCE_THRESHOLD must be between 0 and 1")

        return cls(
            telegram_bot_token=token,
            allowed_telegram_user_id=int(user_id),
            data_dir=data_dir,
            database_url=database_url,
            confidence_threshold=threshold,
            translation_model=os.getenv(
                "TRANSLATION_MODEL", "Helsinki-NLP/opus-mt-tc-big-sh-en"
            ),
            qr_allowed_hosts=tuple(
                value.strip().lower()
                for value in os.getenv(
                    "QR_ALLOWED_HOSTS",
                    "suf.purs.gov.rs,tap.suf.purs.gov.rs,*.suf.purs.gov.rs",
                ).split(",")
                if value.strip()
            ),
            qr_timeout_seconds=float(os.getenv("QR_TIMEOUT_SECONDS", "20")),
            qr_max_response_bytes=int(os.getenv("QR_MAX_RESPONSE_BYTES", "2000000")),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "files").mkdir(exist_ok=True)
        (self.data_dir / "pages").mkdir(exist_ok=True)
        (self.data_dir / "models").mkdir(exist_ok=True)
