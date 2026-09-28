# HANDOFF

**읽는 법:** "현재 상태"만 읽으면 지금 코드가 어떤 상태인지 안다. 그 아래 "이력"은 결정이 내려진 당시의
기록을 최신순으로 그대로 둔 것이라, 뒤 항목의 수치·경로는 그 시점 기준이다.

## 현재 상태 (2026-09-21, 브랜치 `fix/plan`)

### 폴더

```
agent/        LLM 파이프라인 (profiling → 조건 검증 → recommend → report → 설명 검증), 추천 로직(mcda.py), 데이터 로드(data.py)
backend/      FastAPI            frontend/   React + Vite
crawler/      통신 3사·모요 수집·일일 갱신 (결과는 crawler/data/, git 제외)
data/         서비스가 읽는 CSV(고정) · baseline/(기준 유도용 고정본) · eval/(평가용 입력·결과) · synthetic_original/(합성 가입이력)
experiments/  실험·평가 스크립트. 서비스는 import 하지 않는다 → experiments/README.md
notebooks/    가중치 학습·EDA 노트북
outputs/      분석노트/ · 지표표_방법별비교.json  (발표 자료는 저장소에 올리지 않는다)
```

### 지금 적용 중인 정책값 — 바꿀 곳은 각각 한 줄이다

| 정책 | 값 | 위치 |
|---|---|---|
| '무제한' | 기본량 무제한 OR (제공량 ≥ 100GB AND 소진 후 ≥ **10Mbps**) — 잠정("일단") | `agent/data.py` `UNLIMITED_MIN_GB` / `UNLIMITED_QOS_MBPS` |
| 비용 비교 기간 | **12개월** (화면 총비용·혜택 월 환산). 6개월 의견이 있어 팀 확정 대기 | `agent/mcda.py` `COMPARE_MONTHS` |
| 순위의 가격 축 | 지금 내는 월 요금(할인가). "갈아탈 가치" 비교만 12개월 평균 | `agent/mcda.py` `_PRICE_HORIZON_MONTHS`, `_switching_monthly_fee` |
| 효용 눈금 | 요구를 말한 축은 요구 기준(예산 대비·요구량 대비), 나머지는 **카탈로그 고정 범위**(속도 0~10Mbps · 데이터 log 0~300GB · 요금 √0~10만원 · 테더링 0~200GB). 후보 집합 min-max 는 쓰지 않는다 | `agent/mcda.py` `_*_RANGE_*` (점검: `experiments.weight_scale_check`) |
| 추천 대상 | **알뜰폰**(모요의 통신 3사 직판 브랜드 제외). 통신 3사는 `include_mno`·`carrier_type=MNO`·브랜드 지정일 때만 후보. 탐색·비교함·'현재 요금제' 비교에는 전체가 쓰인다 | `agent/data.py` `filter_candidates` / `wants_big3` / `BIG3_DIRECT_BRANDS` |
| 추천 노출 개수 | 5 | `agent/agents/recommend.py` `TOP_N` |
| 페이백 상품 | 청구액이 확인된 81건만 추천·총비용에 포함, 미확인 3건 제외 | `data/페이백_청구액_검증.csv` → `agent/data.py` `_apply_verified_billing` |
| 서비스 입력 CSV | 루트 `data/` 는 **일부러 고정**. 크롤러 결과를 복사하지 않는다 | — |

'무제한' 문턱을 바꾸면 파레토 풀·코사인 카탈로그까지 값이 흘러간다. 아래 순서로 다시 돌려야 발표 수치가 맞는다.

```bash
python -m unittest test_service_process            # 고정본 건수(현재 403) 단언이 같이 바뀐다
python -m experiments.run_metric_table              # outputs/지표표_방법별비교.json
python -m experiments.weight_scale_check            # 가중치 재현 + 고정 눈금이 카탈로그를 덮는지
```

### 미해결

- ~~학습 모집단 ≠ 적용 모집단~~ → 2026-09-21 추천 대상을 알뜰폰으로 한정해 해소. 단 사용자가 통신 3사를 직접 찾으면 같은 가중치가 쓰인다.
- **통신 3사 '현재 요금제'를 이름으로 못 찾는 경우가 많다.** DB 의 통신 3사 요금제가 온라인 전용 위주라서다(SKT 73종, '5GX 레귤러' 없음). 금액·데이터로 말하면 동작한다.
- **SMAA-2 가중치가 점추정이다**(price 0.483 ± 0.008). "사람마다 다른 선호"가 들어 있지 않다.
- **순위를 실제로 가르는 축은 질의마다 다르다.** 사용자가 조건으로 말한 축(N GB 이상·혜택·통화량)은 필터가 되어 통과한 후보가 전부 같은 값이 된다. 남는 것은 가격, (요구량을 필수로 말하지 않았을 때의) 데이터, 소진 후 속도다. '속도 우선'·'데이터 우선'은 Top-5 를 움직이고 '통화 우선'은 요청이 없으면 그대로다.
- **혜택 가중치의 의미가 다르다.** 학습은 "OTT 구독료", 서비스 축은 "요청 혜택 충족". 순위에서는 상수라 영향은 없지만 "학습된 가중치"라고 말할 수 없다.
- **Evaluation 을 두 지점 검증으로 바꿨지만(2026-09-28) 실제 LLM 으로 판정 빈도를 잰 적은 없다.** 검증 기록은 `state.eval_log` 에 남고, 무엇을 고쳤거나 걸린 경우만 서버 로그에 WARNING(`evaluation {...}`)으로 나온다.
- Precision +27% 표를 만든 `run_weight_arms`(0e8a0ca 이후 실행 불가)와 그 결과 파일은 2026-09-21 삭제했다. 발표 덱은 이 수치를 쓰지 않는다(복구: `git checkout 904573c -- experiments/run_weight_arms.py experiments/mcda_original.py data/eval`).
- '무제한' 10Mbps 로 "무제한 + 3만원 이하" 후보가 9건뿐이다. 대용량+5Mbps 를 다시 포함하는 방향 전환 버튼은 미정.

### 확인

```bash
python -m unittest test_service_process
python -m agent.data && python -m agent.mcda && python -m backend.plans && python -m agent.agents.evaluation
cd frontend && npm run build
```

---

# 이력 (최신순 · 당시 기록 그대로)

## Evaluation 을 두 지점 검증으로 (2026-09-28, 브랜치 `feature/evaluation-gates`)

맨 끝에서 한 번 보고 틀린 단계로 되돌리던 구조를, 틀린 것이 생기는 지점 바로 뒤에서 검사해 그 자리에서
고치는 구조로 바꿨다.

```
profiling → ① profile_check → recommend → report → ② evaluation → END   (② 에서만 report 1회 재작성)
```

- **되돌림을 없앤 이유.** recommend 는 같은 입력이면 같은 결과라 되돌려도 결과가 같았다. profiling 도 오류
  대부분이 LLM 뒤의 정규식 보정에서 나와(예: `_apply_explicit_data_max` 가 1턴의 '10GB 이하'를 다시 채움)
  다시 돌려도 같은 값이 나온다. 리포트명·할인가 검사는 `_ensure_all_ranks`·`_ensure_promo_notices` 가 코드로
  채워 항상 통과했다. 기존 판정 LLM 은 사용자 발화를 보지 않아 프로필 오류를 잡을 수 없었다.
