from pathlib import Path

from receipt_bot.db import ReceiptRepository
from receipt_bot.translation import CachedTranslator


class CountingTranslator:
    def __init__(self) -> None:
        self.calls = 0

    def translate(self, text: str) -> str:
        self.calls += 1
        return f"EN:{text}"


def test_translation_is_cached_in_sqlite(tmp_path: Path) -> None:
    repository = ReceiptRepository(tmp_path / "receipts.sqlite3")
    repository.initialise()
    delegate = CountingTranslator()
    translator = CachedTranslator(repository, delegate)

    assert translator.translate("HLEB") == "EN:HLEB"
    assert translator.translate(" hleb ") == "EN:HLEB"
    assert delegate.calls == 1
