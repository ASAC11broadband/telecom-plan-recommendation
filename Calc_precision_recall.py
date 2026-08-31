"""
정답지(test_cases_정답지.xlsx)와 챗봇 결과(chatbot_results_gemini.xlsx)를 비교해서
문항별 Precision/Recall을 계산하고, 레벨별·전체 평균으로 집계하는 스크립트.

계산 방식:
    - 각 문항마다 정답 top5와 챗봇이 실제로 추천한 개수(보통 2~3개)를 비교
    - Precision = 교집합 개수 / 챗봇이 추천한 개수
    - Recall    = 교집합 개수 / 정답 개수(보통 5)
    - 100개 문항 각각 계산한 뒤, 전체/레벨별 평균을 냄(macro-average)

매칭 기준:
    - plan_name이 정확히 같고, 가격(원 단위 오차 1원 이내)이 같으면 매칭
    - 이름+가격이 같은데 여러 개(MNO/MVNO 동명이인 등)라서 carrier_type이
      양쪽 다 채워져 있는 경우에만 carrier_type까지 함께 비교

출력:
    - precision_recall_results.xlsx: 문항별 상세 결과 + 레벨별/전체 요약
    - 콘솔에도 요약 표 출력

사용법:
    python calc_precision_recall.py
"""

import sys

import openpyxl
from openpyxl import Workbook
from collections import defaultdict

# 인자로 덮어쓸 수 있다: python Calc_precision_recall.py [정답지] [챗봇결과] [출력]
ANSWER_KEY_PATH = "data/test_cases_정답지 - 복사본.xlsx"
RESULTS_PATH = "data/recommend_results.xlsx"
OUTPUT_PATH = "data/precision_recall_results.xlsx"
SHEET_NAME = "테스트케이스_v2"
MAX_RANK = 5
PRICE_TOLERANCE = 1  # 원 단위 오차 허용