- **① 조건 검증** (`profile_check_node`): LLM 이 발화와 조건을 대조해 문제를 고르고(뺀 조건이 남음 / 값이 틀림 /
  빠짐 / 희망이 필수로 바뀜 / 말한 적 없음), **코드가 인용이 실제 발화에 있는지 확인한 것만** profile 에 바로
  적용한다. 해제는 `_apply_relaxed_fields` 를 노드 안에서 직접 부른다(`state.relaxed_fields` 에 넣으면 현재
  요금제 기준선이 꺼진다). '말한 적 없음'은 자동으로 지우지 않는다('3만원대'→39,999 같은 표준값까지 지워진다).
  교정 후 예산·데이터 신호가 모두 사라지면 되묻는다. LLM 이 실패하면 검증을 건너뛴다.
  - 값 교정은 인용 속 '수치+단위+경계어'가 붙어 있고 방향(이하=상한, 이상=하한, '넘는 건 싫어'=상한, '이상 아니면 싫어'=하한)과
    단위(원/GB/분)가 맞을 때만 한다. GB 는 주제어로 데이터·테더링을 가른다. 근삿값('N만원대·정도'), 지금 내는 요금(단위 없이
    말한 것 포함), 인용 뒤(같은 발화 포함)에서 다시 말하거나 뺀 값은 고치지 않는다. 상한을 넣을 때 같은 값 이상의 하한이
    있으면 그 하한이 잘못 읽힌 것이라 함께 지운다(데이터 상·하한도 같다).
  - 철회는 인용이 그 필드의 주제(예산·데이터·통화…, 또는 버튼 문구)를 말할 때만 적용한다. 주제를 밝히지 않은
    예산 철회는 'N만원대'의 하한까지 함께 푼다.
  - 화면의 '조건 풀기' 버튼으로 푼 필드(`relaxed_fields`)는 어떤 판정이 와도 건드리지 않는다.
  - 혜택 목록의 철회는 뺀다고 말한 항목만 뺀다('혜택은 없어도 돼'처럼 항목을 지목하지 않으면 전부). 희망 완화는
    혜택을 말한 절에 '반드시·꼭'이 있거나 다른 혜택을 필수라고 했으면 하지 않는다.
  - 한계: 예산을 단위 없이만 말하면(`예산 30000 이하로`) profiling 의 `_drop_phantom_budget` 이 지운 뒤 바로 되묻기로
    끝나 ① 까지 오지 않는다. '1만 5천원 이하'를 5,000 으로 읽는 `_repair_budget_bounds` 오류도 ① 이 되돌릴 수는 있지만
    근본 수정은 profiling 쪽 일이다. OTT 필수 + 음악 희망처럼 한 필드 안에서 강도가 갈리는 경우는 고치지 않고 둔다.
- **② 설명 검증** (`evaluation_node`): 화면에 나가는 카드 문장(`ranked[].reason`)만 코드로 본다. 데이터에 없는
  금액(허용 집합 = 추천 전체·현재 요금제·예산의 금액과 그 차액·12개월 합), 현재보다 비싼데 싸다는 말,
  위약금·해지·속도 보장 약속. 걸리면 report 를 1회 다시 쓰고, 그래도 걸리면 그 문장만 뺀 뒤 통과시킨다.
  LLM 판정은 쓰지 않는다. plan_id·필수 조건·순위 검사는 로그(warning)로만 남긴다.
  - 방향 검사는 '현재 요금제보다·지금 쓰는 요금에 비하면' 바로 뒤에 싸다는 말이 올 때만 본다('현재 월 6,800원'은
    지금 시점, '2순위보다 저렴'은 후보끼리 비교). 비싸다는 말이나 할인 뒤 인상('이후·끝나면')을 함께 밝히면 정상이다.
  - 약속 검사는 약속 표현 바로 뒤에서 부정하거나 확인을 권하면 정상으로 본다('속도 보장은 아닙니다').
  - 금액은 요금·차액·할인 기간 합계·12개월 합계에 대해 반올림('약 3천원'), 버림('6천원 넘게'), 구간('1만 원대') 표기를
    받는다. 한계: 허용 집합이 추천 전체라 다른 후보의 금액을 이 후보 금액처럼 쓴 문장은 잡지 못한다.
  - 재작성 호출이 실패하거나 빈 응답이면 첫 리포트를 그대로 두고 걸린 문장만 뺀다(502 로 끝내지 않는다).
- **바깥 계약은 그대로.** `evaluation`(`passed`/`feedback`/`retry_target`)과 `attempt` 형태를 유지해 백엔드·프론트는
  고치지 않았다. 문장을 빼고 통과하면 `passed=True` 라 '잠정 결과' 배너는 사실상 뜨지 않는다.
- 사례(LLM 없이 재현): '월 2만원 이하, 데이터 10GB 이하' → '데이터 상한은 빼고 다시 추천해줘'. 지금까지는 2턴에서도
  10GB 상한이 남아 후보 794건, 1순위 10GB 요금제. ① 이 2턴 인용으로 해제하면 후보 1,305건, 1순위 125GB·월 7,400원.
- `get_eval_llm` 은 조건 검증 전용이 되어 timeout 20초·재시도 1회로 줄였다.
- 확인: `unittest test_service_process` 77개, `agent.agents.evaluation`·`report`·`data`·`mcda`·`backend.plans` self-check,
  LLM 을 흉내 낸 그래프 전체 실행(교정 → 재작성 1회 → 문장 삭제) 통과. **실제 LLM 으로는 돌려 보지 않았다.**
- P/R(`run_testset`)은 이 변경과 09-22 데이터 갱신 등이 모두 반영되지 않은 결과라 다시 돌려야 한다.

---

## '무제한' 속도 문턱을 4.44Mbps → 10Mbps 로 (2026-09-21)

아래 "100GB＋4.44Mbps" 정의 위에서 한 번 더 좁혔다. **팀 결정이고 "일단"이다.**

```
무제한 = 기본량 무제한
       OR (기본 제공량 >= 100GB  AND  소진 후 >= 10Mbps)
```

근거(고정 분석본 2,759건).
'통신 3사' = `carrier_type=MNO` + 모요에 올라온 3사 직판(`mvno_brand`가 SKT/KT/LG U+):

| 등급 | 조건 | 알뜰폰 | 통신 3사 |
|---|---|---:|---:|
| 무제한 | 기본량 무제한 | 0 | 363 |
| 사실상 무제한 | 소진 후 10Mbps 이상 | 40 | 0 |
| 대용량 + 5Mbps | 5Mbps + 기본량 100GB 이상 | 350 | 29 |

통신 3사는 셋째 줄 규격을 무제한이라 부르지 않고 그 아래 등급으로 판다. 직전 정의는 그 줄까지
무제한으로 받고 있었다. 알뜰폰에는 기본량 무제한이 0건이라 최상위 군집인 10Mbps 를 문턱으로 삼았다.

- 바꾼 것: `agent/data.py`의 `UNLIMITED_QOS_MBPS = 10.0` **한 줄**(＋주석·self-check). 제공량 100GB 조건은
  안전장치로 그대로 둔다(10Mbps 상품이 전부 180GB 이상이라 지금은 결과를 바꾸지 않는다).
- 딸려 고친 것: `backend/analysis.py` 민감도 격자에 5Mbps 줄 추가·한계 문구, `ResultScreen.tsx` 안내 문구,
  `test_service_process.py` 고정본 건수 782 → **403**.
- 결과: 고정본 후보 2,007 → 782 → **403**. 현재 통합본에서 "무제한" 후보 211건(기본량 무제한 172 + QoS형 39),
  상위 5개는 200GB＋10Mbps / 22,000~24,200원. **"무제한＋3만원 이하"는 9건**뿐이고 한 브랜드의 옵션 5종이 상위를 채운다.
- 지표표가 바뀐다: `effective_unlimited`가 파레토 풀과 코사인 카탈로그에 쓰여서 `outputs/지표표_방법별비교.json`을
  다시 뽑았다(③ 파레토 18.3 → 29.2%, 코사인 96.7/15.8/29.3/5 → 95.0/9.2/33.3/7). **발표초안_프롬프트.md 의 표는 옛 값이다.**
- 확인: `unittest test_service_process` 43개 통과, `agent.data`·`backend.plans`·`agent.cosine_recommendation`·
  `agent.agents.evaluation` self-check, `npm run build` 통과.
