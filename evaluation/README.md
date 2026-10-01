# evaluation

시나리오 100문항으로 추천 방식을 비교한 평가. 발표의 방식 비교표 수치가 여기서 나왔다.

- 문항: `data/eval/test_cases_정답지.xlsx` (레벨 1~4, 20·30·30·20문항)
- 문항 조건: `testset_profiles.json`. git `904573c` 의 `data/eval/testset_profiles.json` 사본이다(LLM 이 문항에서 추출해 캐시한 것). 지금 `data/eval/` 에는 없다.
- 카탈로그: 8/21 고정본 `data/baseline/2026-08-21/`. 답지평가의 `_latest` 결과만 그때의 현행 카탈로그다.

스크립트는 **저장소 루트에서** `python evaluation/<폴더>/<스크립트>.py` 로 돌린다. 루트로 이동해 `agent`·`experiments` 를 import 만 하고 고치지 않는다. LLM 은 부르지 않는다.
환경 변수는 bash 기준(`CATALOG=baseline python ...`). PowerShell 은 `$env:CATALOG="baseline"; python ...`.

비교한 방식(6개 실행):

| 이름 | 방식 | 후보 범위 |
|---|---|---|
| ① 세그먼트 분류(협업) | 합성 가입이력 세그먼트 인기 | 알뜰폰 유형 전체(통신 3사 직판 포함) |
| ② 코사인 유사도(콘텐츠) | v1 코사인 | 알뜰폰 유형 전체(통신 3사 직판 포함) |
| ③ 단순 SMAA-2 | 균등 난수 가중치 | 서비스와 같음(알뜰폰, 질문이 통신 3사를 찾으면 포함) |
| ④ SMAA-2 + 회귀계수(채택) | 회귀계수(Ridge) 가중치 | 서비스와 같음 |
| ③' · ④' | ③·④ 와 같음 | 통신 3사 항상 포함 |

## 새답지 — LLM 블라인드 채점(0/1/2)

| 순서 | 실행 | 결과 |
|---|---|---|
| 1 | `CATALOG=baseline python evaluation/새답지/run_answer_key.py` | `answer_key_results_baseline.csv` (방식별 Top-5, `top5_ids` 는 `\|` 구분) |
| 2 | `build_pool.py` | `pool_mapping.json` · `채점자용_문항/batch_01~10.md` |
| 3 | LLM 채점 | `채점원본/grades_01~10.json` |
| 4 | `addendum_pool.py` | 후보 33개 추가 → `채점자용_문항/addendum.md` → `채점원본/grades_addendum.json` |
| 5 | LLM 재채점 | `채점자용_문항/recheck_A·B.md` → `채점원본/recheck_A·B.json` |
| 6 | `python evaluation/새답지/score_new_key.py` | 아래 표 |

- 후보 풀: 문항마다 6개 실행의 Top-5 합집합 + 조건을 만족하는 무작위 5개(통신 3사 포함). 4단계까지 합쳐 2,028개(문항당 10~27, 평균 20.3).
- 블라인드: 후보 순서를 섞고 코드(C01…)를 새로 붙여 어느 방식이 골랐는지 숨겼다. `pool_mapping.json`(코드 → plan_id)은 채점자에게 주지 않았다.
- 채점: 사람이 아니라 LLM(Claude)이 `rubric.md`(2026-09-24 고정) 기준으로 후보마다 0/1/2점과 근거 한 줄을 냈다. 0점은 필수 조건 위반, 1점은 조건은 맞지만 더 나은 대안·할인 종료 부담 등이 있음, 2점은 추천해도 좋음.
- 재채점: 20문항 412개 후보를 코드 역순으로 다시 채점했다. 완전 일치 95.6%, 1점 이내 99.3%, 이차 가중 카파 0.955.
- 사람 검수는 하지 않았다. `human_check_sheet.csv` 의 `사람_등급` 칸이 비어 있다.
- `build_pool.py`·`addendum_pool.py` 는 기록용이다. 다시 돌리면 `pool_mapping.json` 과 `채점자용_문항/*.md` 를 덮어써 채점원본과 어긋난다. `addendum_pool.py` 가 읽는 `pool_mapping_v1.json`(덧붙이기 전 풀)은 남아 있지 않고, 재채점 문항을 만든 스크립트도 없다.
- 1단계는 `answer_key_summary_baseline.json` 도 만드는데 여기에는 남기지 않았다.

`score_new_key.py` 는 채점원본·`pool_mapping.json`·1단계 결과를 읽어 계산만 한다. 다시 돌려도 아래 파일이 그대로 나온다.

