# HANDOFF — 추천 설명 검증 실패 / 비용 기준 통일 / 혜택 왜곡 / 필수·선호 분리

작업일: 2026-09-18
브랜치: `fix/plan`
기준 커밋: `8f5a18c` + 작업 시작 시점의 미커밋 변경 전부

> 첫 커밋(`chore:`)은 작업 시작 시점의 미커밋 변경(수정 31개 파일 + 미추적 파일)을
> 그대로 담은 것이고, 두 번째 커밋(`fix:`)이 이번 작업분입니다.
> 두 번째 커밋만 보면 이번에 바뀐 것을 그대로 읽을 수 있습니다.

---

## 1. 완료한 수정

### 우선순위 1 — 실제 추천의 설명 검증 실패 (해결)

**재현 입력**: `월 데이터 20GB 이상, 요금 3만원 이하로 추천해줘. 만 30세이고 혜택은 상관없어.`

**수정 전 (실측)**
- 후보 4건, 추천 5건 중 4건만 반환, `attempt=3`(재시도 2회 소진), `evaluation.passed=False`
- 검증 실패 사유: `"테더링이 40GB 가능하다는 점이 장점입니다" — 후보 데이터에는 테더링 제공량 정보가 없습니다`

**원인은 두 가지였고 서로 다른 문제였습니다.**

| 구분 | 원인 | 위치 |
|---|---|---|
| 평가 모듈 오판 | Report Agent는 후보 원본 전체를 받는데 Evaluation Agent는 `slim()`이 잘라낸 필드만 받았다. `tethering_gb`가 `SLIM_FIELDS`에 없어서, 데이터에 실재하는 테더링 40GB를 적은 멀쩡한 리포트를 "없는 기능을 단정했다"고 판정했다. | `agent/data.py` |
| 추천 후보 오류 | `3만원 이하`를 LLM이 `budget_min_won=30000` + `budget_max_won=39999`로 뽑았고, 코드 보정이 상한만 30,000으로 고쳐서 **하한=상한**이 되었다. 정확히 30,000원인 상품 4건만 남았다(578 → 4). 사용자는 하한을 말한 적이 없다. | `agent/agents/profiling.py` |

**수정 내용**
- `agent/data.py` `SLIM_FIELDS`: 사람이 읽는 대응 필드가 없는 사실을 평가에도 넘긴다
  (`carrier_type`, `network_gen`, `daily_data_gb`, `tethering_gb`, `sms_unlimited`,
  `is_online_only`, `plan_category`). 두 에이전트가 보는 사실의 범위를 같게 맞췄다.
- `agent/agents/evaluation.py` `PROMPT`: `[판단 기준]` 절 추가.
  값이 들어 있는 항목을 옮겨 적은 것은 환각이 아니고, `null`은 '없음'이 아니라 '미수집'이며,
  `null`인 항목을 **구체적 수치로 단정했을 때만** 불합격이다.
  → `passed=true`를 강제하거나 경고를 지우지 않았다. 판정 기준의 사실 범위를 맞춘 것이다.
- `agent/agents/profiling.py` `_repair_budget_bounds`: 상한 표현(`이하/까지/미만`)이 있고
  발화에 하한 표현(`이상/부터/N만원대/최소 N원`)이 없으면 `budget_min_won`을 지운다.
  프롬프트에도 같은 규칙을 넣었다(코드 보정이 최종 방어선).
- `agent/agents/report.py` `REPORT_PROMPT`:
  - 카드에 이미 있는 스펙 나열 금지, 각 상품은 **차별점 + 주의사항** 1~2문장.
  - 필수 조건을 통과한 상품에 "데이터 부족"이라고 쓰지 말 것.
    부족하다고 쓸 수 있는 경우는 `estimated_monthly_data_gb`보다 제공량이 적을 때뿐이고
    그때는 기준이 된 예상 사용량을 함께 밝히도록 했다.
  - `완벽히 충족` / `가장 우수` 같은 단정은 후보 데이터로 그 자리에서 확인될 때만.
  - 사용자가 혜택을 요청하지 않았으면 혜택이 적다는 것을 단점으로 쓰지 말 것.
