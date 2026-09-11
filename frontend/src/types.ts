export type ScreenType = 's-home' | 's-input' | 's-result' | 's-report' | 's-browse';

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
  score: number;
  reason: string;
  data: string;
  dataNum: number | null;
  dataUnlimited: boolean;
  qos: string;
  call: string;
  sms: string;
  tetheringGb: number | null;
  hash: string[];
  benefit: string;
  total: string;
  totalNum: number;
  compareMonths: number;
  promoMonths: number;
  isPromo: boolean;
  /** 할인이 비교 구간 안에 끝나는 경우의 개월 수. null 이면 구간 내내 같은 가격. */
  priceRisesAfter: number | null;
  isOnlineOnly: boolean;
  hasAddon: boolean;
  ageCondition: string;
  sourceUrl: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

/** profiling 단계가 뽑아낸 조건. 화면은 이 중 일부만 보여준다. */
export interface Profile {
  budget_max_won?: number;
  min_data_gb?: number;
  data_unlimited?: boolean;
  min_voice_minutes?: number;
  voice_unlimited?: boolean;
  sms_unlimited?: boolean;
  age_condition?: string;
  host_mno?: string;
  carrier_type?: string;
  estimated_monthly_data_gb?: number;
  usage_estimate_notes?: string[];
  smartchoice_usage_pattern?: string;
  app_usages?: { service: string; daily_hours: number; mode?: string | null }[];
  assumptions?: string[];
  [key: string]: unknown;
}

export interface RecommendResponse {
  plans: PlanItem[];
  /** 데이터·요금을 둘 다 못 잡아 추천 전에 멈춘 경우. 결과 대신 질문을 보여준다. */
  needsMoreInput: boolean;
  candidateCount: number;
  totalCount: number;
  report: string;
  referencePlan: PlanItem | null;
  profile: Profile | null;
  followupQuestion: string | null;
  assumptions: string[];
  evaluation: { passed: boolean; feedback: string; retry_target: string } | null;
}

/** 필터 그룹 → 항목별 건수. 전체 데이터 기준 고정값. */
export type Facets = Record<string, Record<string, number>>;

export interface Stats {
  total: number;
  mvno: number;
  mno: number;
  brands: number;
  compareMonths: number;
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
  data: string[];
  voice: string[];
  flags: string[];
}

/** 재추천 때마다 한 줄씩 쌓이는 변경 이력. */
export interface HistoryRow {
  time: string;
  change: string;
  before: string;
  after: string;
  result: string;
}