- 남은 것: 대용량＋5Mbps(알뜰폰 329건)를 다시 포함하는 방향 전환 버튼을 둘지 미정.
  `outputs/분석노트/무제한_정의_근거표.md`는 옛 정의(4.44Mbps) 기준 문서라 아직 갱신하지 않았다.

---

## '무제한' 정의를 제공량＋속도 두 조건으로 (2026-09-18, 위 작업에 이어서)

### 증상

"데이터 무제한인 요금제 추천해줘" → **4.5GB / 100원** 상품이 1순위. 정규화 문제가 아니었다.

`is_effectively_unlimited()`가 **소진 후 속도만** 봤다(`QoS >= 1Mbps`). 그래서 '4.5GB + 1Mbps'와
'200GB + 5Mbps'가 같은 문으로 들어왔다. 이 정의에 걸린 QoS형 1,644건의 **중위 제공량이 24GB**,
절반 이상(868건)이 50GB 미만이었다. 데이터 축이 사실상 상수가 되니 가격 축(100원 = 효용 1.0)이
그대로 이겼다.

### 새 정의

```
무제한 = 기본량 무제한
       OR (기본 제공량 >= 100GB  AND  소진 후 >= 4.44Mbps)
```

`agent/data.py`의 `UNLIMITED_MIN_GB = 100.0`, `UNLIMITED_QOS_MBPS = QOS_HD_MBPS` 두 상수와
`is_effectively_unlimited(data_unlimited, qos_mbps, data_gb)` 한 함수가 정의의 전부다.
나머지(필터·평가·카드·탐색)는 전부 `effective_unlimited` 컬럼을 읽어 따라온다.

**적용 결과: 2,007건 → 782건** (기본량 무제한 363 + QoS형 419). 무제한 요청 상위 5개가
4.5GB/100원에서 **125~150GB + 5Mbps / 21,400~22,400원**으로 바뀌었다.

### 근거 (EDA + 레퍼런스)

제공량 구간별 소진 후 속도 — **100GB에서 계단이 진다**:

| 제공량 | 건수 | 소진 후 중위 | ≥4.44Mbps |
|---|---:|---:|---:|
| 1~10GB | 321 | 1.0Mbps | 0% |
| 10~40GB | 539 | 1.0Mbps | 0% |
| 70~95GB | 270 | 3.0Mbps | 0% |
| **100~125GB** | **235** | **5.0Mbps** | **98%** |
| 150GB+ | 163 | 5.0Mbps | 98% |

군집도 칼같다: 71GB 232건 전부 3.0Mbps, 95GB 39건 전부 3.0Mbps, 100GB 196건 중 193건이 5.0Mbps.
**150GB로 올려도 소진 후 속도 중위는 5Mbps로 같다** — 후보만 433→163건으로 줄고 가격 중위가
24,200→31,480원으로 오른다. 그래서 100GB에서 멈췄다.

보조 근거 둘:
- 스마트초이스 생활패턴 최상위 구간이 '하루 3시간 이상 영상 = 월 90GB 이상, **상한 없음**'
  (`agent/usage.py` `SMARTCHOICE_USAGE_RANGES_GB`). 100GB는 그 구간을 기본 제공량만으로 덮는 최소 규격.
- 과기정통부 통계 기준 5G 월평균 사용량 26GB대(2022년 6월 26.16GB)의 약 4배.

속도 문턱을 1Mbps → 4.44Mbps(720p)로 올린 이유는 규제다. 공정위는 2021년 SKT 5G 요금제에
"소진 후 최대 **1Mbps**인데 명시하지 않아 소비자 오인"으로 경고했다. 1Mbps는 규제가 무제한으로
인정한 속도가 아니라 **문제 삼은 속도**다. 종전 정의가 규제와 반대 방향이었다.

> **100GB는 규제가 정한 값이 아니라 팀이 정한 서비스 정책값이다.** 발표에서 그렇게 말해야 한다.
> 되돌리거나 조정하려면 `agent/data.py`의 상수 두 줄만 고치면 전부 따라간다.

### 화면

- 결과 화면에 **‘무제한’을 이렇게 봤습니다** 안내를 추가했다(`UnlimitedBasis`). 무제한을 요청한
  경우에만 뜬다. 기준 숫자는 서버가 내려준 `unlimitedPolicy`를 읽는다 — 화면에 상수를 복제하지 않는다.
  추천 5개 중 기본량 무제한 몇 개 / 대용량＋속도 유지 몇 개인지도 함께 보여준다.
- 같은 자리에 **방향성 질문** 버튼 둘. `기본 제공량 자체가 무제한인 상품만` ↔
  `소진 후 속도가 유지되면 괜찮아요`. 기존 후속 질문 경로를 그대로 탄다.
- `agent/agents/profiling.py` `_repair_latest_unlimited_strictness`: 이 둘도 **마지막에 말한 쪽이
  이긴다**. `_repair_latest_priority`와 같은 종류의 고착 버그를 막는다.

### 확인

- `unittest test_service_process`: **41개 통과**. 새 테스트 2개
  (`test_unlimited_needs_both_allowance_and_speed`, `test_latest_unlimited_scope_wins`).
- `agent.data` self-check, `backend.plans`, `agent.agents.profiling` self-check, `npm run build` 통과.
- 브라우저 실측(API 8009 / frontend 5180): "데이터 무제한인 요금제 추천해줘" → 후보 584건,
  상위 5개 전부 125~150GB + 5Mbps. `기본 제공량 자체가 무제한인 상품만` 클릭 → **후보 180건,
  전부 기본량 무제한**(너겟59 계열 59,000원~), 안내 문구와 버튼 상태도 함께 전환.
- 부수 효과: 앞서 검토하던 `_UNLIMITED_TIER_FIT` 효용 패치는 **필요 없어졌다.** 필터에서
  소량 상품이 걸러지므로 등급 효용을 건드리지 않아도 증상이 사라진다.

### 정의 변경에 딸려 고친 것 (같은 날 후속)

1. `agent/data.py` `filter_candidates` 주석이 옛 정의("1Mbps 이상 QoS형")를 설명하고 있었다.
2. `backend/analysis.py` 한계 문구 "1Mbps 기준은 서비스 정책입니다"를 새 정의로 교체.
3. `backend/analysis.py` 민감도 분석이 **속도 한 축만** 흔들고 있었다(`for speed in [...]`).
   실제 정의는 두 조건을 함께 보므로 **제공량 × 속도 격자**로 바꿨다. `minGb=0` 줄이 옛 정의다.

   | 최소 제공량 | 소진 후 | 후보 | 비무제한 중위 제공량 |
   |---|---|---:|---:|
   | 없음(옛 정의) | 1.0Mbps | 2,007 | 24GB |
   | 없음 | 3.0Mbps | 1,292 | 71GB |
   | 70GB | 1.0Mbps | 1,110 | 100GB |
   | **100GB** | **4.44Mbps** | **782** | **110GB** | ← 현재 기준 |
   | 150GB | 4.44Mbps | 523 | 150GB |

4. 탐색 화면 `data` 필터의 '무제한' 라벨을 **'기본량 무제한'**으로 바꿨다. 같은 단어인데
   탐색은 363건, AI 추천은 782건이라 건수가 달라 보였다. `tier` 그룹이 이미 쓰던 표현에 맞췄다.
   (필터 동작은 그대로다 — `data` 그룹은 제공량 구간 필터라 `data_unlimited`만 보는 게 맞다.)

`test_eda_matches_runtime_rows_and_missing_is_not_zero`도 격자에 맞게 고쳤다. 단조 감소 한 줄
대신 **두 축 각각에 대한 단조성**과 **'현재 기준' 칸이 실제 `effective_unlimited` 건수와 일치**하는지를
본다. 표와 실제 필터가 갈라지면 실패한다.

### 남은 것

