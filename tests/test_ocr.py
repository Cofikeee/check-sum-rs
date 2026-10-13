from pathlib import Path

from receipt_bot.models import OcrLine
from receipt_bot.ocr import StaticOcrEngine


def test_static_ocr_assigns_the_current_page() -> None:
    engine = StaticOcrEngine([OcrLine("MAXI", 0.95, 99)], "rs_latin")
    lines, language = engine.extract(Path("unused.png"), 3)
    assert language == "rs_latin"
    assert lines == [OcrLine("MAXI", 0.95, 3)]
