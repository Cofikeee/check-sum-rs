from __future__ import annotations

from typing import Protocol

from .db import ReceiptRepository


class Translator(Protocol):
    def translate(self, text: str) -> str: ...


class LocalMarianTranslator:
    """Lazy CPU translation adapter; model files are cached below HF_HOME."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._tokenizer = None
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except ImportError as error:
            raise RuntimeError("transformers is not installed. Run the service through Docker.") from error
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name)

    def translate(self, text: str) -> str:
        clean = " ".join(text.split())
        if not clean:
            return clean
        self._load()
        assert self._tokenizer is not None and self._model is not None
        inputs = self._tokenizer(clean, return_tensors="pt", truncation=True)
        generated = self._model.generate(**inputs, max_new_tokens=128)
        return str(self._tokenizer.batch_decode(generated, skip_special_tokens=True)[0]).strip()


class CachedTranslator:
    def __init__(self, repository: ReceiptRepository, delegate: Translator) -> None:
        self.repository = repository
        self.delegate = delegate

    def translate(self, text: str) -> str:
        cached = self.repository.get_translation_override(text)
        if cached is not None:
            return cached
        translated = self.delegate.translate(text)
        self.repository.save_translation_override(text, translated)
        return translated


class IdentityTranslator:
    """Useful for tests and troubleshooting a model installation."""

    def translate(self, text: str) -> str:
        return text