- `_UNLIMITED_TIER_FIT`(qos_hd 0.95 등)은 그대로다. 무제한 요청 후보 안에서는 이 축이 거의
  상수라 순위에 영향이 없다. 필요해지면 그때 손대면 된다.
- 스마트초이스 구간 수치가 `usage.py`에 숫자로만 있고 **원문 URL이 없다.** 발표에 쓰려면 출처 확인 필요.
- 과기정통부 최신 통계(2024년 12월말 기준)는 첨부 xlsx 안에만 수치가 있어 이번에 확인하지 않았다.
  발표에 인용하려면 받아서 확인해야 한다.

---

## 이전: 청구액 분리 / 현재 요금제 대비 변화 / 선호 변경 재계산

## 멘토 피드백 3건 반영 (2026-09-18, 3e8825e + 이전 미커밋 변경 위에 이어서 작업)

작업 위치 `.claude/worktrees/plan-fix`, 브랜치 `fix/plan`. 이번 변경도 **아직 커밋하지 않았습니다.**
이전 미커밋 변경(페이백 84건 계산 제외, 발표 초안)은 그대로 보존한 채 그 위에 이어서 고쳤습니다.

### 1. 실제 청구액과 페이백 표시가 분리

- 모요 상세페이지에 두 금액이 따로 있습니다. **"월 납부액"이 실제 청구액**, **"페이백 포함하면"이
  체감 표시가**입니다. 기존 CSV의 페이백 상품 가격은 목록 카드의 체감가였습니다.
- `crawler/src/crawl_moyo.py`에 `parse_billing_prices()` / `apply_billing_prices()`를 추가하고
  `parse_detail` → `parse_card` 경로에 연결했습니다. 앞으로의 전체 크롤에도 그대로 적용됩니다.
  `crawler/src/schema.py`의 PLAN_COLUMNS에 `payback_included_fee`, `billing_price_verified` 추가.
- `apply_billing_prices`는 청구액(B)과 카드 정가(Y)를 비교해 세 갈래로 나눕니다.
  B<Y 진짜 요금 할인(기간 유지) / B==Y 할인 없이 페이백만(카드의 기간은 **지급 기간**이므로 삭제) /
  B>Y 카드의 '정가'까지 체감가였음(둘 다 청구액으로, 기간 삭제).
- `crawler/src/verify_payback_prices.py` (신규): 서비스 CSV의 페이백 84건만 상세를 받아 청구액을
  확인하고 정정표를 만듭니다. **표시가에 페이백을 더하거나 정가로 치환하지 않습니다.**
- 결과: **81건 확인 / 3건 미확인**(36963, 37443, 37124는 상세에 "월 납부액" 블록이 없음).
  미확인 3건은 `billing_price_known=false`로 남아 추천·총비용 계산에서 계속 빠집니다.
- 정정표는 `crawler/data/final/페이백_청구액_검증.csv`에 만들고, 값을 확인한 뒤
  **`data/페이백_청구액_검증.csv`로 복사**했습니다. 서비스가 읽는
  `data/통신요금제_통합데이터_최종.csv`는 **건드리지 않았습니다**(추천 입력 고정용).
  `agent/data.py`의 `_apply_verified_billing()`이 로드 시 이 표로 가격을 바로잡습니다.

| 원문 확인 사례 | 기존 CSV(청구액/정가/기간) | 정정 후 | 페이백 |
|---|---|---|---|
| 36334 너겟49 | 7,000 / 7,000 / — | **49,000 / 49,000 / —** | 34,000×6, 8,000×12 |
| 37403 핀다이렉트Z 100GB+ | 6,900 / 36,900 / 7 | **41,900 / 41,900 / —** | 35,000×7 |
| 26613 쉐이크 LTE 100GB+ | 11,500 / 42,300 / 7 | **16,400 / 42,300 / 7** | 5,000×기간미상 |
| 6804 슬림 유심 500MB | 0 / 1,700 / 6 | **1,700 / 1,700 / —** | 2,000×6 |

- 회귀 테스트: `crawler/src/test_payback_billing.py` 6개(네트워크 없이 도는 고정 사례),
  `test_service_process.py`에 청구액 복귀·이중차감 방지 2개.

### 2. 현재 요금제 대비 변화 요약

- `backend/plans.py`에 `monthly_fee_schedule()`과 `reference_delta()`를 추가했습니다.
  `total_cost()`는 이제 `sum(monthly_fee_schedule(...))`이라 **표의 총비용과 그래프가 같은 계산**입니다.
  비교 구간은 기존 `agent/mcda.COMPARE_MONTHS = 12` 그대로 씁니다(새 상수 없음).
- `referencePlan`(카탈로그 상품)뿐 아니라 `referenceFacts`(사용자가 말한 납부액·데이터량)로도
  비교합니다. **상품명을 몰라도** 월 납부액과 데이터량만 알려주면 비교됩니다.
- 각 추천 카드에 `referenceDelta`가 붙고 상세 리포트의 `05 현재 요금제 대비 변화`에서
  월 납부액 / 12개월 총비용 차이 / 데이터 제공량 / 소진 후 속도 / 할인 종료 시점과 이후 가격을
  표로, 12개월 월별 청구액을 CSS 막대 그래프로 보여줍니다(차트 라이브러리 추가 안 함).
- 모르는 값은 0이 아니라 `null`이고 화면은 '확인 필요'로 씁니다. 화면에 "요금만 본 차이이고
  확정 절약액이 아니다", 반영하지 못한 항목(해지 위약금, 결합·가족할인 손실, 현재 요금제의
  할인 종료 시점), "현재 납부액이 12개월 유지된다고 가정"을 그대로 적습니다.

### 3. 선호 변경 재계산

- 결과 화면에 `무엇을 더 중요하게 볼까요` 버튼 4개(가격/데이터/소진 후 속도/혜택). 기존 후속 질문
  경로를 그대로 타고, 필수조건을 문장에 다시 실어 보냅니다. 대화·비교함은 유지됩니다.
- **새 가중치 공식이나 슬라이더를 만들지 않았습니다.** 가중치는 기존 SMAA-2 표본 +
  `_boosted()` 우선순위 가산 그대로입니다. 바뀐 것은 아래 세 가지 오류뿐입니다.

| 고친 것 | 증상(실측) | 위치 |
|---|---|---|
| 선호가 최신 발화로 안 바뀜 | "가격 우선" 다음에 "데이터 우선"을 눌러도 `priorities`가 `['price']` 고정 | `agent/agents/profiling.py` `_repair_latest_priority` |
| 선호가 필수조건으로 둔갑 | "가격을 가장 중요하게"가 `comparison_goals=['cheaper']`로 잡혀 후보가 560→0건 | `agent/agents/profiling.py` `_repair_general_comparison` |
| 데이터 우선이 순위를 못 바꿈 | `min_data_gb` 필터를 통과한 후보는 데이터 효용이 전부 1.0(상수)이라 축이 무시됨 | `agent/mcda.py` `_utility_rows` |

### 그 밖에 이번에 고친 것

- **'더 나은 요금제' 요청에 우위 후보가 없으면 '비교 못 함'이 아니라 '유지'**로 답합니다
  (`agent/agents/recommend.py`). 전에는 후보가 0건이 되어 "조건을 만족하는 후보가 없어
  비교하지 못했습니다"가 떴는데, 실제로는 비교를 끝낸 뒤 우위가 없는 상태였습니다.
  `cheaper`·`more_data` 같은 명시적 맞교환 요청은 그대로 필수 조건으로 남습니다.
- **더 비싼 후보에 "더 저렴합니다"라고 쓰던 리포트 문구**를 막았습니다. 현재 요금제와의 금액
  비교를 `agent/agents/report.py` `_vs_current()`가 코드로 계산해 결론까지 넘깁니다
  (실측 오류: 현재 20,000원 / 후보 23,100원인데 "더 저렴합니다").

