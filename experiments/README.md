# 실험 및 분석

`experiments/`는 서비스 실행 코드가 아닌 추천 방식 비교, 가중치 분석, 평가용 스크립트를 모아둔 폴더입니다.

서비스는 이 폴더를 import하지 않습니다. 모든 명령은 저장소 루트에서 `python -m experiments.<모듈명>` 형식으로 실행합니다.

## 스크립트 한눈에 보기

### 추천 방식 비교

| 스크립트 | 목적 | 주요 결과 |
|---|---|---|
| `run_segment_test` | 합성 가입 이력을 이용한 세그먼트·KNN 추천 비교 | `data/synthetic_original/segment_recommendation_comparison.*` |
| `run_cosine_test` | 콘텐츠 기반 코사인 유사도 추천 비교 | `data/synthetic_original/cosine_recommendation_evaluation.*` |
| `run_metric_table` | 추천 방식 5종을 답지 없이 지표로 비교 | `outputs/지표표_방법별비교.json` |

```powershell
python -m experiments.run_segment_test
python -m experiments.run_cosine_test
python -m experiments.run_metric_table
```

### 데이터와 가중치 분석

| 스크립트 | 목적 | 주요 결과 |
|---|---|---|
| `make_synthetic_customers` | 최신 카탈로그 기준 합성 가입 이력 재생성 | `data/synthetic_original/customers_mvno.csv` |
| `weight_scale_check` | Ridge 가중치와 서비스 평가 눈금의 일관성 확인 | 콘솔 점검 결과 |

```powershell
python -m experiments.make_synthetic_customers
python -m experiments.weight_scale_check
```

### 규칙 정답지 평가

```powershell
python -m experiments.run_testset
python -m experiments.Calc_precision_recall
```

`run_testset`은 100개 시나리오를 추천 파이프라인에 입력하고, `Calc_precision_recall`은 결과를 정답지와 비교해 P/R 지표를 계산합니다. 기본 입력과 결과는 `data/eval/`에 있습니다.

참고로 평가 화면은 최종적으로 Top 3를 보여주지만, 일부 비교 실험은 Top 5 기준으로 계산합니다(`run_testset`의 `MAX_RANK = 5`).

## 분석 그림 생성

`plot_*.py` 스크립트는 고정 분석본 `data/baseline/2026-08-21/`을 읽어 회귀와 평가 기준을 시각화합니다.

```powershell
python -m experiments.plot_axis_correlation
python -m experiments.plot_pair_tables
python -m experiments.plot_regression_precheck
python -m experiments.plot_linear_spec
python -m experiments.plot_regression_steps
python -m experiments.plot_transform_detail
```

생성되는 그림은 `outputs/분석노트/`에 저장됩니다.

| 스크립트 | 생성 그림 |
|---|---|
| `plot_axis_correlation` | `EDA_축_상관.png` |
| `plot_pair_tables` | `EDA_쌍별_교차표.png` |
| `plot_regression_precheck` | `EDA_회귀전_점검.png` |
| `plot_linear_spec` | `왜_변환했나.png` |
| `plot_regression_steps` | `회귀_전처리_단계.png` |
| `plot_transform_detail` | `회귀_변환_근거_상세.png` |

`plot_linear_spec`은 `--quick` 옵션을 사용하면 교차검증을 생략할 수 있습니다. 조각 PNG가 필요한 분석은 `--pieces <폴더>` 옵션을 사용할 수 있습니다.

## 실행 시 주의사항

- 서비스 실행이 목적이면 이 폴더의 스크립트를 실행할 필요가 없습니다.
- `run_testset`과 정답지 기반 평가는 OpenAI API를 사용할 수 있으므로 `.env`의 API 키가 필요합니다.
- 실험 결과 파일은 기존 결과를 덮어쓸 수 있으므로, 발표에 사용한 결과를 보존해야 한다면 실행 전에 복사해 두세요.
- 추천 방식 비교의 최종 블라인드 채점 결과는 `evaluation/README.md`에 정리되어 있습니다.
