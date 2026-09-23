"""페이백 상품의 청구액/표시가 분리 회귀 테스트.

목록 카드만 보면 페이백 상품의 가격 칸에 **체감가**(페이백을 뺀 표시가)가 들어간다.
원문에서 확인한 세 갈래 사례를 고정해 둔다 - 표시가를 청구액으로 쓰거나, 표시가에
페이백을 도로 더하거나, 페이백 지급 기간을 요금 할인 기간으로 옮기지 않는지 본다.
"""
from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from crawl_moyo import apply_billing_prices, parse_billing_prices


def _detail_html(billing: str, payback_included: str) -> str:
    """상세 헤더의 두 금액 블록만 흉내 낸다(실제 페이지와 같은 span 구조)."""
    return (
        f"<div><div><span>월 납부액</span><br/><span>월 {billing}원</span></div>"
        f"<div><span>페이백 포함하면</span><br/><span>월 {payback_included}원</span></div></div>"
    )


class BillingPriceTests(unittest.TestCase):
    def test_reads_billing_and_display_price_separately(self):
        soup = BeautifulSoup(_detail_html("49,000", "7,000"), "html.parser")
        self.assertEqual(
            parse_billing_prices(soup),
            {"billing_monthly_fee": 49000, "payback_included_fee": 7000},
        )

    def test_missing_billing_block_is_not_guessed(self):
        soup = BeautifulSoup("<div><span>페이백 포함하면</span><br/><span>월 0원</span></div>", "html.parser")
        prices = parse_billing_prices(soup)
        self.assertIsNone(prices["billing_monthly_fee"])
        self.assertFalse(apply_billing_prices({"monthly_fee": 1700}, prices)["billing_price_verified"])

    def test_card_price_was_display_price_only(self):
        """모요 36334 너겟49: 카드 7,000원, 상세 청구 49,000원(34,000x6 + 8,000x12 페이백)."""
        card = {"monthly_fee": 7000, "discounted_fee": 7000, "discount_period_months": "",
                "discount_type": "모요 프로모션 페이백/할인"}
        fixed = apply_billing_prices(card, parse_billing_prices(
            BeautifulSoup(_detail_html("49,000", "7,000"), "html.parser")))
        self.assertEqual((fixed["discounted_fee"], fixed["monthly_fee"]), (49000, 49000))
        self.assertEqual(fixed["discount_period_months"], "")
        self.assertEqual(fixed["payback_included_fee"], 7000)
        self.assertEqual(fixed["discount_type"], "페이백")

    def test_payback_period_is_not_a_discount_period(self):
        """모요 6804: 카드 '0원 / 6개월', 청구액은 1,700원 그대로. 요금 할인은 없다."""
        card = {"monthly_fee": 1700, "discounted_fee": 0, "discount_period_months": 6,
                "discount_type": "모요 프로모션 페이백/할인"}
        fixed = apply_billing_prices(card, parse_billing_prices(
            BeautifulSoup(_detail_html("1,700", "0"), "html.parser")))
        self.assertEqual((fixed["discounted_fee"], fixed["monthly_fee"]), (1700, 1700))
        self.assertEqual(fixed["discount_period_months"], "")

    def test_real_discount_keeps_its_period(self):
        """모요 26613: 정가 42,300 / 청구 16,400 / 7개월 할인 + 매월 5,000 페이백."""
        card = {"monthly_fee": 42300, "discounted_fee": 11500, "discount_period_months": 7,
                "discount_type": "모요 프로모션 페이백/할인"}
        fixed = apply_billing_prices(card, parse_billing_prices(
            BeautifulSoup(_detail_html("16,400", "11,400"), "html.parser")))
        self.assertEqual((fixed["discounted_fee"], fixed["monthly_fee"]), (16400, 42300))
        self.assertEqual(fixed["discount_period_months"], 7)
        self.assertEqual(fixed["discount_type"], "모요 프로모션 할인 페이백")

    def test_payback_is_never_added_back_to_the_display_price(self):
        """표시가 + 페이백으로 청구액을 만들어내지 않는다. 쓰는 값은 원문의 청구액뿐."""
        fixed = apply_billing_prices(
            {"monthly_fee": 20000, "discounted_fee": 5000, "discount_period_months": 6},
            {"billing_monthly_fee": 12000, "payback_included_fee": 5000},
        )
        self.assertEqual(fixed["discounted_fee"], 12000)


if __name__ == "__main__":
    unittest.main()
