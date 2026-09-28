# telecom-plan-recommendation

요금제 추천 시스템. 조건을 자연어로 받아 CSV 요금제 데이터에서 후보를 추리고,
다기준(SMAA-2) 순위와 근거 리포트를 만든다. 총비용 비교는 12개월 기준이다.

```
agent/      LLM 파이프라인 (profiling → 조건 검증 → recommend → report → 설명 검증)
backend/    FastAPI
frontend/   React + Vite 화면
crawler/    통신 3사·모요 수집·일일 갱신
data/       서비스가 읽는 요금제 CSV(고정) · baseline/ · eval/(평가용) · synthetic_original/
experiments/ 실험·평가 스크립트 (서비스는 import 하지 않는다)
outputs/    발표 자료·분석 노트
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
python -m agent.agents.evaluation # 조건 검증(발화 인용 대조)·설명 검증(카드 문장)
python -m backend.plans           # 12개월 비용 계산, PlanItem 변환
cd frontend && npm run lint       # 타입 체크
```

## UI 개편 (2026-09-23)

- 홈: 커버리지 숫자와 사용 목적별 카테고리(영상 시청형·일상 사용형·가벼운 사용형·통화 중심형·OTT 혜택형), 카테고리별 대표 요금제 카드. 카테고리는 서로 겹칠 수 있고 AI 추천과는 별개다.
- 전체 요금제: 카드/표 보기, 필터(월 요금·데이터 구간 [0,10)·[10,30)·[30,50)·[50,100)·[100,∞)GB·무제한 등), 정렬, 비교 담기, 상세 화면(`#/detail?id=`).
- 비교함: 하단 비교 바, 다른 항목만 보기, 최저 비용·첫 상품 대비 차이 표시.
- AI 추천: 입력·결과·탐색 화면 옆에 같은 채팅 패널을 둔다. 탐색 중에 물으면 목록에 머문다.
- Windows 에서 Vite 설정 로더가 경로 오류를 내 `dev`·`build` 스크립트에 `--configLoader runner` 를 붙였다.

## 실험·평가

서비스 밖의 실험 스크립트는 `experiments/` 에 있다(→ `experiments/README.md`). 저장소 루트에서 모듈로 실행한다.

```bash
python -m experiments.run_metric_table      # 추천 방법 5종을 정답지 없는 지표로 비교
```

현재 적용 중인 정책값과 미해결 항목은 `HANDOFF.md` 맨 위 "현재 상태"에 있다.
