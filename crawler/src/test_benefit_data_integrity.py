from __future__ import annotations

import csv
import unittest
from collections import defaultdict

from schema import (
    BENEFIT_CATEGORIES,
    classify_benefit_name,
    final_path,
    serving_path,
    infer_benefit_search_categories,
    is_duplicate_device_discount_fragment,
    normalize_service,
    summarize_benefits,
)


def _read(name: str) -> list[dict[str, str]]:
    path = final_path(name)
    if not path.exists():
        path = serving_path(name)
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


class BenefitDataIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.benefits = _read("통신요금제_혜택상세_최종.csv")
        cls.plans = _read("통신요금제_통합데이터_최종.csv")

    def test_every_category_is_allowed_and_classifier_is_idempotent(self):
        allowed = set(BENEFIT_CATEGORIES)
        for row in self.benefits:
            with self.subTest(name=row["benefit_name"]):
                self.assertIn(row["benefit_category"], allowed)
                self.assertEqual(
                    classify_benefit_name(
                        row["benefit_name"], row["benefit_category"]
                    ),
                    row["benefit_category"],
                )

    def test_repeated_device_discount_fragments_are_not_served(self):
        self.assertFalse(any(is_duplicate_device_discount_fragment(row) for row in self.benefits))

    def test_cashback_and_data_voucher_do_not_have_unrelated_search_tags(self):
        for row in self.benefits:
            tags = [tag for tag in row["benefit_search_categories"].split(" | ") if tag]
            self.assertNotIn("사은품/페이백", tags, row["benefit_name"])
            self.assertNotEqual(row["benefit_category"], "사은품/페이백")
            self.assertNotIn(row["benefit_category"], tags, row["benefit_name"])
            if row["benefit_category"] == "페이백":
                self.assertNotIn("포인트/적립", tags, row["benefit_name"])
            if row["benefit_category"] == "추가데이터":
                self.assertNotIn("쿠폰/할인", tags, row["benefit_name"])
            if row["benefit_category"] == "스마트기기 회선/데이터쉐어링":
                self.assertNotIn("스마트기기", tags, row["benefit_name"])

    def test_same_benefit_name_never_has_conflicting_categories(self):
        categories_by_name: dict[str, set[str]] = defaultdict(set)
        for row in self.benefits:
            categories_by_name[row["benefit_name"]].add(row["benefit_category"])
        conflicts = {
            name: sorted(categories)
            for name, categories in categories_by_name.items()
            if len(categories) > 1
        }
        self.assertEqual(conflicts, {})

    def test_known_service_names_are_normalized(self):
        missing = []
        for row in self.benefits:
            expected = normalize_service(row["benefit_name"])
            if expected and not row["benefit_service"]:
                missing.append(row["benefit_name"])
        self.assertEqual(missing, [])

    def test_search_categories_match_name_and_primary_category(self):
        mismatches = []
        for row in self.benefits:
            expected = " | ".join(
                infer_benefit_search_categories(
                    row["benefit_name"], row["benefit_category"]
                )
            )
            if row.get("benefit_search_categories") != expected:
                mismatches.append(row["benefit_name"])
        self.assertEqual(mismatches, [])

    def test_plan_summary_matches_benefit_rows(self):
        benefits_by_plan: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in self.benefits:
            benefits_by_plan[row["plan_id"]].append(row)

        fields = (
            "benefit_count",
            "ott_option_count",
            "ott_options",
            "membership_grade",
            "smart_device_benefit",
            "extra_data_benefit",
            "gift_benefit",
        )
        mismatches = []
        for plan in self.plans:
            summary = summarize_benefits(benefits_by_plan.get(plan["plan_id"], []))
            for field in fields:
                if plan[field] != str(summary[field]):
                    mismatches.append((plan["plan_id"], field))
        self.assertEqual(mismatches, [])


if __name__ == "__main__":
    unittest.main()