- `agent/agents/report.py` `_ensure_promo_notices`: 할인 중이 아닌 상품에도 `요금 조건:` 줄을 붙인다.
  설명을 짧게 쓰게 하면서 금액을 본문에서 빼면 코드 검증(`할인가 누락`)에 걸려
  리포트 단계가 통째로 재시도되기 때문이다. 금액은 항상 후보 원본 값으로 들어간다.

**수정 후 (실측)**: 후보 **578건**, 추천 5건, `attempt=1`(재시도 0회), `evaluation.passed=True`

### 우선순위 2 — 비용 비교 기준 12개월로 통일

기준 상수를 **하나**로 만들었습니다: `agent/mcda.py`의 `COMPARE_MONTHS = 12`.

| 대상 | 전 | 후 |
|---|---|---|
| 추천 가격 효용 (`_effective_monthly_fee`) | 12개월 | 12개월 (변화 없음) |
| 화면 총비용 (`backend/plans.py`) | 6개월 | 12개월 |
| 혜택 월 환산 (`BENEFIT_AMORTIZE_MONTHS`) | 6 (하드코딩) | `COMPARE_MONTHS` |
| 전체 요금제 정렬 `total_asc` / `effective_asc` | 6개월 | 12개월 |
| 내 비교함·카드·상세 리포트 | `compareMonths`를 서버에서 받아 표시 | 동일(값만 12로) |

- `backend/plans.py`의 `six_month_cost` → `total_cost`로 이름을 바꿨습니다(6개월 전제가 이름에 박혀 있었음).
  호출부: `SORTS`, `to_plan_item`, `test_service_process.py`.
- 프런트 하드코딩 `6개월` 문자열 제거(`BrowseScreen` 정렬 라벨·표 헤더, `HomeScreen` 폴백,
  `RecommendationTrace` 폴백). 이제 전부 서버가 내려준 `compareMonths` / `rankingMonths`를 씁니다.
- `RecommendationTrace`의 "비용을 두 가지로 보는 이유" 절은 기준이 하나가 됐으므로
  "비용 비교 기준"으로 바꾸고, 두 값이 다를 때만 차이를 안내하도록 남겨 뒀습니다.
- **제공 기간 미확인 할인**은 기존 `costIsEstimate`(할인 중인데 `discount_period_months`가 없음)로
  계속 추정 표시합니다. 12개월로 늘어나면서 과대평가 폭이 커지므로 이 표시가 더 중요해졌습니다.

> ⚠️ **팀 확인 필요**: 이전에 "비교 구간 6개월 고정 — 알뜰폰 갈아타기 주기 때문에 12개월 반대"라는
> 결정이 기록돼 있었습니다. 이번 지시("별도 팀 합의가 없다면 12개월을 기본값으로")에 따라 12로
> 통일했습니다. 되돌리려면 `agent/mcda.py`의 `COMPARE_MONTHS` 한 줄만 6으로 바꾸면 전부 따라갑니다
> (`backend/plans.py` 테스트의 `assert COMPARE_MONTHS == 12`도 함께 수정).

### 우선순위 3 — 혜택이 순위·비용을 왜곡하던 경로 차단

1. **순위**: 사용자가 혜택을 요청하지 않으면 혜택 축을 **중립(0.5 상수)** 으로 둡니다
   (`agent/mcda.py`). 기존에는 `benefit_value_won`과 혜택 개수를 효용으로 썼기 때문에
   "혜택은 상관없어"라고 말한 사용자에게도 페이백이 크거나 혜택이 많은 상품이 올라왔습니다.
   상수 축은 기존 `_discriminating()`이 걸러 내므로 가중치도 가져가지 않습니다.
   `_benefit_value_utility()` 함수는 삭제했습니다.