def load_wide_rows(path: str):
    """id, level, question, ans1~5(name/monthly/discounted/carrier)를 딕셔너리 리스트로 읽어온다."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET_NAME]
    rows = {}
    for r in range(2, ws.max_row + 1):
        qid = ws.cell(row=r, column=1).value
        if qid is None:
            continue
        level = ws.cell(row=r, column=2).value
        question = ws.cell(row=r, column=3).value

        items = []
        for rank in range(MAX_RANK):
            base_col = 4 + rank * 4
            name = ws.cell(row=r, column=base_col).value
            monthly = ws.cell(row=r, column=base_col + 1).value
            discounted = ws.cell(row=r, column=base_col + 2).value
            carrier = ws.cell(row=r, column=base_col + 3).value
            if name is None or str(name).strip() == "":
                continue
            # 유효 가격: discounted_fee가 있으면 그 값, 없으면 monthly_fee
            if discounted not in (None, "", 0):
                price = discounted
            else:
                price = monthly
            items.append({
                "name": str(name).strip(),
                "price": price,
                "carrier": str(carrier).strip() if carrier else "",
            })

        rows[qid] = {"level": level, "question": question, "items": items}
    return rows


def items_match(a: dict, b: dict) -> bool:
    """이름+가격(오차 허용)이 같으면 매칭. 양쪽 다 carrier가 채워져 있으면 그것도 비교."""
    if a["name"] != b["name"]:
        return False
    ap, bp = a["price"], b["price"]
    if ap is None or bp is None:
        return False
    try:
        if abs(float(ap) - float(bp)) > PRICE_TOLERANCE:
            return False
    except (TypeError, ValueError):
        return False
    if a["carrier"] and b["carrier"] and a["carrier"] != b["carrier"]:
        return False
    return True


def count_intersection(answer_items: list, chatbot_items: list) -> int:
    """정답-챗봇 항목 사이 1:1 매칭 개수(그리디 매칭, 중복 카운트 방지)."""
    remaining_answers = list(answer_items)
    matched = 0
    for c_item in chatbot_items:
        for i, a_item in enumerate(remaining_answers):
            if items_match(c_item, a_item):
                matched += 1
                remaining_answers.pop(i)
                break
    return matched


def main():
    paths = (sys.argv[1:] + [ANSWER_KEY_PATH, RESULTS_PATH, OUTPUT_PATH][len(sys.argv) - 1:])
    answer_path, results_path, output_path = paths[:3]

    answer_rows = load_wide_rows(answer_path)
    result_rows = load_wide_rows(results_path)

    print(f"정답지 문항 수: {len(answer_rows)}")
    print(f"챗봇 결과 문항 수: {len(result_rows)}")
    print()

    # ---------- 문항별 계산 ----------
    per_question = []  # (id, level, question, precision, recall, matched, ans_count, chat_count, chat_empty)

    for qid, ans_data in answer_rows.items():
        chat_data = result_rows.get(qid)
        if chat_data is None:
            print(f"⚠️ {qid}: 챗봇 결과 파일에서 찾을 수 없습니다. 건너뜁니다.")
            continue

        answer_items = ans_data["items"]
        chatbot_items = chat_data["items"]

        matched = count_intersection(answer_items, chatbot_items)

        ans_count = len(answer_items)
        chat_count = len(chatbot_items)
        chat_empty = (chat_count == 0)

        precision = (matched / chat_count) if chat_count > 0 else 0.0
        recall = (matched / ans_count) if ans_count > 0 else 0.0

        per_question.append({
            "id": qid,
            "level": ans_data["level"],
            "question": ans_data["question"],
            "matched": matched,
            "ans_count": ans_count,
            "chat_count": chat_count,
            "precision": precision,
            "recall": recall,
            "chat_empty": chat_empty,
        })

    # ---------- 레벨별/전체 집계 (macro-average) ----------
    def avg(values):
        return sum(values) / len(values) if values else 0.0

    by_level = defaultdict(list)
    for row in per_question:
        by_level[row["level"]].append(row)

    level_summary = []
    for level in sorted(by_level.keys(), key=lambda x: str(x)):
        rows = by_level[level]
        level_summary.append({
            "level": level,
            "count": len(rows),
            "avg_precision": avg([r["precision"] for r in rows]),
            "avg_recall": avg([r["recall"] for r in rows]),
            "empty_count": sum(1 for r in rows if r["chat_empty"]),
        })

    overall_precision = avg([r["precision"] for r in per_question])
    overall_recall = avg([r["recall"] for r in per_question])
    overall_empty = sum(1 for r in per_question if r["chat_empty"])

    # ---------- 콘솔 출력 ----------
    print("=" * 70)
    print("레벨별 평균")
    print("=" * 70)
    print(f"{'레벨':<8}{'문항수':<8}{'Precision':<12}{'Recall':<12}{'빈 응답':<8}")
    for s in level_summary:
        print(f"{str(s['level']):<8}{s['count']:<8}{s['avg_precision']:<12.3f}{s['avg_recall']:<12.3f}{s['empty_count']:<8}")

    print()
    print("=" * 70)
    print("전체 평균")
    print("=" * 70)
    print(f"문항 수: {len(per_question)}")
    print(f"Precision (macro-avg): {overall_precision:.3f}")
    print(f"Recall (macro-avg): {overall_recall:.3f}")
    print(f"챗봇이 아무것도 추천 못한 문항: {overall_empty}개")

    # 정확도(Precision)가 낮은 하위 10개 문항 미리보기 (원인 분석용)
    print()
    print("=" * 70)
    print("Precision이 낮은 문항 TOP 10 (원인 분석용)")
    print("=" * 70)
    worst = sorted(per_question, key=lambda r: r["precision"])[:10]
    for r in worst:
        print(f"  {r['id']:<8} P={r['precision']:.2f} R={r['recall']:.2f}  "
              f"({r['matched']}/{r['chat_count']} 매칭)  {r['question']}")

    # ---------- 엑셀로 저장 ----------
    wb_out = Workbook()

    ws_detail = wb_out.active
    ws_detail.title = "문항별 결과"
    ws_detail.append(["id", "level", "question", "matched", "ans_count", "chat_count",
                       "precision", "recall", "chat_empty"])
    for r in per_question:
        ws_detail.append([r["id"], r["level"], r["question"], r["matched"], r["ans_count"],
                           r["chat_count"], round(r["precision"], 4), round(r["recall"], 4),
                           r["chat_empty"]])

    ws_summary = wb_out.create_sheet("레벨별 요약")
    ws_summary.append(["level", "count", "avg_precision", "avg_recall", "empty_count"])
    for s in level_summary:
        ws_summary.append([s["level"], s["count"], round(s["avg_precision"], 4),
                            round(s["avg_recall"], 4), s["empty_count"]])
    ws_summary.append([])
    ws_summary.append(["전체", len(per_question), round(overall_precision, 4),
                        round(overall_recall, 4), overall_empty])

    wb_out.save(output_path)
    print()
    print(f"완료: {output_path} 저장됨 (문항별 결과 시트 + 레벨별 요약 시트)")


if __name__ == "__main__":
    main()