| 파일 | 내용 |
|---|---|
| `new_key_summary.json` | 방식별 평균 · 레벨별 nDCG@5 · 쌍 비교(문항 부트스트랩 95% CI) · 등급 분포 · 재채점 일치율 |
| `new_key_scores.csv` | 문항 × 방식별 지표 |
| `new_answer_key.csv` | 사람이 읽는 답지(문항별 후보·등급·근거) |
| `human_check_sheet.csv` | 무작위 10문항 검수 시트(비어 있음) |

지표: nDCG@5(이득 2^g−1, 이상적 순위는 그 문항 풀 전체) · P@5(1점 이상) · P@5(2점) · Hit@1(1순위가 2점) · 위반률(0점 비율) · 더 나은 대안 없음.
마지막 '더 나은 대안 없음'은 채점이 아니라 `run_answer_key.py` 가 코드로 계산한 값이다(전체 카탈로그 7축 파레토 비지배 비율).

| 방식 | nDCG@5 | P@5(2점) | Hit@1 | 위반률 |
|---|---|---|---|---|
| ① 세그먼트 분류 | 27.6 | 19.0 | 27.0 | 68.8 |
| ② 코사인 유사도 | 29.1 | 18.0 | 19.0 | 61.0 |
| ③ 단순 SMAA-2 | 61.6 | 42.8 | 45.0 | 12.4 |
| ④ SMAA-2 + 회귀계수(채택) | 58.3 | 36.0 | 51.0 | 12.2 |
| ③' 통신 3사 포함 | 77.3 | 51.6 | 56.0 | 10.3 |
| ④' 통신 3사 포함 | 76.5 | 47.4 | 68.0 | 10.1 |

## 추가지표 — 답지 없이 재는 지표

`python evaluation/추가지표/extra_metrics.py` (4분 안팎) → `extra_metrics_by_question.csv` · `extra_metrics_summary.json`. 다시 돌려도 같은 파일이 나온다.
입력은 `새답지/` 의 `testset_profiles.json` 과 `answer_key_results_baseline.csv`.

- A. 100문항: 6개 실행 + 기준선 2개(조건 내 최저가 5 · 12개월 최저가 5). 조건 충족 · 파레토 비지배(전체·조건 내) · 가격 위치 · 할인 종료 충격 · 소진 후 속도 누락 · 근사중복 · 브랜드 다양성 · 커버리지 · 개인화 · 인기 편향.
- B. 24개 격자(필요 5·20·50·100GB × 예산 1·1.5·2·3·4·6만원, 알뜰폰만): 예산 단조성 · 예산 5% 흔들 때 Top-5 유지율 · 선호 반응.

발표 표의 '더 나은 대안 없음'은 여기의 `pareto_feas`(조건 내 파레토 비지배, 조건 밖 추천은 실패로 셈)다. LLM 채점이 아니라 코드로 계산했다.

## 답지평가 — 규칙 정답지 기준(이전 평가)

정답지 엑셀의 이름+가격 정답(437개)과 Top-5 를 맞춰 본다. 새답지 이전의 평가다.

| 실행 | 결과 |
|---|---|
| `python evaluation/답지평가/run_answer_key.py` | `answer_key_results_latest.csv` · `answer_key_summary_latest.json` |
| `CATALOG=baseline python evaluation/답지평가/run_answer_key.py` | `_baseline` |
| `CATALOG=baseline EXCLUDE_PAYBACK=1 python evaluation/답지평가/run_answer_key.py` | `_baseline_nopayback` (페이백 체감가로 뽑힌 정답 45개 제외) |
| `python evaluation/답지평가/fill_answer_key.py` | `filled_answer_ids.json` · `filled_answer_key.csv` (뺀 45개를 같은 규칙에 실제 청구액으로 다시 채움) |
| `CATALOG=baseline ANSWER_IDS=evaluation/답지평가/filled_answer_ids.json python evaluation/답지평가/run_answer_key.py` | `_baseline_filled` |

지표: P@5 정확(답지와 같은 요금제, 분모 5) · 맞힐 수 있는 정답 기준 P@5 · 같은 스펙 허용 P@5 · 조건 충족 · 더 나은 대안 없음.
`_baseline`·`_latest` 결과는 `p5_matchable` 열이 생기기 전 버전으로 만들었다. `새답지/run_answer_key.py` 는 이 스크립트에 세그먼트 Top-5 보정(8/21 에 없는 요금제 거르기)과 `top5_ids` 열을 더한 것이다.