2. **비용**: 화면 총비용에서 빼는 것은 **조건 없는 현금성 혜택**뿐입니다.
   `agent/data.py`에 `benefit_summary()`를 추가해 혜택 금액을 두 갈래로 나눴습니다.
   - `benefit_value_won` — 월 환산 **참고값**(표시용, 종전과 같은 자리)
   - `benefit_deductible_won` — 납부액에서 빼도 되는 금액. 조건: ① 현금성(사은품/페이백)이고
     ② `benefit_condition`/이름에 카드 실적·제휴·결합·약정·응모 같은 추가 조건이 없고
     ③ 제공 기간이 확인된 것
   - `benefit_value_estimated` — 제공 기간 미확인 혜택이 섞였는지(추정 표시용)
   - `benefit_conditional_count` — 조건이 붙어 차감하지 않은 혜택 수
3. **기간**: `_monthly_worth()`가 이제 "비교 구간 동안의 한 달 평균"을 돌려줍니다.
   6개월만 주는 페이백을 12개월 내내 받는 것으로 계산하지 않습니다.
   `_benefit_duration()`이 (개월 수, 확인 여부)를 돌려주며 **기간 미상 `(None, False)`** 과
   **무기한 `(12, True)`** 을 구분합니다.
4. **월 지급액 / 일시금**: 종전대로 크롤러 컬럼(`benefit_value_basis`, `benefit_months`)을
   먼저 믿고, 없으면 이름 문자열을 읽습니다. 일시금은 기간 1개월로 보고 구간에 폅니다.
5. **택1**: 종전대로 `select_group`당 하나만 셉니다(전부 합산하지 않음).
6. **중복 반영 없음**: 가격 효용(`_effective_monthly_fee`)은 혜택을 빼지 않습니다.
   혜택은 화면의 `effectiveTotal`에서만 한 번 반영되고, 그마저도 현금성·무조건분에 한합니다.
7. 프런트: `현금성 혜택 차감 참고값`으로 라벨을 바꾸고, 혜택 월 환산에 `(추정)` 표기와
   "조건이 붙은 혜택 N건은 차감하지 않았습니다" 안내를 추가했습니다.
   OTT 이용자도 알뜰폰 후보를 그대로 볼 수 있습니다(혜택은 필터가 아님 — 아래 4번).

### 우선순위 4 — 필수 조건과 선호 분리

- `agent/data.py` `filter_candidates`: 혜택 조건은 `hard_constraints`에 들어 있을 때만 후보를 지웁니다.
  `hard_constraints`가 아예 없는 호출(테스트·스크립트가 dict를 직접 넘기는 경우)은 종전처럼 필수로 봅니다.
- `agent/agents/profiling.py` `_apply_benefit_constraint_strength`: 말투로 세기를 가릅니다.
  희망(`~면 좋겠어`, `가능하면`, `되도록`, `선호`) → `hard_constraints`에서 뺌 /
  필수(`~만 원해`, `반드시`, `꼭`, `무조건`, `필수`, `있어야`) → `hard_constraints`에 넣음 /
  둘 다 아니면 LLM 판단 유지.
- `agent/agents/profiling.py` `_apply_soft_data_preference`: 수치 없는 "데이터가 넉넉했으면"을
  버리지 않고 `priorities`에 `data`를 넣어 **가중치로만** 반영합니다. 필터는 만들지 않습니다.
  `_drop_inferred_priorities` **뒤에** 실행되어야 하며(앞에 두면 방금 넣은 값이 지워짐)
  그 순서를 self-check가 검사합니다.
- 프롬프트에 "필수 조건이라는 이유로 같은 축을 `priorities`에도 넣지 마라"를 명시했습니다.
  가중치 표본에 대한 기존 우선순위 보정(`_boosted`, `_MAX_BOOSTED_SHARE`)은 그대로 씁니다.

---

## 2. 변경 파일

