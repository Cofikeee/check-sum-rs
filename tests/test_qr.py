from pathlib import Path

from PIL import Image
import pytest

from receipt_bot.qr import QrCodeReader, QrDecodeError


def test_qr_reader_marks_a_blank_page_as_unreadable(tmp_path: Path) -> None:
    image = tmp_path / "blank.png"
    Image.new("RGB", (300, 300), "white").save(image)
    with pytest.raises(QrDecodeError):
        QrCodeReader().read(image)
