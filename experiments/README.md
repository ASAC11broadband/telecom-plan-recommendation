# experiments

서비스 코드(`agent/` `backend/` `frontend/` `crawler/`)가 아닌 실험·평가 스크립트. 서비스는 이 폴더를 import 하지 않는다.
`agent` 패키지를 읽으므로 **저장소 루트에서 모듈로** 실행한다.

| 스크립트 | 하는 일 | LLM |
|---|---|---|
| `run_metric_table` | 추천 방법 5종을 정답지 없는 지표로 비교 → `outputs/지표표_방법별비교.json` | 안 씀 |
| `run_presentation_evidence` | 위 표를 80건·같은 필터·95% 구간으로 재측정 + 발표 그림 → `outputs/발표/` (약 10분) | 안 씀 |
| `run_segment_test` / `run_cosine_test` | 합성 가입이력 100명 홀드아웃 (세그먼트·KNN / 코사인 v1·v2) | 안 씀 |
| `make_canva_figures` | 캔바 발표 덱에 넣을 그림 4장(비교표·무제한 전후·데이터 스키마·코사인 예산) → `outputs/발표/캔바용/` | 안 씀 |
| `make_eda_figures` | 발표용 EDA 그림 4장(빈 값의 의미·보이는 요금 vs 실제·가성비 한계선·카탈로그 변화)과 `eda_수치.json` | 안 씀 |
| `weight_scale_check` | 노트북의 Ridge 가중치를 고정 분석본에서 재현하고, 학습 눈금(z-score)과 서비스 눈금(0~1)의 영향력 비율·고정 범위를 점검 | 안 씀 |
| `make_synthetic_customers` | 합성 가입이력을 현행 카탈로그로 재생성 | 안 씀 |
| `run_testset` → `Calc_precision_recall` | 정답지 100문항을 파이프라인에 태워 P/R 계산 | 씀 |
| `run_weight_arms` (+`mcda_original`) | 가중치 방식별 P/R 비교. **현재 코드로는 실행 불가(보관용)** — 파일 머리말 참고 | 씀 |

```bash
python -m experiments.run_metric_table
```

평가용 입력(정답지·프로필 캐시)과 결과는 `data/eval/`에 있다. `results/`는 어떤 코드도 읽지 않는 과거 실행 결과 보관소다. `scenarios.txt`는 팀이 작성한 시나리오 질의 목록.