### 변경 파일

- 크롤러: `crawler/src/crawl_moyo.py`, `crawler/src/schema.py`,
  `crawler/src/verify_payback_prices.py`(신규), `crawler/src/test_payback_billing.py`(신규)
- 데이터: `data/페이백_청구액_검증.csv`(신규, 정정표만. 통합데이터 CSV는 그대로)
- 서버: `agent/data.py`, `agent/mcda.py`, `agent/agents/profiling.py`,
  `agent/agents/recommend.py`, `agent/agents/report.py`, `backend/plans.py`, `backend/main.py`
- 화면: `frontend/src/types.ts`, `frontend/src/components/ReportScreen.tsx`,
  `frontend/src/components/ResultScreen.tsx`, `frontend/src/index.css`
- 문서: `HANDOFF.md`, `outputs/발표_구성과_시연순서.md`

### 실제 확인 결과 (2026-09-18)

- `python -X utf8 -m unittest test_service_process -q`: **39개 통과**.
  로그의 `추천 실패 (RuntimeError)`는 장애 처리 테스트가 의도적으로 낸 것입니다.
- `crawler/src` 에서 `python -X utf8 -m unittest test_payback_billing -q`: 6개 통과.
- `python -X utf8 -m agent.data`, `-m agent.mcda`, `-m agent.agents.profiling`,
  `-m backend.plans`: self-check 통과. frontend `npm run build` 통과.
- 실제 LLM + 브라우저로 세 시나리오를 돌렸습니다. **아래는 2026-09-18 그날의 실측이며
  추천 정확도나 만족도 검증이 아닙니다.** 촬영 전 반드시 다시 확인하세요.

| 시나리오 | 입력 | 결과 |
|---|---|---|
| A 예산 제한형 | 데이터 20GB 이상, 3만원 이하, 만 30세 | 후보 560건, 상위 5건, 1순위 월 4,400원/20GB, 설명 검증 통과 |
| B 선호 변경형 | A 유지 + `가격 우선` → `데이터 우선` 버튼 | 필수조건·후보 560건 그대로, 상위 5건이 150~200GB로 교체, '신규 진입' 배지 표시 |
| C 현재 상품 비교형 | 현재 월 2만원 / 데이터 100GB, 만 30세 | 판정 **유지**, 카드는 같은 100GB대에서 월 1,400~2,900원 비싼 상품. 상세 리포트에 변화표와 월별 그래프 표시 |

- C에서 "현재보다 유리한 후보"는 0건입니다. 페이백 표시가를 청구액으로 바로잡으면서
  싸 보이던 상품들의 실제 12개월 평균요금이 올라간 결과입니다. 이전 기록의 '전환 우위 2건'은
  잘못된 가격 데이터에 기댄 것이므로 재사용하지 마세요.
- 검증 서버: API `127.0.0.1:8008`, frontend `127.0.0.1:5179`. 기존 8000/5173과 이전 세션의
  8002/5175는 종료하지 않았습니다.

```powershell
# 이 작업 사본에서 API 실행
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8008
# 별도 터미널에서 이 작업 사본의 frontend 폴더로 이동한 뒤
$env:API_TARGET='http://127.0.0.1:8008'
npm run dev -- --host 127.0.0.1 --port 5179 --strictPort
```

### 남은 점검 / 하지 않은 것

1. **최신 CSV 승격은 하지 않았습니다.** `crawler/data/final`과 루트 `data/`를 비교한 결과:
   스키마 42컬럼 동일 / 상품 2,591건 vs 2,759건(468건 소멸, 300건 신규) /
   `discounted_fee` 547건·`monthly_fee` 114건·`discount_period_months` 121건 차이 /
   페이백 표시 99건 vs 84건 / 혜택 표에 `benefit_value_basis`, `benefit_months`,
   `benefit_data_gb` 3개 컬럼 추가(현재 루트 CSV는 옛 스키마라 혜택 월 환산을 이름 문자열로
   추론 중). 승격하면 추천 결과가 크게 바뀝니다 — 팀 확인 후 결정하세요.
2. 상세에 "월 납부액"이 없는 3건(36963, 37443, 37124)은 원문 구조를 다시 봐야 합니다.
   숫자를 추정해 채우지 않았습니다.
3. 페이백 지급 기간이 '평생'이거나 미표기인 항목은 `payback_schedule`에 `5000x?`로 남습니다.
   기간 미상 혜택은 기존 규칙대로 비용에서 차감하지 않습니다.
4. 비교 구간 12개월은 그대로입니다. 발표에서 기간과 '현재 청구액 고정' 가정을 밝혀야 합니다.
5. 전체 재크롤링은 하지 않았습니다. 크롤러의 청구액 수집 코드는 다음 전체 크롤 때 적용됩니다.

---

## 이전: 설명 검증 / 비용 기준 통일 / 혜택 왜곡 / 필수·선호 / 유지 판정 / 비슷한 추천 완화

## 페이백 표시가 점검 및 발표 초안 (2026-09-18, 3e8825e 이후)

- 사용자 요청으로 후속 점검 및 8장 발표 초안을 만들었습니다. 이번 변경은 아직 커밋하지 않았습니다.
- 원문 확인: 모요 36334는 청구액 49,000원 / 페이백 표시가 7,000원, 37403은 41,900원 / 6,900원입니다. 아래 과거 실검증의 2개 추천은 잘못된 가격 데이터에 의존하므로 성과 사례로 재사용하지 마세요.
- 기존 CSV에서 discount_type에 페이백이 있는 84건을 추천에서 제외했습니다. all_plans 탐색에는 남기되 billing_price_known=false를 전달합니다.
- 미확인 청구액에는 총비용/현금성 혜택 차감값을 null로 보내고 UI에 확인 필요를 표시합니다. 알려진 현재 납부액이 있으면 그 값을 우선하고, 없으면 현재 상품 비교 전에 질문합니다. 비용순 정렬에서 미확인 항목은 뒤로 보냅니다.
- 이 작업은 안전한 계산 제외 조치입니다. 상세 청구액과 기간별 가격을 크롤러가 수집하도록 바꾸거나 최신 CSV를 승격한 것은 아닙니다. CSV는 변경하지 않았습니다.
- 검증: unittest 30개, agent.data 및 backend.plans self-check, frontend build 통과. 브라우저 탐색/비교함에서 표시가 7,000원과 청구액 49,000원의 구분 및 계산 제외 확인. 이번에는 새 LLM 요청을 보내지 않았습니다.
- 발표 파일: outputs/모모플랜_멘토피드백_발표초안.pptx (8장), outputs/발표_구성과_시연순서.md. 실제 탐색/비교 화면, EDA, 데이터 가격 구분, 추천 3분류안, 에이전트 입력·출력, 90초 시연 계획을 포함합니다. 수치와 설명 출처는 발표자 노트에 있습니다.
- PPT 구조/레이아웃/편집 가능한 표·차트 검사 및 8장 렌더 확인 완료. PowerPoint 앱에서 직접 연 검증은 하지 않았습니다.
- 실행 화면: http://127.0.0.1:5175/ → API 8002. 원래 main 작업 사본으로 옮기거나 병합하지 않았습니다.

---

## Codex 후속 보완 (2026-09-18)

작업 위치는 `.claude/worktrees/plan-fix`, 브랜치 `fix/plan`, Claude 기준 HEAD `62927b0`입니다.
아래 이전 기록의 서버 상태와 미검증 항목은 이 절을 우선하세요. 이번 변경은 미커밋입니다.

### 추가 수정

