export type ScreenType = 's-home' | 's-input' | 's-result' | 's-report' | 's-browse' | 's-compare' | 's-detail';

/** backend/plans.py 의 to_plan_item 산출물과 1:1. 바꾸려면 양쪽 같이 바꾼다. */
export interface PlanItem {
  id: string;
  rank: number;
  best: boolean;
  name: string;
  carrier: string;
  carrierType: 'MNO' | 'MVNO';
  network: string;
  networkGen: string;
  price: string;
  priceNum: number;
  originalPrice: number;
  priceNote: string;
  billingPriceKnown: boolean;
  score: number;
  reason: string;
  data: string;
  dataNum: number | null;
  dataUnlimited: boolean;
  /** 기본량을 다 쓴 뒤 무엇이 되는지. 'unlimited_full' | 'qos_hd' | ... | 'capped' */
  dataTier: string;
  dataTierLabel: string;
  /** 완전 무제한 + 소진 후에도 쓸 만한 QoS형. 사용자가 말하는 '무제한'의 범위. */
  effectiveUnlimited: boolean;
  dailyDataGb: number | null;
  qos: string;
  /** false 면 qos 는 '없음'이 아니라 '미수집'이다. */
  qosKnown: boolean;
  call: string;
  sms: string;
  tethering: string;
  tetheringGb: number | null;
  hash: string[];
  benefit: string;
  /** 수집된 혜택의 월 환산 원화 가치. 0 은 '혜택 없음'이 아니라 '금액 미확인'. */
  benefitValue: number;
  /** 납부 총액에서 실제로 빼도 되는 부분(조건 없는 현금성 혜택)만. */
  benefitDeductible: number;
  /** 제공 기간이 확인되지 않은 혜택이 섞여 있으면 월 환산액은 추정이다. */
  benefitValueEstimated: boolean;
  /** 카드 실적·별도 가입 조건이 붙어 자동 차감하지 않은 혜택 수. */
  benefitConditionalCount: number;
  /** 축별 충족도 0~1. price/data/qos/benefit/voice/tethering */
  criteriaFit: Record<string, number>;
  expectedRank: number | null;
  firstRankAcceptability: number | null;
  /** 현재 조건에 대한 다기준 적합도(0~100). 만족 확률·가입 성공 확률이 아니다. */
  recommendationFit: number | null;
  /** 가중치 300세트 중 상위 3위 안에 든 비율(0~1). */
  top3Acceptability: number | null;
  rankingMonths: number;
  rankingAverageFee: number | null;
  costIsEstimate: boolean;
  dataWarnings: string[];
  signupNotice: string;
  total: string;
  totalNum: number | null;
  effectiveTotal: string;
  effectiveTotalNum: number | null;
  /** 혜택 금액이 요금을 넘은 경우. 실부담은 0 원에서 끊고 조건 확인을 안내한다. */
  benefitExceedsFee: boolean;
  compareMonths: number;
  promoMonths: number;
  isPromo: boolean;
  /** 할인이 비교 구간 안에 끝나는 경우의 개월 수. null 이면 구간 내내 같은 가격. */
  priceRisesAfter: number | null;
  /** 비교 구간 밖에서 정가로 오르는 경우. 총비용만 보면 안 보인다. */
  priceRisesLater: boolean;
  promoDiscountRate: number;
  isOnlineOnly: boolean;
  hasAddon: boolean;
  ageCondition: string;
  sourceUrl: string;
  /** 현재 쓰는 요금제가 있을 때만 채워진다. 상품명 없이 납부액만 말한 경우도 포함. */
  referenceDelta: ReferenceDelta | null;
}

/** 현재 요금제 대비 변화. 금액 계산은 전부 서버(backend/plans.reference_delta)에서 한다.
 *  모르는 값은 0 이 아니라 null 이고, 화면은 null 을 '확인 필요'로 보여준다. */
