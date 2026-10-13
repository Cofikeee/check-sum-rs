from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
import shutil
from uuid import uuid4


class LocalStorage:
    def __init__(self, data_dir: Path) -> None:
        self.files_dir = data_dir / "files"
        self.pages_dir = data_dir / "pages"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.pages_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def persist_download(self, temporary_path: Path, original_name: str | None) -> tuple[Path, str, str]:
        digest = self.sha256(temporary_path)
        suffix = Path(original_name or temporary_path.name).suffix.lower()
        target = self.files_dir / f"{digest}{suffix}"
        if not target.exists():
            shutil.move(str(temporary_path), target)
        else:
            temporary_path.unlink(missing_ok=True)
        mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return target, digest, mime_type

    def page_path(self, receipt_id: int, page_number: int) -> Path:
        target = self.pages_dir / str(receipt_id)
        target.mkdir(parents=True, exist_ok=True)
        return target / f"page-{page_number}-{uuid4().hex}.png"

    @staticmethod
    def discard(path: Path) -> None:
        path.unlink(missing_ok=True)