```
agent/data.py                         SLIM_FIELDS 확장, benefit_summary/_benefit_duration 추가,
                                      _monthly_worth 기간 반영, filter_candidates 혜택 필수/선호,
                                      benefit_details 에 condition 추가, BENEFIT_AMORTIZE_MONTHS 통일
agent/mcda.py                         COMPARE_MONTHS 도입, 혜택 축 중립화, _benefit_value_utility 삭제
agent/agents/evaluation.py            PROMPT 에 [판단 기준] 절 추가
agent/agents/report.py                REPORT_PROMPT 규칙 2·4 재작성 + 사실성 원칙 3개 추가,
                                      _ensure_promo_notices 를 할인 없는 상품까지 확장
agent/agents/profiling.py             PROFILING_PROMPT 예산 하한·필수/선호 규칙,
                                      _repair_budget_bounds 하한 제거, _BUDGET_MIN_RE,
                                      _apply_benefit_constraint_strength, _apply_soft_data_preference
backend/plans.py                      six_month_cost -> total_cost, COMPARE_MONTHS 를 mcda 에서 import,
                                      effectiveTotal 을 benefit_deductible_won 기준으로,
                                      benefitDeductible/benefitValueEstimated/benefitConditionalCount 추가
test_service_process.py               import 변경 + 테스트 4개 추가
frontend/src/types.ts                 PlanItem 에 필드 3개 추가
frontend/src/components/BrowseScreen.tsx        '6개월' 하드코딩 제거, 차감 표시 기준 변경
frontend/src/components/HomeScreen.tsx          폴백 6 -> 12
frontend/src/components/RecommendationTrace.tsx 폴백 6 -> 12, 4번 절 문구 재작성
frontend/src/components/ReportScreen.tsx        혜택 블록 재작성(추정·조건 안내)
frontend/src/components/ResultScreen.tsx        차감 표시 기준·라벨 변경
HANDOFF.md                            (신규)
```

**데이터 파일은 변경하지 않았습니다.** 루트 `data/`의 CSV 2개는 그대로입니다.

---

## 3. 실행한 테스트와 결과

모두 저장소 루트에서 `PYTHONPATH=.` 로 실행했습니다.

| 명령 | 결과 |
|---|---|
| `python -B -m unittest test_service_process` | **OK — 16개** (기존 12 + 신규 4) |
| `python -m unittest discover -s crawler/src -p "test_*.py"` | **OK — 34개** |
| `python backend/plans.py` | `self-check ok: 2759 plans` |
| `python -m agent.data` | `self-check ok: 1555 candidates` |
| `python -m agent.mcda` | `self-check ok: SMAA-2 ranking (bootstrap weights)` |
| `python -m agent.agents.profiling` / `report` / `evaluation` | 각 `self-check ok` |
| `python -m agent.usage` | `self-check ok` |
| `cd frontend && npm run build` | `tsc --noEmit` 통과, `✓ built in 2.44s` |

**신규 테스트 4개** (`test_service_process.py`)
- `test_compare_period_is_single_source_of_truth` — `compareMonths == rankingMonths == BENEFIT_AMORTIZE_MONTHS`
- `test_benefit_is_not_deducted_without_confirmed_usage` — 구독형 혜택은 총비용에서 빠지지 않는다
- `test_benefit_axis_is_neutral_when_not_requested` — 혜택 미요청 시 혜택 축이 전 후보 0.5
- `test_preferred_benefit_does_not_remove_candidates` — 선호 혜택은 후보를 지우지 않는다(알뜰폰 포함)

> 기존 테스트 중 스펙이 바뀐 것은 **기준을 느슨하게 만든 게 아니라 새 스펙으로 다시 쓴 것**입니다.
> - `plans.py`: 6개월 총비용 기대값 → 12개월 기대값(계산식 그대로 노출)
> - `data.py`: 일시금 60,000원 → 월 10,000원(÷6) → 월 5,000원(÷12), 기간/조건 판정 테스트 추가
> - `mcda.py`: "혜택 축이 금액으로 갈린다" → "요청 없으면 중립, 요청하면 일치도로 갈린다" +
>   "혜택이 가격을 이기면 안 된다" 회귀 테스트 추가

---

## 4. 실제 API 테스트 입력과 결과

`agent.graph.graph.invoke()` 를 직접 호출했습니다(LLM 실호출, `MODEL=gpt-4o-mini`, `EVAL_MODEL=gpt-4o`).