- 현재 요금제 유지·전환 판단 및 cheaper 비교에 12개월 평균요금을 사용합니다. 초기 할인가만으로 유리하다고 판정하지 않습니다.
- 데이터/통화 무제한=False만 있고 제공량이 없으면 정보 부족으로 처리합니다. 후보의 비교 항목이나 할인 기간이 불명확하면 우열 판정에서 제외합니다.
- 상품명이 있어도 사용자가 직접 밝힌 현재 납부액·제공량을 카탈로그보다 우선합니다. 현재 납부액은 비교 기간 동안 유지된다고 가정합니다(현재 프로모션 만료 입력은 향후 과제).
- 매월 지급하는 현금성 혜택의 기간이 미확인이면 비용에서 차감하지 않습니다. 명시된 일회성 혜택은 한 번만 계산합니다.
- 유지 안내가 현재 상품의 최적성을 보장하지 않도록 UI/리포트 문구를 수정했습니다.
- 현재 요금제에 대한 일반 비교에서 우위 후보가 있으면 그 후보 안에서 순위를 매깁니다. 후보 수가 적다고 열등한 상품으로 5개를 채우지 않습니다.
- 실검증에서 '데이터 100GB'를 '데이터100G(밀리의서재)+' 상품명으로 오인하는 오류를 발견해, 제공량뿐인 이름 stem의 부분 일치를 제한했습니다.
- '현재 요금제보다 유리한' 요청을 cheaper로 축소하는 오추출을 better로 보정합니다.

### 검증

- `python -X utf8 -m unittest test_service_process -q`: 29개 통과. 예상된 장애 처리 테스트의 RuntimeError 로그는 실패가 아닙니다.
- `python -X utf8 -m agent.data`, `python -X utf8 -m agent.agents.profiling`: self-check 통과.
- frontend `npm run build`, `git diff --check`: 통과.
- 사용자 승인 후 설정된 OpenAI API로 가상 입력을 전송하여 실제 브라우저 검증 완료:
  '지금 월 2만원에 데이터 100GB인 요금제를 쓰고 있어. 만 30세야. 현재 요금제보다 유리한 요금제가 있으면 추천해줘.'
- 수정 전: 전환 우위 2건 안내와 달리 6~10GB 저가 상품 5개 표시.
- 수정 후: 현재 데이터 이상인 120GB/100GB 후보 2개 표시, 12개월 비교 안내와 카드 일치. 이는 현재 CSV와 알려진 비교 항목 기준이며 상품 가격 원문 검증을 뜻하지 않습니다.
- 현재 검증 서버: frontend 5175 → API 8002. API PID 23656. 기존 8000/5173 및 Claude 서버는 종료하지 않았습니다.

```powershell
# 이 작업 사본에서 API 실행
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8002
# 별도 터미널에서 이 작업 사본의 frontend 폴더로 이동한 뒤
$env:API_TARGET='http://127.0.0.1:8002'
npm run dev -- --host 127.0.0.1 --port 5175 --strictPort
```

### 남은 점검

1. 크롤러 CSV의 실제 청구요금/페이백 반영 가격을 원문과 대조해야 합니다. 실검증에서 너겟49가 월 7,000원 + 별도 월 페이백으로 표시되었습니다. 이미 혜택을 뺀 수집가격이면 이중 차감 위험이 있습니다. 숫자를 추정해 수정하지 않았습니다.
2. 아래 원래 기록의 crawler/data/final 승격 전 스키마·혜택 환산 차이 검증은 아직 남아 있습니다. CSV 교체/재크롤링은 하지 않았습니다.
3. 비교 기간 12개월은 Claude 구현을 유지했습니다. 발표에서 기간과 현재 청구액 고정 가정을 명시해야 합니다.
4. keep/undetermined는 자동 API 테스트로 확인했고, 실제 LLM+브라우저는 위 switch 사례를 확인했습니다. 모든 자연어 변형과 추천 정확도를 보장하는 검증은 아닙니다.

---

작업일: 2026-09-18
브랜치: `fix/plan`
기준 커밋: `8f5a18c` + 작업 시작 시점의 미커밋 변경 전부

> 첫 커밋(`chore:`)은 작업 시작 시점의 미커밋 변경(수정 31개 파일 + 미추적 파일)을
> 그대로 담은 것이고, 그 뒤 커밋들이 이번 작업분입니다.
> `git log 8f5a18c..HEAD` 로 이번에 바뀐 것만 볼 수 있습니다.

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

### 후순위 2 — 비슷한 추천 반복 완화

**먼저 점검했습니다.** 기존 `_dedupe_by_name`은 `plan_name` 하나로만 묶어 이름이 같으면 지웠습니다.
실측 결과 그게 지우던 **369행 중 313행이 실제로 다른 상품**이었습니다.

| 동명 그룹이 갈리는 축 | 그룹 수 |
|---|---|
| 포함 혜택 | 101 |
| 가입 조건(age_condition) | 97 |
| 사업자 유형·브랜드 | 38 |
| 망(host_mno) | 30 |
| 데이터 제공량 | 32 |
| 요금만 다름 | 54 |

(전체 2,759행 / 고유 상품명 2,390 / 동명 그룹 200개 569행)
예: `초이스 더블 유튜브 프리미엄+넷플릭스`는 같은 90,000원인데 **혜택이 하나 더 많은 청년 전용
버전이 조용히 지워지고** 있었습니다.

**고친 방식 — 지우는 자리와 고르는 자리를 분리했습니다.**

1. `_dedupe_identical_offers` (후보 정리): 상품명·가입 조건·사업자·망·데이터·통화·포함 혜택까지
   **전부 같은 중복 행만** 1건으로 합칩니다(실납부액이 싼 쪽). 이름이 같아도 조건이 다르면 남깁니다.
   후보에서 없애면 그 상품은 화면에서 아예 볼 수 없기 때문입니다.
2. `_diverse_selection` (상위 5개 선정): **사업자 · 데이터 구간 · 소진 후 등급**이 겹치는 상품이
   자리를 나눠 먹지 않게 고릅니다. 데이터 구간은 `3/10/20/50/100/200GB` 경계입니다.
   - 성격 분류를 **억지로 채우지 않습니다.** 겹치지 않는 후보가 모자라면 미뤄 둔 후보를
     기대순위 순서대로 그냥 채웁니다. '절약형·데이터형·혜택형' 같은 칸은 만들지 않습니다.
   - 같은 상품명이 두 번 나오는 것만은 끝까지 막습니다(코드 검증이 중복 추천으로 잡습니다).
   - 마지막에 기대순위로 다시 정렬해 순위 정합성 검증을 그대로 통과합니다.
3. `ranked`를 바꾸므로 **카드와 리포트가 같은 목록을 씁니다.** 카드 수만 달라지는 일은 없습니다.
   화면의 "어떻게 이 결과가 나왔나요?"에 선정 기준 문구(`trace.diversification`)를 함께 내려보냅니다.

**실제 결과** (`월 3만원 이하 20GB 이상`, 후보 578건)

| | 1 | 2 | 3 | 4 | 5 | 서로 다른 성격 |
|---|---|---|---|---|---|---|
| 전 | 너겟49 (LG U+) | 더든든한500분20G (프리티) | (5G)더든든한200분20G (**프리티**) | (5G)하나은행 500분20G (**프리티**) | [K]울트라 (아이즈) | **3/5**, 사업자 3종 |
| 후 | 너겟49 (LG U+) | 더든든한500분20G (프리티) | [K]울트라 (아이즈) | 가성비플러스 (티플러스) | 핀다이렉트 Speed (핀다이렉트) | **5/5**, 사업자 5종 |

다른 요청에서도 확인했습니다. `3만원 이하 + 데이터 우선`은 4/5 → 5/5(찬스모바일 중복 제거),
`무제한 5만원 이하`는 이미 5/5라 **결과가 바뀌지 않았습니다**(억지로 손대지 않습니다).

**이 과정에서 드러난 회귀 2건도 고쳤습니다.** 둘 다 이번 작업에서 제가 만든 것입니다.
- `sms_count`가 `SLIM_FIELDS`에 없어, 리포트가 적은 `문자 300건`을 평가가 "데이터에 없는 기능"으로
  잡았습니다(테더링과 같은 종류의 비대칭). 추가했습니다.
