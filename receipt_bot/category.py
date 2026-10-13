from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Protocol


@dataclass(frozen=True)
class CategoryResult:
    wide: str
    narrow: str
    confidence: float
    source: str


class CategoryClassifier(Protocol):
    def classify(self, product_name: str, merchant_name: str | None = None) -> CategoryResult: ...


class HeuristicCategoryClassifier:
    """Fast, deterministic fallback used when no paid AI key is configured."""

    RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("fruit", ("jabuk", "banana", "pomor", "kruš", "krus", "grož", "groz", "limun", "narand", "orange")),
        ("vegetables", ("paradajz", "paprik", "krompir", "krump", "luk", "šarg", "sarg", "salat", "kupus", "pasulj")),
        ("dairy", ("mlek", "mleč", "mlec", "sir", "jogurt", "kajmak", "pavlak", "buter", "maslac")),
        ("meat", ("meso", "mesn", "pilet", "pileć", "pilec", "ćuret", "curet", "svin", "junet", "kobas", "šunka", "sunka")),
        ("bakery", ("hleb", "hljeb", "peciv", "kif", "kroasan", "kruh", "burek")),
        ("beverages", ("sok", "voda", "pivo", "vino", "kafa", "kava", "čaj", "caj", "cola", "energets")),
        ("snacks", ("čips", "cips", "grick", "keks", "čokol", "cokol", "bombon", "slatkiš", "slatkis")),
        ("household", ("deterž", "deterz", "sredstvo", "sunđer", "sunder", "toalet", "papir", "folija", "kesa")),
        ("personal_care", ("šampon", "sampon", "sapun", "krema", "dezodor", "pasta za zube", "pelene")),
    )

    def classify(self, product_name: str, merchant_name: str | None = None) -> CategoryResult:
        value = product_name.casefold()
        for narrow, tokens in self.RULES:
            if any(token in value for token in tokens):
                wide = "food" if narrow not in {"household", "personal_care"} else "non_food"
                return CategoryResult(wide, narrow, 0.78, "heuristic")
        merchant = (merchant_name or "").casefold()
        if any(token in merchant for token in ("restaurant", "burger", "pekara", "cafe", "kaf", "food")):
            return CategoryResult("food_service", "prepared_food", 0.62, "heuristic")
        return CategoryResult("other", "other", 0.35, "heuristic")


class OpenAICategoryClassifier:
    """Optional AI classifier; it is used only when CATEGORY_OPENAI_API_KEY is set."""

    def __init__(self, api_key: str, model: str = "gpt-4.1-mini") -> None:
        self.api_key = api_key
        self.model = model

    def classify(self, product_name: str, merchant_name: str | None = None) -> CategoryResult:
        import httpx

        prompt = (
            "Classify this Serbian retail product. Return JSON only with keys "
            "wide, narrow, confidence. Use English snake_case labels. Wide must be one of "
            "food, food_service, non_food, other. Narrow should be a useful subtype "
            "such as fruit, dairy, vegetables, meat, bakery, beverages, snacks, household, "
            "personal_care, prepared_food, or other. Do not infer a producer.\n"
            f"Merchant: {merchant_name or 'unknown'}\nProduct: {product_name}"
        )
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": prompt},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        output = body.get("output_text") or ""
        if not output:
            for item in body.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") in {"output_text", "text"}:
                        output += content.get("text", "")
        data = json.loads(output.strip().strip("`"))
        wide = re.sub(r"[^a-z0-9_]+", "_", str(data.get("wide", "other")).lower()).strip("_") or "other"
        narrow = re.sub(r"[^a-z0-9_]+", "_", str(data.get("narrow", "other")).lower()).strip("_") or "other"
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.7))))
        return CategoryResult(wide, narrow, confidence, "openai")


class CachingCategoryClassifier:
    def __init__(self, delegate: CategoryClassifier) -> None:
        self.delegate = delegate
        self.cache: dict[tuple[str, str], CategoryResult] = {}

    def classify(self, product_name: str, merchant_name: str | None = None) -> CategoryResult:
        key = (product_name.casefold(), (merchant_name or "").casefold())
        if key not in self.cache:
            self.cache[key] = self.delegate.classify(product_name, merchant_name)
        return self.cache[key]


def classifier_from_environment() -> CategoryClassifier:
    key = os.getenv("CATEGORY_OPENAI_API_KEY", "").strip()
    if key:
        return CachingCategoryClassifier(OpenAICategoryClassifier(key, os.getenv("CATEGORY_OPENAI_MODEL", "gpt-4.1-mini")))
    return CachingCategoryClassifier(HeuristicCategoryClassifier())