### 시나리오 1 — 3만원 이하 + 데이터 여유 선호
입력: `월 3만원 이하로 추천해줘. 가능하면 데이터가 넉넉했으면 좋겠어. 만 30세야.`

- 기존 문제: 수치 없는 "넉넉했으면"이 조건 필드에 못 들어가 통째로 버려졌고,
  예산만 맞는 10GB 요금제가 상위 5개를 채웠다(`priorities=None`).
- 수정 기준: 희망은 필터가 아니라 가중치. `priorities`에 `data`만 추가하고 후보는 그대로 둔다.
- 실제 결과: `priorities=['data']`, `hard_constraints=['budget_max_won']`, 후보 **1,914건(변화 없음)**,
  추천 1~5위가 120GB / 150GB×4로 바뀜. 전부 30,000원 이하(7,000~22,500원). 예산 위반 없음.
  `evaluation.passed=True`, 재시도 0회.

### 시나리오 2 — 넷플릭스 이용 중
입력: `넷플릭스 이용 중이야. 월 3만원 이하 20GB 이상으로 추천해줘. 만 30세.`

- 기존 문제: 이용 정보가 혜택 요구로 읽히면 넷플릭스 미포함 알뜰폰이 전부 탈락한다.
- 수정 기준: 시청 언급은 혜택 요구가 아니다(프롬프트) + 혜택은 `hard_constraints`일 때만 필터(코드).
- 실제 결과: `wanted_benefits=None`, 후보 **578건 전부 MVNO**. 알뜰폰이 배제되지 않음. `passed=True`.

### 시나리오 3 — 넷플릭스 포함 필수
입력: `넷플릭스 포함 요금제만 원해. 월 5만원 이하. 만 30세.`

- 기존 문제: 필수 혜택이 선호로 처리되면 넷플릭스 없는 상품이 추천된다.
- 수정 기준: `~만 원해`는 필수 → `hard_constraints`에 `wanted_benefits` 포함.
- 실제 결과: `hard_constraints=['budget_max_won', 'wanted_benefits']`, 후보 **0건**.
  이건 오류가 아니라 데이터의 사실입니다 — 넷플릭스 포함 요금제는 45건이고 **최저 실납부 59,000원**이라
  5만원 예산과 동시에 만족하는 상품이 없습니다. `blockers`가 그대로 알려 줍니다:
  `wanted_benefits 빼면 2,265건 / budget_max_won 빼면 34건(최저 59,000원)`.
  ⚠️ 다만 이 경우 화면에는 "추천 가능한 요금제를 찾지 못했습니다"만 나옵니다.
  `blockers`를 결과 화면에서 어떻게 보여주는지는 확인하지 못했습니다(아래 5번).

### 시나리오 4 — 혜택은 상관없음 (= 우선순위 1의 재현 입력)
입력: `월 데이터 20GB 이상, 요금 3만원 이하로 추천해줘. 만 30세이고 혜택은 상관없어.`

- 기존 문제: 후보 4건, `attempt=3`, `passed=False`(테더링 환각 오판). 페이백·혜택 개수가 효용에 반영됨.
- 수정 기준: 평가가 보는 사실 범위 일치 + 예산 하한 제거 + 혜택 축 중립화.
- 실제 결과: 후보 **578건**, `attempt=1`, `passed=True`.
  1순위 `너겟49`(7,000원 / 120GB)는 혜택이 아니라 데이터·가격으로 1위입니다(혜택 축은 전 후보 0.5).

### 시나리오 5 — 프로모션 6개월 후 인상
입력(코드 검증): `discounted_fee=19,000 / monthly_fee=39,000 / discount_period_months=6`

- 수정 기준: 비교 구간 총비용 = 할인가×할인개월 + 정가×나머지, 구간은 12개월 하나.
- 실제 결과: 수동 계산 `19,000×6 + 39,000×6 = 348,000` = 화면 `totalNum` **348,000** (일치).
  랭킹 평균요금도 `348,000/12 = 29,000` 일치. `priceRisesAfter=6`, `priceRisesLater=False`.