- 요금 조건 줄의 `월 7,000원 (할인 없음)` 문구가 혜택 쪽 프로모션(`모요 프로모션 페이백/할인`)과
  어긋나는 말로 읽혀 평가가 떨어뜨렸습니다. `월 7,000원 · 정상가와 동일`로 바꿨습니다.
- 두 건을 고친 뒤 재현 입력은 다시 `attempt=1`, `passed=True`입니다(고치기 전 `attempt=3`).

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
agent/agents/profiling.py             PROFILING_PROMPT 예산 하한·필수/선호·현재요금 규칙,
                                      _repair_budget_bounds 하한 제거, _BUDGET_MIN_RE,
                                      _apply_benefit_constraint_strength, _apply_soft_data_preference,
                                      _apply_reference_fee, _drop_placeholder_text,
                                      _POST_NORMALIZE_REPAIRS (정규화가 선호 완화를 되돌리던 문제)
agent/agents/recommend.py             _reference_verdict / _known_reference_axes /
                                      REFERENCE_CONFIRM_NOTES, 세 분기에서 reference_verdict 반환,
                                      _dedupe_by_name -> _dedupe_identical_offers(_offer_key),
                                      _diverse_selection / _offer_character / _data_band,
                                      trace 에 diversification 추가
agent/state.py                        PipelineState 에 reference_verdict 추가
backend/main.py                       응답에 referenceVerdict 추가
backend/plans.py                      six_month_cost -> total_cost, COMPARE_MONTHS 를 mcda 에서 import,
                                      effectiveTotal 을 benefit_deductible_won 기준으로,
                                      benefitDeductible/benefitValueEstimated/benefitConditionalCount 추가
test_service_process.py               import 변경 + 테스트 10개 추가
frontend/src/types.ts                 PlanItem 에 필드 3개 추가, ReferenceVerdict 타입 추가
frontend/src/components/BrowseScreen.tsx        '6개월' 하드코딩 제거, 차감 표시 기준 변경
frontend/src/components/HomeScreen.tsx          폴백 6 -> 12
frontend/src/components/RecommendationTrace.tsx 폴백 6 -> 12, 4번 절 문구 재작성, 선정 기준 문구 표시
frontend/src/components/ReportScreen.tsx        혜택 블록 재작성(추정·조건 안내)
frontend/src/components/ResultScreen.tsx        차감 표시 기준·라벨 변경, 현재 요금제 판정 배너 추가
HANDOFF.md                            (신규)
```

**데이터 파일은 변경하지 않았습니다.** 루트 `data/`의 CSV 2개는 그대로입니다.

---

## 3. 실행한 테스트와 결과

모두 저장소 루트에서 `PYTHONPATH=.` 로 실행했습니다.

| 명령 | 결과 |
|---|---|
| `python -B -m unittest test_service_process` | **OK — 22개** (기존 12 + 신규 10) |
| `python -m unittest discover -s crawler/src -p "test_*.py"` | **OK — 34개** |
| `python backend/plans.py` | `self-check ok: 2759 plans` |
| `python -m agent.data` | `self-check ok: 1555 candidates` |
| `python -m agent.mcda` | `self-check ok: SMAA-2 ranking (bootstrap weights)` |
| `python -m agent.agents.profiling` / `report` / `evaluation` | 각 `self-check ok` |
| `python -m agent.usage` | `self-check ok` |
| `cd frontend && npm run build` | `tsc --noEmit` 통과, `✓ built in 2.44s` |

**신규 테스트 10개** (`test_service_process.py`)
- `test_compare_period_is_single_source_of_truth` — `compareMonths == rankingMonths == BENEFIT_AMORTIZE_MONTHS`
- `test_benefit_is_not_deducted_without_confirmed_usage` — 구독형 혜택은 총비용에서 빠지지 않는다
- `test_benefit_axis_is_neutral_when_not_requested` — 혜택 미요청 시 혜택 축이 전 후보 0.5
- `test_preferred_benefit_does_not_remove_candidates` — 선호 혜택은 후보를 지우지 않는다(알뜰폰 포함)
- `test_keep_current_plan_is_distinguished_from_cannot_tell` — 유지/전환/판단불가 3상태, 후보 0건은 유지가 아니다
- `test_current_fee_is_not_turned_into_a_budget_cap` — 현재 납부액이 예산 상한이 되지 않는다
- `test_recommend_exposes_reference_verdict_through_api` — API 응답에 referenceVerdict 가 나간다
- `test_same_name_different_offer_is_not_deleted` — 이름이 같아도 조건이 다르면 지우지 않는다
- `test_top5_does_not_repeat_the_same_kind_of_plan` — 상위 5개의 성격이 겹치지 않는다(회귀 지점 포함)
- `test_diversity_never_invents_choices_it_does_not_have` — 겹치는 후보뿐이면 억지로 만들지 않는다

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
  **화면 처리도 코드로 확인했습니다.** `ResultScreen`은 `plans.length === 0 && !needsMoreInput`일 때
  `EmptyResult`를 띄우고, `blockers`를 한 줄씩 풀어 보여줍니다 —
  `"<조건 이름>" 조건을 빼면 N건 · 이 조건들로는 월 59,000원부터 가능합니다`.
  각 줄에는 그 조건을 푸는 문장(`예산을 59,000원까지 올릴게요`)을 바로 상담에 보내는 버튼이 붙어 있습니다.
  `blockers`가 비었을 때(조건 두 개 이상이 동시에 걸린 경우)는 조건을 풀어 달라는 안내만 나옵니다.
  즉 "없습니다"로 끝나지 않습니다. 추가 작업이 필요 없어 남은 항목에서 뺐습니다.
  (`frontend/src/components/ResultScreen.tsx`의 `EmptyResult`)

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

**해결.** 유지 권고와 판단 불가를 코드로 구분합니다(`agent/agents/recommend.py:_reference_verdict`).
LLM 판정이 아니라 코드 판정이고, 리포트는 그 결과를 따르기만 합니다.

| status | 조건 | 뜻 |
|---|---|---|
| `keep` | 현재 요금제를 **모든** 비교 항목에서 앞서는 후보(파레토 우위)가 없다 | 유지가 낫다 |
| `switch` | 파레토 우위 후보가 있다 | 바꿀 만하다 |
| `undetermined` | 현재 요금·데이터 중 모르는 게 있거나, 후보가 0건이다 | **판정하지 않는다** |

요금만 알고 데이터를 모르면 `undetermined`입니다. 그 상태에서 "더 싼 게 있다"는 말은
무엇을 포기하는지 빼고 한 말이라 유불리 근거가 못 됩니다.
후보 0건도 `keep`이 아니라 `undetermined`이고, 문구에 "후보가 없다는 것이 현재 요금제가
유리하다는 뜻은 아닙니다"를 넣었습니다.
어느 상태든 `confirm`(실제 납부액·결합할인·위약금)을 함께 내려보내 `### 가입 전 확인`에 들어갑니다.

함께 고친 것 두 가지 — 둘 다 이 시나리오를 막고 있던 별개 버그입니다.

1. **현재 납부액이 예산 상한으로 둔갑** (`_apply_reference_fee`)
   `지금 월 3만원인데`가 `budget_max_won=30000`이 되면 지금보다 싼 상품만 후보가 되어,
   "유지가 낫다"는 답 자체가 나올 수 없습니다. 발화의 금액이 현재 요금 하나뿐이고
   경계 표현(`이하`/`N만원대`)이 없으면 프로필에 붙은 예산을 지웁니다.
   금액을 둘 말했으면(`지금 3만원 내는데 2만원짜리 있어?`) 손대지 않습니다.