export interface ReferenceDelta {
  months: number;
  currentMonthlyFee: number | null;
  candidateMonthlyFee: number | null;
  monthlyDiff: number | null;
  currentTotal: number | null;
  candidateTotal: number | null;
  /** 요금만 비교한 차이다. 위약금·결합할인 손실은 빠져 있어 '확정 절약액'이 아니다. */
  totalDiff: number | null;
  discountEndsAfterMonths: number | null;
  feeAfterDiscount: number | null;
  currentData: string;
  candidateData: string;
  dataDiffGb: number | null;
  currentQos: string;
  candidateQos: string;
  schedule: { month: number; current: number | null; candidate: number | null }[];
  unknowns: string[];
  assumption: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

/** profiling 단계가 뽑아낸 조건. 화면은 이 중 일부만 보여준다. */
export interface Profile {
  budget_max_won?: number;
  budget_min_won?: number;
  min_data_gb?: number;
  target_data_gb?: number;
  max_data_gb?: number;
  data_unlimited?: boolean;
  min_voice_minutes?: number;
  voice_unlimited?: boolean;
  sms_unlimited?: boolean;
  age_condition?: string;
  user_age?: number;
  priorities?: string[];
  host_mno?: string;
  carrier_type?: string;
  mvno_brand?: string;
  network_gen?: string;
  network_preference?: string;
  require_full_unlimited?: boolean;
  min_qos_mbps?: number;
  requires_qos?: boolean;
  min_tethering_gb?: number;
  min_discount_period_months?: number;
  wanted_benefits?: string[];
  wanted_benefit_categories?: string[];
  benefit_match_mode?: 'all' | 'any';
  estimated_monthly_data_gb?: number;
  usage_estimate_notes?: string[];
  smartchoice_usage_pattern?: string;
  app_usages?: { service: string; daily_hours: number; mode?: string | null }[];
  assumptions?: string[];
  [key: string]: unknown;
}

/** 후보를 0건으로 만든 조건 하나. agent.data.diagnose_empty 산출물과 1:1. */
export interface Blocker {
  field: string;
  label: string;
  value: unknown;
  candidates: number;
  minimum_fee?: number;
}

export interface ReferenceFacts {
  discounted_fee?: number | null;
  data_gb?: number | null;
  data_unlimited?: boolean | null;
  voice_minutes?: number | null;
  voice_unlimited?: boolean | null;
  qos_mbps?: number | null;
  plan_name?: string | null;
}

export interface ReferenceVerdict {
  status: 'keep' | 'switch' | 'undetermined';
  reason: string;
  /** 판단하지 못한 이유가 된, 모르는 항목들. */
  missing: string[];
  /** 요금제 데이터로는 알 수 없어 사용자가 직접 확인해야 하는 것들. */
  confirm: string[];
  betterCount?: number;
  cheaperCount?: number;
}

export interface RecommendResponse {
  plans: PlanItem[];
  /** 데이터·요금을 둘 다 못 잡아 추천 전에 멈춘 경우. 결과 대신 질문을 보여준다. */
  needsMoreInput: boolean;
  candidateCount: number;
  totalCount: number;
  report: string;
  referencePlan: PlanItem | null;
  /** 상품명을 몰라도 사용자가 말한 현재 납부액·데이터량만으로 비교할 때 쓰는 원본 값. */
  referenceFacts: ReferenceFacts | null;
  /** 현재 요금제 유지/전환/판단불가. 서버가 코드로 판정한 값이며 LLM 판정이 아니다. */
  referenceVerdict: ReferenceVerdict | null;
  profile: Profile | null;
  followupQuestion: string | null;
  assumptions: string[];
  /** 후보가 0건일 때, 어느 조건을 풀면 몇 건이 살아나는지. */
  blockers: Blocker[];
  dataAsOf: string;
  /** '무제한'을 어느 범위로 봤는지. 상수는 서버(agent/data.py)에만 두고 화면은 받아 쓴다. */
  unlimitedPolicy: { minGb: number; qosMbps: number };
  evaluation: { passed: boolean; feedback: string; retry_target: string } | null;
  trace: {
    eligibleCount?: number;
    rankedCount?: number;
    shownCount?: number;
    rankingProfile?: Profile;
    referenceBaselineApplied?: boolean;
    deduplication?: string;
    /** 상위 3개를 어떤 기준으로 겹치지 않게 골랐는지. */
    diversification?: string;
    elapsedSeconds: number;
    evaluationAttempts: number;
  };
}

/** 필터 그룹 → 항목별 건수. 전체 데이터 기준 고정값. */
export type Facets = Record<string, Record<string, number>>;

export interface Stats {
  total: number;
  mvno: number;
  mno: number;
  brands: number;
  compareMonths: number;
  dataAsOf: string;
  facets: Facets;
}

export interface PlanPage {
  total: number;
  page: number;
  pageSize: number;
  plans: PlanItem[];
}

/** 탐색 화면의 체크박스 상태. 그룹 안에서는 OR, 그룹 사이에서는 AND. */
export interface BrowseFilters {
  networks: string[];
  price: string[];
  data: string[];
  tier: string[];
  gen: string[];
  voice: string[];
  flags: string[];
}

export const EMPTY_FILTERS: BrowseFilters = {
  networks: [],
  price: [],
  data: [],
  tier: [],
  gen: [],
  voice: [],
  flags: [],
};

/** 재추천 때마다 한 줄씩 쌓이는 변경 이력. */
export interface HistoryRow {
  time: string;
  change: string;
  before: string;
  after: string;
  result: string;
}
