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
