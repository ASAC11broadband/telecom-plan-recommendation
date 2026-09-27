from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from schema import (
    BENEFIT_CATEGORIES,
    BENEFIT_COLUMNS,
    classify_benefit_name,
    infer_benefit_search_categories,
    extract_data_gb,
    extract_membership_tier,
    make_benefit_row,
    normalize_service,
    parse_value_period,
    write_benefits,
)
from merge_plans import _read


class BenefitClassificationTests(unittest.TestCase):
    def test_mixed_choice_with_html_whitespace(self):
        self.assertEqual(
            classify_benefit_name("초이스 OTT 1개 또는\n\t디바이스 할인", "영상/OTT"),
            "복합/선택혜택",
        )

    def test_ai_partner_bundle_is_not_ott(self):
        self.assertEqual(
            classify_benefit_name(
                "구글 AI프로+도미노피자 할인쿠폰", "사은품/페이백"
            ),
            "교육/AI서비스",
        )

    def test_ai_mock_exam_is_education(self):
        self.assertEqual(
            classify_benefit_name("토스미·오픽미 AI 모의고사", "사은품/페이백"),
            "교육/AI서비스",
        )

    def test_video_music_and_book_subscriptions_are_separated(self):
        for name, category in (
            ("넷플릭스 광고형 스탠다드", "영상/OTT"),
            ("티빙 광고형 스탠다드 제공 (12개월)", "영상/OTT"),
            ("지니뮤직 마음껏 듣기", "음악/오디오"),
            ("지니 스마트 음악감상", "음악/오디오"),
            ("밀리의 서재 평생 구독 0원", "도서/콘텐츠"),
            ("교보문고 sam", "도서/콘텐츠"),
            ("모아진", "도서/콘텐츠"),
            ("조선일보 종이신문 1개월 무료 구독 이벤트", "도서/콘텐츠"),
            ("예스24 크레마클럽 구독권 (24개월)", "도서/콘텐츠"),
            ("더중앙플러스 구독권 매월 제공", "도서/콘텐츠"),
            ("네이버웹툰 쿠키 30개 (12개월)", "도서/콘텐츠"),
            ("카카오이모티콘 플러스 매월 무료 제공", "제휴서비스"),
            ("북앤라이프 5000P 도서문화상품권", "도서/콘텐츠"),
        ):
            with self.subTest(name=name):
                self.assertEqual(classify_benefit_name(name), category)

    def test_kt_choice_edge_cases(self):
        for name, category in (
            ("GoogleAI Plus (400GB)", "교육/AI서비스"),
            ("위버스", "제휴서비스"),
            ("삼성", "스마트기기"),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    classify_benefit_name(name, "OTT/구독"), category
                )

    def test_device_discount_is_not_partner_service(self):
        name = "디바이스 할인 (1개 or 2개 선택 가능) 1개 선택 시 최대 12천원 할인 2개 선택 시 각 최대 6천원 할인"
        self.assertEqual(classify_benefit_name(name, "제휴서비스"), "스마트기기")
        self.assertEqual(
            infer_benefit_search_categories(name, "스마트기기"), []
        )

    def test_device_line_fee_is_not_hardware_discount(self):
        for name in ("1대 월정액 할인 (최대 11,000원)", "2대 월정액 할인(최대 33,000원)"):
            self.assertEqual(
                classify_benefit_name(name, "스마트기기"),
                "스마트기기 회선/데이터쉐어링",
            )
            self.assertEqual(
                infer_benefit_search_categories(name, "스마트기기 회선/데이터쉐어링"), []
            )

    def test_writer_omits_repeated_device_discount_explanation(self):
        detail = "디바이스 할인 (1개 or 2개 선택 가능) / 1개 선택 시 최대 12천원 할인"
        rows = [
            {"plan_id": "test", "benefit_name": "디바이스 할인 (1개 or 2개 선택 가능)", "benefit_detail": detail},
            {"plan_id": "test", "benefit_name": "1개 선택 시 최대 12천원 할인", "benefit_detail": detail},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "benefits.csv"
            write_benefits(rows, path)
            with path.open(encoding="utf-8-sig", newline="") as file:
                saved = list(csv.DictReader(file))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["benefit_category"], "스마트기기")

    def test_legacy_category_never_survives_as_default(self):
        result = classify_benefit_name("프로모션 혜택", "OTT/구독")
        self.assertEqual(result, "제휴서비스")
        self.assertIn(result, BENEFIT_CATEGORIES)

    def test_cross_category_bundles_use_mixed_category(self):
        for name in (
            "티빙/지니/밀리",
            "지니뮤직, 밀리의서재, 구글원 등 미디어 혜택",
            "프로모션 혜택_초이스 OTT 1개 또는 디바이스 할인",
        ):
            with self.subTest(name=name):
                self.assertEqual(classify_benefit_name(name), "복합/선택혜택")

    def test_ai_ott_bundle_uses_ai_primary_and_both_search_categories(self):
        name = "Google AI + OTT 1종 할인"
        primary = classify_benefit_name(name)
        self.assertEqual(primary, "교육/AI서비스")
        self.assertEqual(
            infer_benefit_search_categories(name, primary),
            ["영상/OTT"],
        )

    def test_mixed_media_bundle_has_all_search_categories(self):
        name = "티빙/지니/밀리"
        primary = classify_benefit_name(name)
        self.assertEqual(primary, "복합/선택혜택")
        self.assertEqual(
            set(infer_benefit_search_categories(name, primary)),
            {"영상/OTT", "음악/오디오", "도서/콘텐츠"},
        )

    def test_mixed_partner_bundle_has_only_actual_search_categories(self):
        name = "지니뮤직, 밀리의서재, 구글원 등 미디어 혜택"
        primary = classify_benefit_name(name)
        self.assertEqual(primary, "복합/선택혜택")
        self.assertEqual(
            set(infer_benefit_search_categories(name, primary)),
            {"음악/오디오", "도서/콘텐츠", "제휴서비스"},
        )

    def test_roaming_and_protection_are_specific_even_when_coupon(self):
        for name, category in (
            ("데이터 로밍 3일 무료 쿠폰", "로밍"),
            ("로밍 50% 할인", "로밍"),
            ("단말보험 최대 4,500원 할인", "보험/안심"),
            ("추가혜택 KT 안심박스 무료제공", "보험/안심"),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    classify_benefit_name(name, "사은품/페이백"), category
                )

    def test_cash_gifts_coupons_and_sim_support_are_distinct(self):
        for name, category in (
            ("네이버페이 매달 5천원 페이백 (평생)", "페이백"),
            ("첫 달 요금 네이버페이로 전액 환급", "페이백"),
            ("요금 5천원 환급", "페이백"),
            ("네이버페이 5,000원", "포인트/적립"),
            ("네이버페이 매월 5,000P 제공", "포인트/적립"),
            ("네이버페이 포인트로 5천원 환급", "포인트/적립"),
            ("마트상품권, 네이버페이 최대 2만원", "상품권/사은품"),
            ("쇼핑라운지 할인쿠폰 5천원권", "쿠폰/할인"),
            ("KT유심&배송비 무료", "유심/배송비"),
            ("유심사 3일 500MB 할인 쿠폰 지급", "쿠폰/할인"),
            ("스마트기기 2회선 이용요금 무료", "스마트기기 회선/데이터쉐어링"),
        ):
            with self.subTest(name=name):
                self.assertEqual(classify_benefit_name(name, "사은품/페이백"), category)

    def test_single_category_does_not_repeat_in_search_tags(self):
        name = "네이버페이 매달 5천원 페이백 (평생)"
        tags = infer_benefit_search_categories(name, classify_benefit_name(name))
        self.assertEqual(tags, [])

    def test_data_coupon_is_not_cash_discount_coupon(self):
        name = "데이터쿠폰 20GB"
        self.assertEqual(classify_benefit_name(name), "추가데이터")
        self.assertEqual(
            infer_benefit_search_categories(name, "추가데이터"),
            [],
        )

    def test_mixed_gift_and_point_keeps_both_search_types(self):
        name = "마트상품권, 네이버페이 최대 2만원"
        self.assertEqual(
            infer_benefit_search_categories(name, classify_benefit_name(name)),
            ["포인트/적립"],
        )

    def test_membership_grade_without_membership_word(self):
        self.assertEqual(
            classify_benefit_name("24개월간 VIP 등급", "사은품/페이백"),
            "멤버십",
        )

    def test_common_writer_reclassifies_every_crawler_row(self):
        row = {
            "plan_id": "test",
            "benefit_category": "사은품/페이백",
            "benefit_name": "조선일보 종이신문 1개월 무료 구독 이벤트",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "benefits.csv"
            write_benefits([row], path)
            with path.open(encoding="utf-8-sig", newline="") as file:
                saved = next(csv.DictReader(file))
        self.assertEqual(saved["benefit_category"], "도서/콘텐츠")
        self.assertEqual(saved["benefit_search_categories"], "")
        self.assertEqual(saved["benefit_service"], "조선일보")

    def test_merge_accepts_legacy_interim_without_search_category_column(self):
        legacy_columns = [
            column for column in BENEFIT_COLUMNS
            if column != "benefit_search_categories"
        ]
        row = {column: "" for column in legacy_columns}
        row.update(
            plan_id="test",
            benefit_category="OTT/구독",
            benefit_name="티빙/지니/밀리",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy_benefits.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=legacy_columns)
                writer.writeheader()
                writer.writerow(row)
            saved = _read(path, BENEFIT_COLUMNS)[0]
        self.assertEqual(
            set(saved["benefit_search_categories"].split(" | ")),
            {"영상/OTT", "음악/오디오", "도서/콘텐츠"},
        )
        self.assertEqual(saved["benefit_category"], "복합/선택혜택")


class BenefitValuePeriodTests(unittest.TestCase):
    """benefit_value_won 은 총액이 아니라 1회분/한 달치다.

    총액으로 접으면 기간이 사라져서, 소비하는 쪽이 개월 수를 추측하다가
    12개월 페이백을 정확히 두 배로 계산한 적이 있다.
    """

    def test_recurring_payback_keeps_period(self):
        self.assertEqual(
            parse_value_period("네이버페이 매달 3.4만원 페이백 (6개월)"), ("monthly", 6)
        )
        self.assertEqual(
            parse_value_period("네이버페이 매달 2.5만원 페이백 (12개월)"), ("monthly", 12)
        )

    def test_indefinite_recurring_has_no_month_count(self):
        self.assertEqual(
            parse_value_period("네이버페이 매달 5천원 페이백 (평생)"), ("monthly", "")
        )

    def test_subscription_is_monthly_list_price(self):
        self.assertEqual(parse_value_period("넷플릭스"), ("monthly", ""))

    def test_one_off_gift_is_not_monthly(self):
        self.assertEqual(
            parse_value_period("3대 마트 상품권, 네이버페이 2만원"), ("one_off", "")
        )
        self.assertEqual(parse_value_period("KT유심&배송비 무료"), ("one_off", ""))

    def test_row_fills_basis_and_months_from_name(self):
        row = make_benefit_row(
            "p1", "KT", "요금제", "사은품/페이백",
            "네이버페이 매달 8천원 페이백 (12개월)", value_won=8000,
        )
        self.assertEqual(row["benefit_value_basis"], "monthly")
        self.assertEqual(row["benefit_months"], 12)

    def test_new_columns_are_in_schema(self):
        self.assertIn("benefit_value_basis", BENEFIT_COLUMNS)
        self.assertIn("benefit_months", BENEFIT_COLUMNS)


class BenefitQuantityTests(unittest.TestCase):
    """수량·등급이 이름 문자열에만 있으면 소비하는 쪽이 정규식을 다시 짜게 된다."""

    def test_extra_data_gb_is_extracted(self):
        self.assertEqual(extract_data_gb("공유데이터 100GB", "추가데이터"), 100.0)
        self.assertEqual(extract_data_gb("데이터쿠폰 20GB", "추가데이터"), 20.0)
        self.assertEqual(
            extract_data_gb("만 34세 이하 공유/테더링 데이터 20.0GB 추가 제공", "추가데이터"),
            20.0,
        )

    def test_gb_is_not_attached_to_non_data_benefits(self):
        # OTT 이름에 우연히 GB 가 들어가도 데이터 혜택으로 보면 안 된다.
        self.assertEqual(extract_data_gb("넷플릭스", "영상/OTT"), "")
        self.assertEqual(extract_data_gb("구글 원 100GB", "제휴서비스"), "")

    def test_membership_tier_prefers_longest_match(self):
        self.assertEqual(extract_membership_tier("멤버십 VVIP"), "VVIP")
        self.assertEqual(extract_membership_tier("VVIP 등급"), "VVIP")
        self.assertEqual(extract_membership_tier("T 멤버십 VIP 혜택"), "VIP")
        self.assertEqual(extract_membership_tier("넷플릭스"), "")

    def test_membership_row_fills_tier_not_subscription_tier(self):
        rows = [
            make_benefit_row("p1", "KT", "요금제", "멤버십", "멤버십 VVIP"),
            make_benefit_row("p1", "KT", "요금제", "추가데이터", "공유데이터 60GB"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.csv"
            write_benefits(rows, path)
            written = _read(path, BENEFIT_COLUMNS)
        self.assertEqual(written[0]["benefit_tier"], "VVIP")
        self.assertEqual(written[1]["benefit_data_gb"], "60.0")

    def test_newly_aliased_services(self):
        self.assertEqual(normalize_service("토스미·오픽미 AI 모의고사"), "토스미·오픽미")
        self.assertEqual(normalize_service("SNOW 앱 VIP 월 구독권"), "SNOW")


class MoyoAmountParsingTests(unittest.TestCase):
    """모요 사은품 금액 표기. "3만4천원"처럼 만·천이 이어 붙는 형태가 실제로 나온다.

    조각을 따로 읽으면 "4천원"만 잡혀 34,000원이 4,000원이 된다(재수집에서 발견).
    """

    def test_composed_korean_amount(self):
        from crawl_moyo import _parse_krw

        self.assertEqual(_parse_krw("3만4천원"), 34000)
        self.assertEqual(_parse_krw("1만6천원"), 16000)
        self.assertEqual(_parse_krw("2만1천원"), 21000)

    def test_plain_amounts_still_work(self):
        from crawl_moyo import _parse_krw

        self.assertEqual(_parse_krw("19.2만원"), 192000)
        self.assertEqual(_parse_krw("5천원"), 5000)
        self.assertEqual(_parse_krw("네이버페이 5,000원"), 5000)
        self.assertIsNone(_parse_krw("무료"))

    def test_largest_amount_wins_for_total_and_split(self):
        from crawl_moyo import _parse_krw

        self.assertEqual(_parse_krw("네이버페이 19.2만원(매월 3.2만원씩)"), 192000)

    def test_recurring_payback_returns_per_month_and_months(self):
        from crawl_moyo import _recurring_payback

        self.assertEqual(_recurring_payback("현금성포인트 매달 3만4천원 페이백(6개월)"), (34000, 6))
        self.assertEqual(_recurring_payback("네이버페이 매달 2만원 페이백 (6개월)"), (20000, 6))
        # 기간이 없으면 한 달치만 남고 개월 수는 빈값이다.
        self.assertEqual(_recurring_payback("네이버페이 매달 5천원 페이백 (평생)"), (5000, ""))
        self.assertEqual(_recurring_payback("3대 마트 상품권, 네이버페이 2만원"), (None, ""))


if __name__ == "__main__":
    unittest.main()