2. **`_normalize_profile`이 선호 완화를 되돌리던 문제** (우선순위 4의 실제 회귀)
   `_normalize_profile`은 `hard_constraints`를 **값 유무로 다시 만듭니다.** 그래서
   `_apply_benefit_constraint_strength`가 앞에서 `wanted_benefits`를 빼도 곧바로 되돌아왔습니다.
   단위 테스트는 통과했는데 노드 전체로는 동작하지 않던 상태였습니다.
   이 보정만 `_POST_NORMALIZE_REPAIRS`로 옮겨 정규화 뒤에 적용합니다.
3. **구조화 출력의 `"null"` 문자열** (`_drop_placeholder_text`)
   LLM이 `reference_plan_name`에 문자열 `"null"`을 넣었고, 그 이름으로 DB를 뒤지다
   "정확한 요금제명을 알려주세요"로 파이프라인이 통째로 멈췄습니다(후보 0건, 평가 미실행).
   문자열 필드의 자리표시자(`null`/`none`/`없음`/`N/A` 등)를 진짜 `None`으로 바꿉니다.

**실제 결과** (LLM 실호출)

| 입력 | 예산 | verdict | 후보 |
|---|---|---|---|
| `지금 쓰는 요금제가 월 3만원인데 바꾸는 게 나을까?` | 상한 없음 | `undetermined` · missing=`['데이터 제공량']` | 1,906 |
| `지금 월 2만원에 데이터 100GB 쓰고 있어. 바꾸는 게 나을까?` | 상한 없음 | `switch` · better 121 / cheaper 118 | 677 |
| `지금 월 7만원에 데이터 50GB 쓰는데 바꾸는 게 나을까?` | 상한 없음 | `switch` · better 851 / cheaper 2,390 | 2,390 |
| `지금 월 5천원에 데이터 무제한에 통화도 무제한으로 쓰고 있어.` | 상한 없음 | `keep` · better 0 / cheaper 38 | 1,397 |

- 첫 번째 리포트: `현재 요금제의 데이터 제공량을 알 수 없어 지금이 유리한지 판단하지 못했습니다.`
  (수정 전에는 데이터를 모르는 채 `현재 요금제보다 더 저렴하면서도 데이터와 통화 혜택이 우수한`이라고 단정했습니다)
- 네 번째 리포트: `현재 요금제를 유지하는 것이 더 나을 것으로 보입니다.` + 확인 항목 3줄
- 수정 전 첫 번째 입력은 `budget_max_won=30000`이 붙고 `reference_plan_name="null"`로 막혀
  추천 자체가 나오지 않았습니다.

**선호/필수 재확인** (2번 항목을 고친 뒤 다시 측정)

| 입력 | hard_constraints | 후보 | 그중 넷플릭스 포함 |
|---|---|---|---|
| `넷플릭스 포함이면 좋겠어` (3만원 이하 20GB 이상) | `budget_max_won`, `min_data_gb` | 578 | 0 |
| `넷플릭스 포함 요금제만 원해` (7만원 이하) | `budget_max_won`, **`wanted_benefits`** | 15 | 15 |

선호는 후보를 지우지 않고, 리포트가 `넷플릭스 혜택은 포함되어 있지 않으니 참고하시기 바랍니다`로
못 맞췄다는 사실을 알립니다. 필수는 15건 전부 넷플릭스 포함입니다.

### 시나리오 8 — 담기 → 비교 → 새로고침

**브라우저로 직접 확인했습니다.** (Chrome, 1440x1000)

검증용 스택을 따로 띄웠습니다. 8000/5173은 이미 다른 서버가 쓰고 있었고
그쪽은 구 코드(`compareMonths: 6`)라 결과가 섞이면 안 되므로 **8001/5174**에 별도로 올렸습니다.
`frontend/vite.config.ts`의 프록시 대상을 8001로 잠시 바꿨다가 **확인 후 원복**했습니다
(커밋에 들어 있지 않습니다). 확인이 끝난 뒤 8001/5174만 종료했고 기존 8000/5173은 그대로 뒀습니다.

| 단계 | 결과 |
|---|---|
| 전체 요금제 화면 | 전체 2,759건 · 통신 3사 556 / 알뜰폰 2,203 / 브랜드 30, 가격·데이터·통신망 분포 모두 표시 |
| 표 헤더 | `12개월 총비용`, `현금성 혜택 차감 참고값` (6개월 하드코딩 사라짐 확인) |
| 탐색 상품 담기 | 체크박스 2건 → `내 비교함 (2)`, 저장된 항목 `rank: 0`, `compareMonths: 12` |
| AI 추천 (실호출) | 후보 578건 → 추천 5건. 사업자 5종(LG U+ · 프리티 · 아이즈모바일 · 티플러스 · 핀다이렉트)으로 분산 |
| 추천 요약 | `추천 후보 중 12개월 총비용이 가장 낮은 상품은 더든든한500분20G (52,800원)` |
| 추출 프로필 패널 | `희망 월 예산 30,000원 이하` (예산 하한이 생기지 않음), `연령대 만 30세` |
| 추천 상품 담기 | 1순위·3순위 담기 → `내 비교함 (4)`, `rank: 1` / `rank: 3`으로 구분 저장 |
| 내 비교함 | 탐색 2 + 추천 2가 한 표에 나란히, 우측 `12개월 기준 · 프로모션 가격 반영` |
| **새로고침(F5)** | `#/compare` 유지, 비교함 4건 그대로, 대화 2건·추천 결과 5건 복원, `pending: false` |
| 새로고침 후 상담 화면 | 사용자 발화와 답변, 추출 프로필(`1차 후보 선별 완료`) 모두 복원 |
| 상세 리포트 | `12개월`만 등장, `6개월` 표기 없음. 혜택 안내가 새 문구로 표시 — <br>`혜택 월 환산 가치는 납부액 할인이 아닙니다. 해당 서비스를 실제 이용하고 직접 결제 중일 때만 절약이 되므로…`, `차감 참고값에는 조건 없는 현금성 혜택 월 25,000원만 반영했습니다.` |
| 비교함에서 삭제 | 1건 삭제 → `내 비교함 (3)`, 표에서도 사라짐 |
| 콘솔 | 오류·예외 없음 |

기존 서비스 기능(담기·비교·삭제·새로고침 복원)은 깨지지 않았습니다.

> 위 결과는 과정의 정합성 확인입니다. 추천 정확도나 사용자 만족도 수치가 아닙니다.
> 비교 실험을 하지 않았으므로 "추천이 좋아졌다"고 말할 수 없습니다.

---

## 5. 아직 남은 것 / 재현 방법

1. **리포트가 여전히 카드 스펙을 한 번씩 반복한다.**
   프롬프트에 금지 규칙과 좋은 예/나쁜 예를 넣어 많이 줄었지만 `gpt-4o-mini`가 완전히 따르지는 않습니다
   (`"120GB의 데이터와 무제한 음성 및 SMS를 제공합니다"` 같은 문장이 남습니다).
   정확성 문제는 아닙니다. 더 줄이려면 `MODEL`을 올리거나 예시를 몇 개 더 붙이는 쪽입니다.
   후처리로 문장을 지우는 방식은 넣지 않았습니다(멀쩡한 비교 문장까지 지울 위험).
2. **리포트 단계가 가끔 재시도를 한두 번 태운다.** 검증은 최종적으로 통과하지만
   `gpt-4o-mini`가 후보 데이터에 없는 표현을 쓰면 평가가 되돌립니다(요청당 10~20초 추가).
   재현: `월 3만원 이하 20GB 이상으로 추천해줘. 넷플릭스 포함이면 좋겠어.` (attempt 2~3 관측)


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
3. 현재 요금제 판정 배너(`ResultScreen`의 `referenceVerdict`)는 화면으로 확인하지 못했습니다.
   기준 요금제를 말한 입력(`지금 월 2만원에 100GB 쓰고 있어`)으로 한 번 띄워 보세요.
