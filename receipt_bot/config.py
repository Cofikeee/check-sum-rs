from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_telegram_user_id: int
    data_dir: Path
    database_path: Path
    confidence_threshold: float = 0.90
    translation_model: str = "Helsinki-NLP/opus-mt-tc-big-sh-en"

    @classmethod
    def from_environment(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        user_id = os.getenv("ALLOWED_TELEGRAM_USER_ID", "").strip()
        if not token or not user_id:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN and ALLOWED_TELEGRAM_USER_ID must be set in .env"
            )

        data_dir = Path(os.getenv("DATA_DIR", "./data")).expanduser().resolve()
        database_path = Path(
            os.getenv("DATABASE_PATH", str(data_dir / "receipts.sqlite3"))
        ).expanduser().resolve()
        threshold = float(os.getenv("OCR_CONFIDENCE_THRESHOLD", "0.90"))
        if not 0 < threshold <= 1:
            raise RuntimeError("OCR_CONFIDENCE_THRESHOLD must be between 0 and 1")

        return cls(
            telegram_bot_token=token,
            allowed_telegram_user_id=int(user_id),
            data_dir=data_dir,
            database_path=database_path,
            confidence_threshold=threshold,
            translation_model=os.getenv(
                "TRANSLATION_MODEL", "Helsinki-NLP/opus-mt-tc-big-sh-en"
            ),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "files").mkdir(exist_ok=True)
        (self.data_dir / "pages").mkdir(exist_ok=True)
        (self.data_dir / "models").mkdir(exist_ok=True)
