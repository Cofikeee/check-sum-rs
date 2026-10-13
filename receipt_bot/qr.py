from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageEnhance, ImageOps


class QrDecodeError(RuntimeError):
    """Raised when no readable QR payload is present in a document page."""


class QrCodeReader:
    """Decode QR symbols locally without sending the receipt image anywhere."""

    def read(self, image_path: Path) -> list[str]:
        try:
            import numpy as np
            import zxingcpp
        except ImportError as error:  # pragma: no cover - Docker supplies it
            raise RuntimeError("The zxing-cpp QR decoder is not installed") from error

        payloads: list[str] = []
        with Image.open(image_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            for variant in self._variants(image):
                try:
                    barcodes = zxingcpp.read_barcodes(np.asarray(variant))
                except Exception:
                    continue
                for barcode in barcodes:
                    value = str(getattr(barcode, "text", "") or "").strip()
                    if value and value not in payloads:
                        payloads.append(value)
        if not payloads:
            raise QrDecodeError(f"No readable QR code found in {image_path.name}")
        return payloads

    @staticmethod
    def _variants(image: Image.Image) -> Iterable[Image.Image]:
        # Keep the first pass cheap, then try common phone-photo failure modes:
        # low contrast, small symbols and mild rotation.
        gray = ImageOps.grayscale(image)
        enhanced = ImageEnhance.Contrast(gray).enhance(1.8)
        threshold = enhanced.point(lambda value: 255 if value > 160 else 0)
        candidates = [image, gray, enhanced, threshold]
        for angle in (90, 180, 270):
            candidates.append(image.rotate(angle, expand=True))
        for candidate in candidates:
            yield candidate
            width, height = candidate.size
            if max(width, height) < 2400:
                scale = min(3.0, 2400 / max(width, height))
                yield candidate.resize(
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    Image.Resampling.LANCZOS,
                )
