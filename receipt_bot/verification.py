from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import ipaddress
import json
from pathlib import Path
import re
import socket
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlparse

from .models import ParsedReceipt, ReceiptItemDraft


class VerificationError(RuntimeError):
    """The QR payload or the remote verification response is unusable."""


@dataclass(frozen=True)
class VerificationDocument:
    url: str | None
    content: str
    content_type: str
    status_code: int


class ReceiptVerificationClient:
    """Fetch public receipt verification pages with SSRF and size limits."""

    def __init__(
        self,
        allowed_hosts: Iterable[str],
        timeout_seconds: float = 20.0,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        self.allowed_hosts = tuple(host.strip().lower() for host in allowed_hosts if host.strip())
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def fetch(self, payload: str) -> tuple[ParsedReceipt, VerificationDocument]:
        url = self._payload_url(payload)
        if url is None:
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as error:
                raise VerificationError("QR payload is neither an HTTPS URL nor JSON") from error
            if not isinstance(data, dict):
                raise VerificationError("QR JSON payload must be an object")
            parsed = VerificationParser().from_mapping(data, None, "application/json", 200)
            self._require_receipt_fields(parsed)
            return parsed, VerificationDocument(None, payload, "application/json", 200)

        current = self._validate_url(url)
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - Docker supplies it
            raise VerificationError("httpx is not installed") from error

        headers = {
            "User-Agent": "check-sum-rs/1.0 receipt verifier",
            "Accept": "application/json, text/html;q=0.9, */*;q=0.1",
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds, headers=headers, follow_redirects=False) as client:
                response = None
                for _ in range(4):
                    response = client.get(current)
                    if response.status_code not in {301, 302, 303, 307, 308}:
                        break
                    location = response.headers.get("location")
                    if not location:
                        break
                    current = self._validate_url(urljoin(current, location))
                assert response is not None
        except Exception as error:
            raise VerificationError(f"Verification URL could not be fetched: {error}") from error

        if response.status_code >= 400:
            raise VerificationError(f"Verification service returned HTTP {response.status_code}")
        if len(response.content) > self.max_response_bytes:
            raise VerificationError("Verification response is too large")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        try:
            content = response.content.decode(response.encoding or "utf-8", errors="replace")
        except LookupError:
            content = response.content.decode("utf-8", errors="replace")
        parser = VerificationParser()
        try:
            if "json" in content_type or content.lstrip().startswith(("{", "[")):
                parsed = parser.from_mapping(json.loads(content), current, content_type, response.status_code)
            else:
                parsed = parser.from_html(content, current, content_type, response.status_code)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise VerificationError(f"Verification response could not be parsed: {error}") from error
        self._require_receipt_fields(parsed)
        return parsed, VerificationDocument(current, content, content_type, response.status_code)

    def _validate_url(self, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise VerificationError("QR verification links must be HTTPS URLs")
        host = parsed.hostname.lower().rstrip(".")
        if self.allowed_hosts and not any(self._host_matches(host, pattern) for pattern in self.allowed_hosts):
            raise VerificationError(f"QR host is not allowed: {host}")
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        except OSError as error:
            raise VerificationError(f"QR host cannot be resolved: {host}") from error
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise VerificationError(f"QR host resolves to a non-public address: {host}")
        return parsed.geturl()

    @staticmethod
    def _host_matches(host: str, pattern: str) -> bool:
        pattern = pattern.strip().lower().rstrip(".")
        if pattern.startswith("*."):
            suffix = pattern[1:]
            return host.endswith(suffix) and host != suffix[1:]
        return host == pattern

    @staticmethod
    def _payload_url(payload: str) -> str | None:
        value = payload.strip()
        if value.lower().startswith("https://"):
            return value
        return None

    @staticmethod
    def _require_receipt_fields(parsed: ParsedReceipt) -> None:
        missing = []
        if not parsed.merchant_name_source:
            missing.append("merchant")
        if parsed.receipt_datetime is None:
            missing.append("date/time")
        if parsed.gross_total is None:
            missing.append("total")
        if not parsed.items:
            missing.append("items")
        if missing:
            raise VerificationError("Verification response is missing: " + ", ".join(missing))


class VerificationParser:
    """Parse both the official TaxCore HTML page and JSON-shaped responses."""

    def from_mapping(
        self,
        data: Mapping[str, Any],
        url: str | None,
        content_type: str,
        status_code: int,
    ) -> ParsedReceipt:
        top = {self._key(key): value for key, value in data.items()}
        merchant = self._first(top, "companyname", "company_name", "businessname", "merchant", "предузеће")
        date_value = self._first(
            top, "sdc_datetime", "sdc_datetime", "datetime", "dateandtimeofissue", "time", "date", "време на безбедносном елементу"
        )
        gross = self._first(top, "totalamount", "total_amount", "amount", "gross_total", "total", "за уплату")
        tax = self._first(top, "taxamount", "tax_amount", "tax_total", "tax", "порез укупно")
        payment = self._first(top, "paymenttype", "payment_method", "payment", "начин плаћања")
        invoice_number = self._first(top, "invoicenumber", "invoice_number", "fiscal_receipt_number", "број рачуна")
        items_data = self._first(top, "items", "invoiceitems", "ставке")
        items = self._items_from_mapping(items_data if isinstance(items_data, list) else [])
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
        return ParsedReceipt(
            merchant_name_source=self._text(merchant),
            receipt_datetime=self._parse_datetime(date_value),
            currency="RSD",
            gross_total=self._decimal(gross),
            tax_total=self._decimal(tax),
            payment_method=self._payment(payment),
            fiscal_receipt_number=self._text(invoice_number),
            items=items,
            raw_text=raw[:1_000_000],
            ocr_language="qr_json",
            confidence=1.0,
            qr_url=url,
            verification_response=raw[:1_000_000],
            verification_content_type=content_type,
            verification_http_status=status_code,
        )

    def from_html(
        self,
        html: str,
        url: str | None,
        content_type: str,
        status_code: int,
    ) -> ParsedReceipt:
        try:
            from bs4 import BeautifulSoup
        except ImportError as error:  # pragma: no cover - Docker supplies it
            raise VerificationError("beautifulsoup4 is not installed") from error
        soup = BeautifulSoup(html, "html.parser")
        lines = [line.strip() for line in soup.get_text("\n", strip=True).splitlines() if line.strip()]
        values = self._label_values(lines)
        items = self._items_from_html(soup)
        raw = "\n".join(lines)
        return ParsedReceipt(
            merchant_name_source=self._first_text(values, "предузеће", "company", "business name", "merchant"),
            receipt_datetime=self._parse_datetime(self._first_text(values, "време на безбедносном елементу", "време", "date", "датум")),
            currency="RSD",
            gross_total=self._decimal(self._first_text(values, "за уплату", "укупан износ", "total amount", "total")),
            tax_total=self._decimal(self._first_text(values, "порез укупно", "укупан износ пореза", "tax total")),
            payment_method=self._payment(self._first_text(values, "начин плаћања", "payment method", "плаћање")),
            fiscal_receipt_number=self._first_text(values, "број рачуна", "invoice number"),
            items=items,
            raw_text=raw[:1_000_000],
            ocr_language="qr_html",
            confidence=1.0,
            qr_url=url,
            verification_response=html[:1_000_000],
            verification_content_type=content_type,
            verification_http_status=status_code,
        )

    @staticmethod
    def _key(value: object) -> str:
        return re.sub(r"[^a-z0-9а-яё]+", "", str(value).lower())

    @classmethod
    def _first(cls, mapping: Mapping[str, Any], *keys: str) -> Any:
        wanted = {cls._key(key) for key in keys}
        for key, value in mapping.items():
            if cls._key(key) in wanted and value not in (None, "", []):
                return value
        return None

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, dict):
            for key in ("name", "value", "text", "description"):
                if key in value:
                    return VerificationParser._text(value[key])
        text = " ".join(str(value).split())
        return text or None

    @classmethod
    def _label_values(cls, lines: list[str]) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in lines:
            if ":" not in line:
                continue
            label, value = line.split(":", 1)
            key = cls._key(label)
            value = value.strip()
            if value:
                values[key] = value
        return values

    @classmethod
    def _first_text(cls, values: Mapping[str, str], *keys: str) -> str | None:
        wanted = {cls._key(key) for key in keys}
        for key, value in values.items():
            if key in wanted:
                return value
        return None

    def _items_from_html(self, soup: Any) -> list[ReceiptItemDraft]:
        items: list[ReceiptItemDraft] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            headers = [self._key(cell.get_text(" ", strip=True)) for cell in rows[0].find_all(["th", "td"])]
            name_index = self._column(headers, "назив", "name", "item")
            quantity_index = self._column(headers, "количина", "quantity", "qty")
            price_index = self._column(headers, "јед цена са пдв", "јед цена", "unit price", "price")
            total_index = self._column(headers, "укупна цена", "total price", "total")
            if name_index is None or quantity_index is None or total_index is None:
                continue
            for row in rows[1:]:
                cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
                if len(cells) <= max(name_index, quantity_index, total_index):
                    continue
                name = cells[name_index].strip()
                if not name:
                    continue
                items.append(
                    ReceiptItemDraft(
                        position=len(items) + 1,
                        name_source=name,
                        quantity=self._decimal(cells[quantity_index]),
                        unit=self._unit(name),
                        unit_price=self._decimal(cells[price_index]) if price_index is not None and len(cells) > price_index else None,
                        line_total=self._decimal(cells[total_index]),
                        confidence=1.0,
                    )
                )
            if items:
                return items
        return items

    @classmethod
    def _column(cls, headers: list[str], *names: str) -> int | None:
        wanted = {cls._key(name) for name in names}
        for index, header in enumerate(headers):
            if header in wanted or any(name in header for name in wanted if len(name) > 3):
                return index
        return None

    def _items_from_mapping(self, values: list[Any]) -> list[ReceiptItemDraft]:
        items: list[ReceiptItemDraft] = []
        for index, value in enumerate(values, start=1):
            if not isinstance(value, Mapping):
                continue
            row = {self._key(key): val for key, val in value.items()}
            name = self._text(self._first(row, "name", "item", "description", "назив"))
            if not name:
                continue
            items.append(
                ReceiptItemDraft(
                    position=index,
                    name_source=name,
                    quantity=self._decimal(self._first(row, "quantity", "qty", "количина")),
                    unit=self._unit(name),
                    unit_price=self._decimal(self._first(row, "unitprice", "price", "јединична цена", "јед цена")),
                    line_total=self._decimal(self._first(row, "totalamount", "totalprice", "total", "укупна цена")),
                    confidence=1.0,
                )
            )
        return items

    @staticmethod
    def _unit(name: str) -> str | None:
        match = re.search(r"/(KOM|KG|L|M|PCS)\b", name.upper())
        return match.group(1).lower() if match else None

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        if value is None:
            return None
        text = str(value).strip().replace("RSD", "").replace("дин", "").replace(" ", "")
        if not text:
            return None
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        try:
            return Decimal(text)
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        text = str(value).strip().replace(" UTC", "+00:00")
        for candidate in (text, text.replace(".", "-")):
            try:
                return datetime.fromisoformat(candidate.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                pass
        for fmt in ("%d.%m.%Y. %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y. %H:%M", "%d.%m.%Y %H:%M"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
        return None

    @staticmethod
    def _payment(value: Any) -> str | None:
        if isinstance(value, list):
            values = []
            for item in value:
                if isinstance(item, Mapping):
                    candidate = VerificationParser._first(
                        {VerificationParser._key(key): item_value for key, item_value in item.items()},
                        "paymenttype", "type", "name",
                    )
                    if candidate is not None:
                        values.append(str(candidate))
            value = ", ".join(values)
        text = VerificationParser._text(value)
        if not text:
            return None
        lowered = text.lower()
        for marker, result in (
            ("готов", "cash"), ("cash", "cash"), ("карти", "card"), ("card", "card"),
            ("пренос", "bank_transfer"), ("wire", "bank_transfer"), ("чек", "check"),
            ("voucher", "voucher"), ("вауч", "voucher"), ("mobile", "mobile_money"),
        ):
            if marker in lowered:
                return result
        return lowered
