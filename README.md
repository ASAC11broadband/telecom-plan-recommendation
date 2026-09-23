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

## UI 개편 (2026-09-22)

홈에서 실측 커버리지와 사용 목적별 요금제 카드를 확인할 수 있습니다.
영상 시청형(20GB 이상 또는 무제한), 일상 사용형(3GB 이상 20GB 미만), 가벼운 사용형(3GB 미만), 통화 중심형(음성 무제한), OTT 혜택형(OTT 옵션 포함)으로 분류합니다. 카테고리는 중복될 수 있으며 적합성을 보장하는 AI 추천과는 별개입니다.

탐색에서는 카드/표 보기, 필터, 정렬, 페이지 이동, 비교 담기와 상세 보기를 지원합니다.

현재 로컬 실행은 백엔드 8000 포트와 빌드 미리보기 5173 포트를 사용합니다.
Windows에서 Vite 기본 설정 로더가 경로 접근 오류를 내면 `--configLoader runner`를 사용하세요.
개발 서버의 의존성 사전 번들링도 실패하는 환경에서는 `npm run build` 후 `npm run preview -- --configLoader runner --host 127.0.0.1 --port 5173`으로 확인할 수 있습니다. 이 모드에서는 소스 수정 후 다시 빌드해야 반영됩니다.

## 최신 로컬 UI 실행

기존 프로세스와 충돌하지 않도록 현재 프론트 미리보기는 http://127.0.0.1:5174, 백엔드는 8001 포트입니다. 프론트의 /api 프록시도 8001을 사용합니다.
백엔드: `.langgraph-venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8001`
프론트: `npm run build` 후 `npm run preview -- --configLoader runner --host 127.0.0.1 --port 5174`

데이터 필터는 [0,10), [10,30), [30,50), [50,100), [100,∞)GB 및 무제한으로 중복 없이 구분합니다. 영상 시청형은 30GB 이상 또는 무제한, 일상 사용형은 10GB 이상 30GB 미만, 가벼운 사용형은 10GB 미만입니다.
비교 바와 비교 페이지에서 개별 삭제/전체 비우기를 지원합니다. CSV 내보내기는 제거했습니다. 추천 결과의 로딩과 상담 채팅은 별도 영역이며, 진행 중에는 다음 조건을 작성할 수 있고 완료 후 전송할 수 있습니다.
## 가격 필터 및 UI 개선 (2026-09-23)

최신 로컬 주소는 http://127.0.0.1:5175 입니다. 백엔드는 8002 포트이며 backend 변경 시 자동 재시작합니다. 이전 포트의 서버는 종료 권한 제한으로 유지되어 있으므로 최신 주소를 사용하세요.

월 요금 필터: 1만원 미만, 1~2만원, 2~3만원, 3~5만원, 5~7만원, 7만원 이상. 할인 적용 월 요금(discounted_fee)을 기준으로 시작 금액 이상/끝 금액 미만이며, 같은 그룹 안에서는 OR, 데이터·통신망 등 다른 그룹과는 AND로 적용합니다. 카테고리 변경 시 선택한 가격 구간은 유지합니다.

메인 소개 배너를 줄이고, 카드의 데이터·가격을 강조했습니다. 비교함에 첫 상품 대비 비용과 기본 데이터 차이를 추가하고, AI 입력 예시와 추천 완료 표시를 개선했습니다.
