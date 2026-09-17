import {
  BrowseFilters,
  ChatMessage,
  PlanItem,
  PlanPage,
  RecommendResponse,
  Stats,
} from './types';

// LLM 4단계라 40초 안팎. 넉넉히 잡고 그 전까지는 로딩을 유지한다.
const TIMEOUT_MS = 120_000;

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      throw new Error(typeof detail?.detail === 'string' ? detail.detail : `입력 내용을 확인해 주세요 (${res.status})`);
    }
    return res.json();
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error('응답이 2분을 넘었습니다. 조건을 줄이고 다시 시도해 주세요.');
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export function recommend(messages: ChatMessage[]) {
  return call<RecommendResponse>('/api/recommend', {
    method: 'POST',
    body: JSON.stringify({ messages }),
  });
}

export function fetchStats() {
  return call<Stats>('/api/stats');
}

export function listPlans(params: {
  q?: string;
  filters: BrowseFilters;
  sort: string;
  page: number;
  pageSize: number;
}) {
  const query = new URLSearchParams({
    sort: params.sort,
    page: String(params.page),
    page_size: String(params.pageSize),
  });
  // 필터 그룹 이름은 백엔드 FILTER_GROUPS 의 키와 1:1. 한쪽만 늘리면 조용히 무시된다.
  for (const [group, keys] of Object.entries(params.filters)) {
    if (keys.length > 0) query.set(group, keys.join(','));
  }
  if (params.q) query.set('q', params.q);
  return call<PlanPage>(`/api/plans?${query}`);
}

export function fetchPlan(planId: string) {
  return call<PlanItem>(`/api/plans/${encodeURIComponent(planId)}`);
}

export function ask(planId: string, question: string) {
  return call<{ answer: string }>('/api/ask', {
    method: 'POST',
    body: JSON.stringify({ planId, question }),
  });
}
