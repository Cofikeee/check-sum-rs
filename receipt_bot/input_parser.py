from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re


UNITS = ("pcs", "kg", "g", "l", "ml", "pack", "set")
UNIT_ALIASES = {
    "kom": "pcs", "kom.": "pcs", "komada": "pcs", "ком": "pcs", "ком.": "pcs",
    "kg": "kg", "кг": "kg", "g": "g", "г": "g", "l": "l", "л": "l",
    "ml": "ml", "мл": "ml", "pak": "pack", "pack": "pack", "пак": "pack",
    "set": "set", "сет": "set",
}


@dataclass(frozen=True)
class InputDraft:
    merchant_name_source: str
    product_name_source: str
    unit: str
    quantity: Decimal


class InputParseError(ValueError):
    pass


def _quantity(value: str) -> Decimal:
    try:
        result = Decimal(value.strip().replace(",", "."))
    except InvalidOperation as error:
        raise InputParseError("Quantity must be a number") from error
    if result <= 0:
        raise InputParseError("Quantity must be greater than zero")
    return result


def _unit(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized in UNITS:
        return normalized
    if normalized in UNIT_ALIASES:
        return UNIT_ALIASES[normalized]
    raise InputParseError(f"Unknown unit '{value}'. Use one of: {', '.join(UNITS)}")


def parse_input(
    text: str, selected_merchant: str | None = None, selected_unit: str | None = None
) -> InputDraft:
    """Parse pipe format or a small natural-language form.

    Accepted examples:
    ``Lidl | Mleko | l | 2``
    ``Mleko 2 l`` when a merchant was selected in the Telegram flow.
    """
    value = " ".join(text.split()).strip()
    if not value:
        raise InputParseError("Input is empty")
    parts = [part.strip() for part in re.split(r"\s*\|\s*", value)]
    if len(parts) >= 4:
        merchant, product, unit, quantity = parts[:4]
        if not merchant or not product:
            raise InputParseError("Use: shop | product | unit | quantity")
        return InputDraft(merchant, product, _unit(unit), _quantity(quantity))
    if len(parts) == 3 and selected_merchant:
        product, unit, quantity = parts
        return InputDraft(selected_merchant, product, _unit(unit), _quantity(quantity))
    if len(parts) == 2 and selected_merchant and selected_unit:
        product, quantity = parts
        return InputDraft(selected_merchant, product, _unit(selected_unit), _quantity(quantity))

    # Natural-language fallback used by the free-text Telegram command.
    merchant = selected_merchant
    merchant_match = re.search(r"(?:shop|store|prodavnica|magazin)\s*[:=]\s*([^,;]+)", value, re.IGNORECASE)
    if merchant_match:
        merchant = merchant_match.group(1).strip()
        value = (value[:merchant_match.start()] + value[merchant_match.end():]).strip(" ,;")
    quantity_match = re.search(
        r"(?P<quantity>\d+(?:[,.]\d+)?)\s*(?P<unit>pcs?\.?|kom(?:ada)?\.?|kg|кг|g|г|l|л|ml|мл|pack|pak|set|сет)\s*$",
        value,
        re.IGNORECASE,
    )
    if not quantity_match:
        raise InputParseError("Could not find product quantity and unit, for example: milk 2 l")
    if not merchant:
        raise InputParseError("Select or specify a shop first")
    product = value[:quantity_match.start()].strip(" ,;:-")
    if not product:
        raise InputParseError("Product name is empty")
    return InputDraft(merchant, product, _unit(quantity_match.group("unit")), _quantity(quantity_match.group("quantity")))
