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
            if payloads:
                return payloads

            # Phone photos frequently contain a large, skewed QR at the bottom
            # of a receipt. OpenCV can find its four corners even when ZXing's
            # full-page detector cannot; rectify that quadrilateral and retry.
            payloads, detected = self._opencv_fallback(image, zxingcpp, np)
        if payloads:
            return payloads
        if detected:
            raise QrDecodeError(
                f"QR code detected but unreadable in {image_path.name}; "
                "send the original image as a Telegram file/document"
            )
        raise QrDecodeError(f"No readable QR code found in {image_path.name}")

    @staticmethod
    def _opencv_fallback(image: Image.Image, zxingcpp: object, np: object) -> tuple[list[str], bool]:
        try:
            import cv2
        except ImportError:  # pragma: no cover - only relevant outside Docker
            return [], False

        frame = np.asarray(image)
        detector = cv2.QRCodeDetector()
        payloads: list[str] = []
        detected = False

        def add(value: object) -> None:
            text = str(value or "").strip()
            if text and text not in payloads:
                payloads.append(text)

        frames = [frame, cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)]
        for candidate in frames:
            for method in ("detectAndDecode", "detectAndDecodeCurved"):
                try:
                    value, points, _ = getattr(detector, method)(candidate)
                except Exception:
                    continue
                add(value)
                if points is not None:
                    detected = True
                    for rectified in QrCodeReader._rectify(candidate, points, cv2, np):
                        for binarizer in zxingcpp.Binarizer:
                            try:
                                barcodes = zxingcpp.read_barcodes(
                                    rectified,
                                    binarizer=binarizer,
                                    try_rotate=True,
                                    try_downscale=False,
                                )
                            except Exception:
                                continue
                            for barcode in barcodes:
                                add(getattr(barcode, "text", ""))
                        try:
                            value, _, _ = detector.detectAndDecode(rectified)
                        except Exception:
                            value = ""
                        add(value)
        return payloads, detected

    @staticmethod
    def _rectify(candidate: object, points: object, cv2: object, np: object) -> Iterable[object]:
        corners = np.asarray(points, dtype=np.float32).reshape(-1, 2)
        if len(corners) != 4:
            return []
        # Order corners as top-left, top-right, bottom-right, bottom-left.
        sums = corners.sum(axis=1)
        diffs = corners[:, 1] - corners[:, 0]
        ordered = np.array(
            [
                corners[np.argmin(sums)],
                corners[np.argmin(diffs)],
                corners[np.argmax(sums)],
                corners[np.argmax(diffs)],
            ],
            dtype=np.float32,
        )
        width = int(max(np.linalg.norm(ordered[1] - ordered[0]), np.linalg.norm(ordered[2] - ordered[3])))
        height = int(max(np.linalg.norm(ordered[3] - ordered[0]), np.linalg.norm(ordered[2] - ordered[1])))
        size = max(400, min(2400, width, height))
        border = max(20, size // 25)
        target_size = size + (2 * border)
        target = np.array(
            [
                [border, border],
                [target_size - border - 1, border],
                [target_size - border - 1, target_size - border - 1],
                [border, target_size - border - 1],
            ],
            dtype=np.float32,
        )
        matrix = cv2.getPerspectiveTransform(ordered, target)
        warped = cv2.warpPerspective(
            candidate,
            matrix,
            (target_size, target_size),
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
        gray = warped if len(warped.shape) == 2 else cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY)
        yield gray
        yield cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

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
