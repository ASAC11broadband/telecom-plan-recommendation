# experiments

서비스 코드(`agent/` `backend/` `frontend/` `crawler/`)가 아닌 실험·평가 스크립트. 서비스는 이 폴더를 import 하지 않는다.
`agent` 패키지를 읽으므로 **저장소 루트에서 모듈로** 실행한다(`python -m experiments.<이름>`).

| 스크립트 | 하는 일 | LLM |
|---|---|---|
| `run_metric_table` | 추천 방법 5종을 정답지 없는 지표로 비교 → `outputs/지표표_방법별비교.json` | 안 씀 |
| `weight_scale_check` | 노트북의 Ridge 가중치를 고정 분석본에서 재현하고, 학습 눈금과 서비스 눈금·고정 범위를 점검 | 안 씀 |
| `run_segment_test` / `run_cosine_test` | 합성 가입이력 100명 홀드아웃 (세그먼트·KNN / 코사인 v1·v2) → `data/synthetic_original/` 의 `segment_recommendation_comparison.*` / `cosine_recommendation_evaluation.xlsx`·`_cosine.jsonl`·`_coverage.jsonl` | 안 씀 |
| `make_synthetic_customers` | 합성 가입이력을 현행 카탈로그로 재생성 → `data/synthetic_original/customers_mvno.csv` | 안 씀 |
| `run_testset` → `Calc_precision_recall` | 규칙 정답지 100문항을 파이프라인에 태워 P/R 계산. 입력·결과는 `data/eval/` | 씀 |
| `plot_*` (6개) | `outputs/분석노트/` 의 분석 그림. 고정 분석본 `data/baseline/2026-08-21` 을 읽는다 | 안 씀 |

- `Calc_precision_recall [정답지] [챗봇결과] [출력]`: 인자를 생략하면 `data/eval/test_cases_정답지.xlsx`·`recommend_results.xlsx`·`precision_recall_results.xlsx`.
- `run_testset` 의 `MAX_RANK = 5` 지만 파이프라인은 화면용 `TOP_N`(3)개만 돌려준다.
- `plot_*` 그림: `plot_axis_correlation` → `EDA_축_상관.png`, `plot_pair_tables` → `EDA_쌍별_교차표.png`, `plot_regression_precheck` → `EDA_회귀전_점검.png`, `plot_linear_spec` → `왜_변환했나.png`(`--quick` 은 CV 생략), `plot_regression_steps` → `회귀_전처리_단계.png`, `plot_transform_detail` → `회귀_변환_근거_상세.png`. `plot_linear_spec` 외에는 `--pieces DIR` 로 조각 PNG 를 따로 저장한다.

시나리오 100문항을 LLM 이 블라인드 채점한 방식 비교(발표 비교표의 출처)는 여기가 아니라 `evaluation/` 에 있다(→ `evaluation/README.md`).

`scenarios.txt`는 팀이 작성한 시나리오 질의 목록.
