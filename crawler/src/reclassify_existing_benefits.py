"""현재 최종 CSV에 공통 혜택 분류 규칙을 다시 적용한다.

크롤링을 새로 하지 않고 분류 규칙만 바꿨을 때 사용한다. 혜택 상세를 먼저
재분류한 뒤 통합 요금제 CSV의 혜택 요약 컬럼도 같은 데이터로 다시 계산한다.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from schema import (
    BENEFIT_COLUMNS,
    classify_benefit_name,
    final_path,
    infer_benefit_search_categories,
    normalize_service,
    summarize_benefits,
)


PLANS_PATH = final_path("통신요금제_통합데이터_최종.csv")
BENEFITS_PATH = final_path("통신요금제_혜택상세_최종.csv")
SUMMARY_FIELDS = (
    "benefit_count",
    "ott_option_count",
    "ott_options",
    "membership_grade",
    "smart_device_benefit",
    "extra_data_benefit",
    "gift_benefit",
)


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), list(reader)


def _write_atomic(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> None:
    _benefit_columns, benefits = _read(BENEFITS_PATH)
    changes: Counter[tuple[str, str]] = Counter()
    for benefit in benefits:
        before = benefit.get("benefit_category", "") or "기타"
        after = classify_benefit_name(benefit.get("benefit_name", ""), before)
        if after != before:
            changes[(before, after)] += 1
            benefit["benefit_category"] = after
        benefit["benefit_search_categories"] = " | ".join(
            infer_benefit_search_categories(
                benefit.get("benefit_name", ""), after
            )
        )
        if not benefit.get("benefit_service"):
            benefit["benefit_service"] = normalize_service(
                benefit.get("benefit_name", "")
            )

    by_plan: dict[str, list[dict[str, str]]] = defaultdict(list)
    for benefit in benefits:
        by_plan[benefit.get("plan_id", "")].append(benefit)

    plan_columns, plans = _read(PLANS_PATH)
    for plan in plans:
        summary = summarize_benefits(by_plan.get(plan.get("plan_id", ""), []))
        for field in SUMMARY_FIELDS:
            plan[field] = str(summary.get(field, ""))

    _write_atomic(BENEFITS_PATH, BENEFIT_COLUMNS, benefits)
    _write_atomic(PLANS_PATH, plan_columns, plans)

    print(f"혜택 {len(benefits)}건, 요금제 {len(plans)}건 재분류 완료")
    for (before, after), count in sorted(changes.items()):
        print(f"  {before} -> {after}: {count}건")


if __name__ == "__main__":
    main()
