# experiments

서비스 코드(`agent/` `backend/` `frontend/` `crawler/`)가 아닌 실험·평가 스크립트. 서비스는 이 폴더를 import 하지 않는다.
`agent` 패키지를 읽으므로 **저장소 루트에서 모듈로** 실행한다(`python -m experiments.<이름>`).

| 스크립트 | 하는 일 | LLM |
|---|---|---|
| `run_metric_table` | 추천 방법 5종을 정답지 없는 지표로 비교 → `outputs/지표표_방법별비교.json` | 안 씀 |
| `weight_scale_check` | 노트북의 Ridge 가중치를 고정 분석본에서 재현하고, 학습 눈금과 서비스 눈금·고정 범위를 점검 | 안 씀 |
| `run_segment_test` / `run_cosine_test` | 합성 가입이력 100명 홀드아웃 (세그먼트·KNN / 코사인 v1·v2) | 안 씀 |
| `make_synthetic_customers` | 합성 가입이력을 현행 카탈로그로 재생성 | 안 씀 |
| `run_testset` → `Calc_precision_recall` | 규칙 정답지 100문항을 파이프라인에 태워 P/R 계산. 입력·결과는 `data/eval/` | 씀 |

`scenarios.txt`는 팀이 작성한 시나리오 질의 목록.
