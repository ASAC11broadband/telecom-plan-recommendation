# telecom-plan-recommendation

요금제 추천 시스템. 조건을 자연어로 받아 CSV 요금제 데이터에서 후보를 추리고,
6개월 총비용 기준으로 순위와 리포트를 만든다.

```
agent/      LLM 파이프라인 (profiling → recommend → report → evaluation)
backend/    FastAPI. 화면이 부르는 엔드포인트 4개
frontend/   React + Vite 화면
data/       요금제 CSV 3종
```

## 실행

`.env` 에 `OPENAI_API_KEY` 가 있어야 한다.

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000   # 백엔드

cd frontend && npm install && npm run dev       # 프론트 (5173)
```

브라우저에서 http://localhost:5173 접속.
같은 와이파이의 다른 기기에서는 터미널에 찍히는 `Network:` 주소(예: http://192.168.0.37:5173)로 접속한다.
`/api` 는 Vite 가 8000 으로 프록시하므로 백엔드 주소를 따로 넣을 필요가 없다.
첫 실행 때 Windows 방화벽이 물어보면 "개인 네트워크" 로 허용한다.

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/recommend` | 대화 전체를 받아 추천 + 리포트. LLM 4단계라 40초 안팎. 데이터·요금 신호가 둘 다 없으면 추천 대신 질문(`needsMoreInput`)을 4초 만에 돌려준다 |
| GET | `/api/stats` | 홈 커버리지 숫자 + 필터 항목별 건수(`facets`) |
| GET | `/api/plans` | 탐색 화면. `q`·`networks`·`data`·`voice`·`flags`·`sort`·`page`·`page_size`. 그룹 안 OR, 그룹 간 AND |
| GET | `/api/plans/{id}` | 요금제 한 건 |
| POST | `/api/ask` | 특정 요금제에 대한 단발 질문 |

## 자체 점검

```bash
python -m agent.data              # 하드 필터
python -m agent.agents.report     # ranked ↔ candidates 결합 (plan_id 기준)
python -m agent.agents.evaluation # 환각·Hard Constraint·순위 검증
python -m backend.plans           # 6개월 비용 계산, PlanItem 변환
cd frontend && npm run lint       # 타입 체크
```

## 합성 가입이력 기반 세그먼트 추천 실험

원본 합성데이터에는 사용자 4만 명의 사용량·예산·필요 조건과, 그 사용자가 최적으로
가입했다고 가정한 요금제(`source_plan_id`)가 함께 들어 있다. 당시 요금제 스냅샷과 연결된
가입 이력으로 아래 두 방식을 100명 홀드아웃에서 비교한다.

```
초기안: 합성 가입이력 → 세그먼트별 소속 확률 → 확률 × 세그먼트별 인기 요금제 점수
전환안: 신규 사용자 입력 → 프로필 유사 이웃 100명 → 거리 가중 요금제 Top-5
평가: 실제 가입 요금제 Hit@5 / MRR@5 + Top-5 반복도
```

```bash
python -m agent.segmentation
python run_segment_test.py
```

`run_segment_test.py`는 LLM을 호출하지 않는다. `data/synthetic_original/customers_mvno.csv`의
가입 이력을 학습/평가로 분리하고, 결과를 같은 폴더의
`segment_recommendation_comparison.xlsx`와 `.jsonl`에 저장한다. 엑셀에는 소프트 세그먼트
초기안, 개인 이웃 전환안, 비교 요약 시트가 있다. 사용자당 가입 이력이 하나뿐이므로
전환안은 순수 협업필터링이 아니라 **프로필 유사도를 함께 쓰는 KNN 개인화 추천**이다.

## 코사인 유사도 기반 콘텐츠 추천 실험

사용자 입력과 요금제 속성을 데이터·통화·SMS·무제한 요구·가격 민감도·OTT 요구의 공통
벡터로 정규화해 코사인 유사도를 구한다. 최소 사용량과 무제한 요구는 먼저 하드 필터로
보장하고, 코사인 점수에 과잉 제공 방지·예산·OTT 적합도를 작게 보정한다.

```bash
python -m agent.cosine_recommendation
python run_cosine_test.py
```

같은 원본 합성 가입이력에서 100명을 분리해 정답 가입 요금제 Hit@5와 MRR@5를 기록한다.
상세 결과는 `data/synthetic_original/cosine_recommendation_evaluation.xlsx`에 저장한다.
