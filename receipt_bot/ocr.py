from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from statistics import mean
from typing import Protocol

from .models import OcrLine


class OcrEngine(Protocol):
    def extract(self, image_path: Path, page_number: int) -> tuple[list[OcrLine], str]: ...


class PaddleOcrEngine:
    """PaddleOCR adapter compatible with current and legacy result shapes."""

    def __init__(self) -> None:
        self._engines: dict[str, object] = {}

    def extract(self, image_path: Path, page_number: int) -> tuple[list[OcrLine], str]:
        results: list[tuple[str, list[OcrLine]]] = []
        errors: list[Exception] = []
        for language in ("rs_latin", "rs_cyrillic"):
            try:
                results.append((language, self._extract_language(image_path, page_number, language)))
            except Exception as error:
                errors.append(error)
        if not results:
            raise RuntimeError("PaddleOCR failed for both Serbian script models") from errors[-1]
        language, lines = max(results, key=lambda result: self._score(result[0], result[1]))
        return lines, language

    @staticmethod
    def _score(language: str, lines: list[OcrLine]) -> float:
        if not lines:
            return 0.0
        score = mean(line.confidence for line in lines) * min(len(lines), 20)
        if language == "rs_cyrillic":
            # The Cyrillic model preserves Serbian fiscal labels (УКУПАН
            # ИЗНОС, ПЛАТНА КАРТИЦА) that the Latin model often transliterates
            # into unusable fragments. Prefer it when it actually detected
            # Cyrillic text, even if its raw confidence is slightly lower.
            cyrillic_chars = sum(
                1 for line in lines for char in line.text if "\u0400" <= char <= "\u04ff"
            )
            score += min(cyrillic_chars / 20.0, 4.0)
        return score

    def _engine(self, language: str) -> object:
        if language not in self._engines:
            try:
                from paddleocr import PaddleOCR
            except ImportError as error:
                raise RuntimeError("PaddleOCR is not installed. Run the service through Docker.") from error
            # PaddlePaddle's CPU OneDNN path is not compatible with the
            # current PP-OCR model graph on all slim Debian hosts.
            # Disable it for predictable CPU inference on the pilot box.
            self._engines[language] = PaddleOCR(lang=language, enable_mkldnn=False)
        return self._engines[language]

    def _extract_language(self, image_path: Path, page_number: int, language: str) -> list[OcrLine]:
        engine = self._engine(language)
        if hasattr(engine, "predict"):
            prediction = next(iter(engine.predict(str(image_path))))
            payload = prediction.json if hasattr(prediction, "json") else prediction
            if callable(payload):
                payload = payload()
            if not isinstance(payload, dict) and hasattr(payload, "to_dict"):
                payload = payload.to_dict()
            if isinstance(payload, dict):
                result = payload.get("res", payload)
                texts = result.get("rec_texts", [])
                scores = result.get("rec_scores", [0.0] * len(texts))
                boxes = result.get("dt_polys", [None] * len(texts))
                return [
                    OcrLine(str(text), float(score), page_number, self._bbox(box))
                    for text, score, box in zip(texts, scores, boxes)
                    if str(text).strip()
                ]
        # PaddleOCR 2.x uses .ocr() and returns polygon/(text, confidence) pairs.
        raw = engine.ocr(str(image_path), cls=True)
        page = raw[0] if raw else []
        return [
            OcrLine(str(recognition[0]), float(recognition[1]), page_number, self._bbox(box))
            for box, recognition in page
            if str(recognition[0]).strip()
        ]

    @staticmethod
    def _bbox(points: object) -> tuple[float, float, float, float] | None:
        try:
            flattened = [float(value) for point in points for value in point]  # type: ignore[union-attr]
            return min(flattened[::2]), min(flattened[1::2]), max(flattened[::2]), max(flattened[1::2])
        except (TypeError, ValueError):
            return None


class StaticOcrEngine:
    """Small deterministic OCR adapter for tests and local parser experiments."""

    def __init__(self, lines: Iterable[OcrLine], language: str = "rs_latin") -> None:
        self.lines = list(lines)
        self.language = language

    def extract(self, image_path: Path, page_number: int) -> tuple[list[OcrLine], str]:
        return [
            OcrLine(line.text, line.confidence, page_number, line.bbox) for line in self.lines
        ], self.language
