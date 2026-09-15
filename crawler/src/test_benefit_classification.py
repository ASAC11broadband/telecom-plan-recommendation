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
    write_benefits,
)
from merge_plans import _read


class BenefitClassificationTests(unittest.TestCase):
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
            ["교육/AI서비스", "영상/OTT"],
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

    def test_roaming_and_protection_are_other_even_when_coupon(self):
        for name in (
            "데이터 로밍 3일 무료 쿠폰",
            "로밍 50% 할인",
            "단말보험 최대 4,500원 할인",
            "추가혜택 KT 안심박스 무료제공",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    classify_benefit_name(name, "사은품/페이백"), "기타"
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
        self.assertEqual(saved["benefit_search_categories"], "도서/콘텐츠")
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


if __name__ == "__main__":
    unittest.main()
