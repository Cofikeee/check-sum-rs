from decimal import Decimal

import pytest

from receipt_bot.verification import ReceiptVerificationClient, VerificationParser, VerificationError


HTML = """
<html><body>
<div>ПИБ: 100039229</div>
<div>Предузеће: ОГРАНАК 081 НОВИ ПАЗАР</div>
<div>За уплату: 5.618,00</div>
<div>Порез укупно: 936,33</div>
<div>Начин плаћања: Готовина</div>
<div>Време на безбедносном елементу: 26.04.2023. 13:15:12</div>
<div>Број рачуна: H453PTCR-T84XL200-618</div>
<table>
 <tr><th>Назив</th><th>Количина</th><th>Јед. цена са ПДВ</th><th>Укупна цена</th></tr>
 <tr><td>LOPTA UCL /KOM</td><td>1,00</td><td>2.799,00</td><td>2.799,00</td></tr>
</table>
</body></html>
"""


def test_official_html_parser_extracts_receipt_and_items() -> None:
    parsed = VerificationParser().from_html(HTML, "https://suf.purs.gov.rs/v/?vl=test", "text/html", 200)
    assert parsed.merchant_name_source == "ОГРАНАК 081 НОВИ ПАЗАР"
    assert parsed.gross_total == Decimal("5618.00")
    assert parsed.tax_total == Decimal("936.33")
    assert parsed.payment_method == "cash"
    assert parsed.fiscal_receipt_number == "H453PTCR-T84XL200-618"
    assert parsed.items[0].line_total == Decimal("2799.00")
    assert parsed.items[0].unit == "kom"


def test_json_parser_extracts_receipt() -> None:
    parsed = VerificationParser().from_mapping({
        "companyName": "MAXI",
        "sdcDateTime": "2026-10-13T10:00:00+02:00",
        "totalAmount": 100.0,
        "invoiceNumber": "A-1",
        "paymentType": "Card",
        "items": [{"name": "HLEB", "quantity": 1, "unitPrice": 100, "totalAmount": 100}],
    }, None, "application/json", 200)
    assert parsed.merchant_name_source == "MAXI"
    assert parsed.payment_method == "card"
    assert parsed.items[0].name_source == "HLEB"


def test_client_rejects_non_https_and_non_allowlisted_host() -> None:
    client = ReceiptVerificationClient(("suf.purs.gov.rs",))
    with pytest.raises(VerificationError):
        client.fetch("http://suf.purs.gov.rs/v/?vl=test")
    with pytest.raises(VerificationError):
        client.fetch("https://example.com/receipt")