### 시나리오 6 — 월 8천원 페이백 12개월 / 일시금 2만원
입력(코드 검증): `benefit_summary([매달 8천원 페이백(12개월) 96,000원, 가입 사은품 상품권 2만원])`

- 기존 문제: 총액/월액 구분은 있었으나 **제공 기간을 무시**해 6개월 페이백도 구간 내내 계산됐다.
- 수정 기준: 월 지급액 × min(제공개월, 12) / 12. 일시금은 기간 1개월.
- 실제 결과: `{'monthly_won': 9667, 'deductible_won': 9667, 'estimated': False, 'conditional': 0}`
  (= 8,000 + 20,000/12). 함께 확인한 값:
  - `매달 3.4만원 페이백 (6개월)` → 월 **17,000** (12개월 합 204,000 = 실제 지급 총액과 일치.
    수정 전이라면 34,000×12 = 408,000으로 두 배가 됐다)
  - `넷플릭스 17,000원` → `monthly_won=17,000`, `deductible_won=0`, `estimated=True`
    (기간 미상 + 이용 여부 미확인 → 차감 안 함)
  - `매달 5천원 페이백 (평생)` → `estimated=False` (무기한은 기간 미상과 구분됨)
  - 택1 9,000 / 13,500 → **13,500 하나만** 계상
  - 카드 실적 조건이 붙은 페이백 → `deductible_won=0`, `conditional=1`

### 시나리오 7 — 현재 요금제가 유리하거나 비교 정보가 부족한 경우
입력: `지금 쓰는 요금제가 월 3만원인데 바꾸는 게 나을까? 만 30세.`

- 실제 결과: **미해결 (후순위 항목)**. `budget_max_won=30000`이 만들어졌습니다.
  사용자는 현재 납부액을 말한 것이지 예산 상한을 말한 게 아닙니다(→ `reference_fee_won`이 맞습니다).
  그 결과 "현재 요금제보다 싼 것"만 후보가 되고, 리포트는 현재 요금제의 데이터·혜택을 모르는 채
  `현재 사용 중인 요금제보다 더 저렴하면서도 데이터와 통화 혜택이 우수한` 이라고 단정했습니다.
  `followup_question`("어떤 조건을 고려해서 바꾸고 싶은지")은 나왔습니다.
  유지 권고와 판단 불가는 아직 구분되지 않습니다. 재현 방법은 위 입력 그대로입니다.

### 시나리오 8 — 담기 → 비교 → 새로고침
- **브라우저로 직접 확인하지 못했습니다.** 확인한 범위: `tsc --noEmit` + `vite build` 통과,
  `/api/stats`·`/api/plans`(정렬 5종)·`/api/plans/{id}` 응답 정상, 새 필드 3개 포함,
  `compareMonths == rankingMonths == 12`, 탐색 화면 facet 분포 유지
  (`SKT 662 / KT 1021 / LGU+ 1076 / MNO 직영 556`, `lt3 196 / 3to10 567 / 10to20 690 / gte20 943 / unlimited 363`).
  비교함·복원 로직(`localStorage`/`sessionStorage`)은 건드리지 않았습니다.

> 위 결과는 과정의 정합성 확인입니다. 추천 정확도나 사용자 만족도 수치가 아닙니다.
> 비교 실험을 하지 않았으므로 "추천이 좋아졌다"고 말할 수 없습니다.

---

## 5. 아직 남은 것 / 재현 방법

1. **시나리오 7 — 현재 요금제 유지 권고와 판단 불가 구분** (후순위 1, 미착수)
   - 재현: `지금 쓰는 요금제가 월 3만원인데 바꾸는 게 나을까? 만 30세.`
   - 할 일: (a) "현재 월 N원"을 `budget_max_won`이 아니라 `reference_fee_won`으로 보내는 보정,
     (b) 현재 요금제의 데이터·혜택을 모르면 "유리하다"고 단정하지 않고 확인 안내로 끝내기.
     `프로필에 reference_* 가 비어 있음` = 판단 불가, `reference가 파레토 우위` = 유지 권고.
