"""페이백 상품의 실제 청구액을 모요 상세페이지에서 확인해 정정표를 만든다.

목록 카드만 보고 만든 기존 CSV는 페이백 상품의 `discounted_fee`에 **체감가**(페이백을
뺀 표시가)가 들어가 있다. 실제 청구액은 상세페이지의 "월 납부액"에만 있다.
(plan 36334: 카드 7,000원 / 청구 49,000원, 36334의 페이백은 34,000x6 + 8,000x12)

전체 재크롤링은 하지 않는다. 서비스가 읽는 CSV에서 discount_type 에 '페이백'이 있는
행만 상세를 받아 청구액을 확인하고, 확인된 것만 정정표로 남긴다. 확인 실패는
행을 만들지 않는다 - 소비하는 쪽에서 계속 '계산 제외'로 남는다.

    python -X utf8 crawler/src/verify_payback_prices.py           # 캐시 없으면 받는다
    python -X utf8 crawler/src/verify_payback_prices.py --parse-only

출력: crawler/data/final/페이백_청구액_검증.csv
루트 data/ 로의 반영은 사람이 명시적으로 복사한다(루트 CSV는 추천 입력 고정용).
"""
import csv
import os
import re
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bs4 import BeautifulSoup

from crawl_moyo import (
    CACHE_DIR, DETAIL_URL, _get, _recurring_payback, apply_billing_prices,
    parse_billing_prices, GIFT_LABEL_SUFFIX,
)
from schema import SERVING_DIR, final_path

OUT_NAME = "페이백_청구액_검증.csv"
COLUMNS = [
    "plan_id", "plan_name",
    "billing_monthly_fee",     # 상세 "월 납부액" = 실제 청구액
    "payback_included_fee",    # 상세 "페이백 포함하면" = 체감 표시가
    "monthly_fee",             # 정정 후 정가
    "discounted_fee",          # 정정 후 청구액
    "discount_type",
    "discount_period_months",  # 요금 할인 기간(페이백 지급 기간과 다르다)
    "payback_schedule",        # "34000x6 | 8000x12", 기간 미상은 x?
    "verified_on", "source_url",
]


def _payback_schedule(soup) -> str:
    """상세의 페이백 링크에서 (월 지급액 x 개월수) 목록을 만든다.

    요금 할인 기간과 지급 기간이 다르므로 총액으로 접지 않는다. 개월수를 못 읽으면
    '평생'/미표기이므로 x? 로 남겨 기간 미상임을 보이게 둔다.
    """
    parts = []
    for a in soup.select('a[href^="/gift-group/"]'):
        label = (a.get("aria-label") or "").strip()
        if label.endswith(GIFT_LABEL_SUFFIX):
            label = label[: -len(GIFT_LABEL_SUFFIX)].strip()
        per_month, months = _recurring_payback(label)
        if per_month:
            parts.append(f"{int(per_month)}x{months or '?'}")
    return " | ".join(parts)


def _detail_soup(plan_id: str, parse_only: bool):
    path = os.path.join(CACHE_DIR, f"detail_{plan_id}.html")
    if not os.path.exists(path):
        if parse_only:
            return None
        os.makedirs(CACHE_DIR, exist_ok=True)
        try:
            html = _get(DETAIL_URL.format(plan_id=plan_id))
        except Exception as e:
            print(f"  ! {plan_id} 받기 실패: {type(e).__name__} {e}")
            return None
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        time.sleep(0.3)
    with open(path, encoding="utf-8") as f:
        return BeautifulSoup(f.read(), "html.parser")


def _payback_rows(csv_path):
    with open(csv_path, encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if "페이백" in (r.get("discount_type") or "")]


def _int_or_none(text):
    text = (text or "").strip()
    return int(float(text)) if re.fullmatch(r"-?\d+(\.\d+)?", text) else None


def verify(rows, parse_only=False) -> tuple[list[dict], list[dict]]:
    """returns (정정표 행들, 변경 비교 행들)"""
    verified, report = [], []
    for row in rows:
        plan_id = row["plan_id"]
        soup = _detail_soup(plan_id, parse_only)
        if soup is None:
            continue
        prices = parse_billing_prices(soup)
        if prices["billing_monthly_fee"] is None:
            print(f"  ? {plan_id} {row['plan_name']}: 상세에 '월 납부액'이 없다 - 제외 유지")
            continue
        card = {
            "monthly_fee": _int_or_none(row.get("monthly_fee")),
            "discounted_fee": _int_or_none(row.get("discounted_fee")),
            "discount_type": row.get("discount_type") or "",
            "discount_period_months": row.get("discount_period_months") or "",
        }
        fixed = apply_billing_prices(card, prices)
        verified.append({
            "plan_id": plan_id,
            "plan_name": row.get("plan_name", ""),
            "billing_monthly_fee": prices["billing_monthly_fee"],
            "payback_included_fee": prices["payback_included_fee"] if prices["payback_included_fee"] is not None else "",
            "monthly_fee": fixed["monthly_fee"],
            "discounted_fee": fixed["discounted_fee"],
            "discount_type": fixed["discount_type"],
            "discount_period_months": fixed["discount_period_months"],
            "payback_schedule": _payback_schedule(soup),
            "verified_on": date.today().isoformat(),
            "source_url": DETAIL_URL.format(plan_id=plan_id),
        })
        report.append({
            "plan_id": plan_id, "plan_name": row.get("plan_name", ""),
            "before": f"{card['discounted_fee']}/{card['monthly_fee']}/{card['discount_period_months'] or '-'}",
            "after": f"{fixed['discounted_fee']}/{fixed['monthly_fee']}/{fixed['discount_period_months'] or '-'}",
            "changed": (card["discounted_fee"], card["monthly_fee"]) != (fixed["discounted_fee"], fixed["monthly_fee"]),
        })
    return verified, report


def main():
    parse_only = "--parse-only" in sys.argv
    src = SERVING_DIR / "통신요금제_통합데이터_최종.csv"
    rows = _payback_rows(src)
    print(f"{src.name}: 페이백 표시 {len(rows)}건")
    verified, report = verify(rows, parse_only)

    out = final_path(OUT_NAME)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(verified)

    changed = [r for r in report if r["changed"]]
    print(f"\n청구액 확인 {len(verified)}건 / 미확인 {len(rows) - len(verified)}건 (미확인은 계산 제외 유지)")
    print(f"가격 정정 {len(changed)}건 · 형식: 청구액/정가/할인기간")
    for r in sorted(changed, key=lambda r: r["plan_id"])[:20]:
        print(f"  {r['plan_id']:>6} {r['plan_name'][:28]:<28} {r['before']:>22} -> {r['after']}")
    if len(changed) > 20:
        print(f"  ... 외 {len(changed) - 20}건")
    print(f"\n-> {out}")
    print("루트 data/ 반영은 값을 확인한 뒤 사람이 복사한다.")


if __name__ == "__main__":
    main()