2. **리포트가 여전히 카드 스펙을 한 번씩 반복한다.**
   프롬프트에 금지 규칙과 좋은 예/나쁜 예를 넣어 많이 줄었지만 `gpt-4o-mini`가 완전히 따르지는 않습니다
   (`"120GB의 데이터와 무제한 음성 및 SMS를 제공합니다"` 같은 문장이 남습니다).
   정확성 문제는 아닙니다. 더 줄이려면 `MODEL`을 올리거나 예시를 몇 개 더 붙이는 쪽입니다.
   후처리로 문장을 지우는 방식은 넣지 않았습니다(멀쩡한 비교 문장까지 지울 위험).
3. **후보 0건일 때 `blockers`가 화면에서 어떻게 보이는지 확인 못 함** (시나리오 3).
   서버는 `{'field','label','value','candidates','minimum_fee'}`를 내려보냅니다.
4. **비슷한 추천 반복 완화** (후순위 2, 미착수). `_dedupe_by_name`은 이름이 같으면 실납부가 가장 싼
   1건만 남깁니다. 가입 조건·혜택이 다른 동명 상품이 지워지는지는 점검하지 않았습니다.

---

## 6. 데이터 파일 / 크롤러

- **루트 `data/`는 건드리지 않았습니다.** `--promote`도 실행하지 않았습니다.
- 두 스냅샷은 **수집 시점이 다릅니다.** 차이를 단종으로 읽으면 안 됩니다.

| | 요금제 | 혜택 행 | `crawled_at` |
|---|---|---|---|
| 루트 `data/` (서비스 입력) | 2,759 | 5,581 | **2026-08-21** |
| `crawler/data/final/` (새 수집) | 2,591 | 5,609 | **2026-09-17** |

  요금제는 168건 줄었는데 혜택 행은 28건 늘었습니다. 27일 차이가 나는 서로 다른 수집분입니다.
- **스키마 차이 (승격 전 반드시 확인)**: 새 혜택 CSV에만 `benefit_value_basis`, `benefit_months`,
  `benefit_data_gb` 컬럼이 있습니다. 루트 CSV에는 없습니다.
  `agent/data.py`는 두 스키마를 모두 읽습니다 — 컬럼이 있으면 그 값을 믿고(`basis == "monthly"` 등),
  없으면 혜택 이름 문자열에서 기간·월지급액을 파싱합니다(`_monthly_worth`).
  승격하면 **금액 판정 경로가 문자열 파싱에서 크롤러 컬럼으로 바뀌므로**
  `benefit_value_won` / `benefit_deductible_won`이 전체적으로 달라질 수 있습니다.
  승격 전에 두 스냅샷으로 `benefit_summary()` 결과를 비교한 차이 요약을 남기세요.
- 이번 작업에 재크롤링은 하지 않았습니다.

---

## 7. 실행 중인 서버

**없습니다.** 이 작업에서 uvicorn/vite 를 상주시키지 않았습니다. 필요하면:

```bash
# API (저장소 루트에서)
uvicorn backend.main:app --reload --port 8000
# 프런트 (frontend/ 에서)
npm run dev
```

`frontend/node_modules`는 이 작업 사본에서 원본 체크아웃을 가리키는 **디렉터리 정션**으로 걸어 뒀고,
`crawler/data/final/`은 크롤러 테스트를 돌리려고 복사해 둔 것입니다. 둘 다 gitignore 대상입니다.

---

## 8. 다음 작업자가 가장 먼저 할 일

1. `git log --oneline -2` 로 두 번째 커밋만 리뷰하세요. 그게 이번 작업분 전부입니다.
2. **비교 구간 12개월을 팀과 확정하세요.** 이전 6개월 결정과 충돌합니다(위 2번 ⚠️).
   되돌리는 비용은 `agent/mcda.py` 한 줄 + 테스트 한 줄입니다.
3. 브라우저로 시나리오 8(담기 → 비교 → 새로고침)을 눈으로 확인하세요. 코드로는 빌드까지만 봤습니다.
4. 그다음이 시나리오 7(유지 권고 / 판단 불가 구분)입니다.
