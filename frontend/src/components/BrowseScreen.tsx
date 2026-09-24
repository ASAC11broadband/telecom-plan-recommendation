import { BrandLogo } from './BrandLogo';
import { categories, categoryFilters } from '../categories';
import { PlanTile } from './PlanTile';
import { useEffect, useState, ReactNode } from 'react';
import { BrowseFilters, BrowseState, EMPTY_FILTERS, Facets, PlanItem, PlanPage, Stats } from '../types';
import { fetchStats, listPlans } from '../api';

const PRICE_OPTIONS: [string, string][] = [['lt10k','1만원 미만'],['10to20k','1~2만원'],['20to30k','2~3만원'],['30to50k','3~5만원'],['50to70k','5~7만원'],['gte70k','7만원 이상']];
const TIER_OPTIONS: [string, string][] = [
  ['unlimited_full', '기본량 무제한'],
  ['qos_hd', '5Mbps'],
  ['qos_sd', '3Mbps'],
  ['qos_lite', '1Mbps'],
  ['qos_text', '400Kbps'],
  ['capped', 'QoS 없음'],
];
const TIER_LABELS = Object.fromEntries(TIER_OPTIONS);
const GROUPS: { group: keyof BrowseFilters; label: string; options: [string, string][] }[] = [
  { group: 'price', label: '월 예산', options: PRICE_OPTIONS },
  {
    group: 'networks',
    label: '통신망',
    options: [
      ['SKT', 'SKT'],
      ['KT', 'KT'],
      ['LGU+', 'LG U+'],
      ['MNO', 'MNO 직영'],
    ],
  },
  {
    group: 'gen',
    label: '네트워크',
    options: [
      ['5G', '5G'],
      ['LTE', 'LTE'],
    ],
  },
  {
    group: 'data',
    label: '기본 데이터',
    options: [
      ['lt10', '0~10GB 미만'],
      ['10to30', '10~30GB 미만'],
      ['30to50', '30~50GB 미만'],
      ['50to100', '50~100GB 미만'],
      ['gte100', '100GB 이상'],
      // 아래 '다 쓴 뒤에는' 그룹과 같은 말을 쓴다. 그냥 '무제한'이라고 두면 AI 추천이 말하는
      // '무제한'(기본량 무제한 + 대용량·속도 유지 상품)과 같은 단어인데 건수가 달라 보인다.
      ['unlimited', '기본량 무제한'],
    ],
  },
  {
    // 기본량보다 이쪽이 체감을 가른다. '무제한'(소진 후 100Kbps)보다 '100GB+5Mbps'가 빠르다.
    group: 'tier',
    label: '다 쓴 뒤에는',
    options: TIER_OPTIONS,
  },
  {
    group: 'voice',
    label: '음성통화',
    options: [
      ['unlimited', '무제한'],
      ['quota', '기본 제공량 있음'],
      ['none', '제공 없음'],
    ],
  },
];

const SORTS: [string, string][] = [
  ['fee_asc', '월 요금 낮은순'],
  ['data_desc', '데이터 많은순'],
  ['qos_desc', '소진 후 속도 빠른순'],
];

const EMPTY = EMPTY_FILTERS;
const PAGE_SIZE = 20;

const labelOf = (group: keyof BrowseFilters, key: string) =>
  GROUPS.find((g) => g.group === group)?.options.find(([k]) => k === key)?.[1] ?? key;

export function BrowseScreen({
  chat,
  state,
  onStateChange,
  compare,
  onToggleCompare,
  onOpenCompare,
}: {
  chat: ReactNode;
  state: BrowseState;
  onStateChange: (state: BrowseState) => void;
  compare: PlanItem[];
  onToggleCompare: (plan: PlanItem) => void;
  onOpenCompare: () => void;
}) {
  const { category, view, filters, sort, q, page } = state;
  const updateState = (patch: Partial<BrowseState>) => onStateChange({ ...state, ...patch });
  const [stats, setStats] = useState<Stats | null>(null);
  const facets: Facets = stats?.facets ?? {};
  const [data, setData] = useState<PlanPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    listPlans({ q, filters, sort, page, pageSize: PAGE_SIZE })
      .then((res) => live && (setData(res), setError(null)))
      .catch((err) => live && setError(err instanceof Error ? err.message : '조회 실패'))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [q, filters, sort, page]);

  const toggle = (group: keyof BrowseFilters, key: string) => {
    updateState({
      page: 1,
      category: group === 'price' ? category : 'custom',
      filters: {
        ...filters,
        [group]: filters[group].includes(key)
          ? filters[group].filter((k) => k !== key)
          : [...filters[group], key],
      },
    });
  };

  const active = GROUPS.flatMap((g) =>
    filters[g.group].map((key) => ({ group: g.group, key, label: labelOf(g.group, key) }))
  );
  const total = data?.total ?? 0;
  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const to = Math.min(page * PAGE_SIZE, total);

  return (
    <div className="body browse-workspace">
      <details className="card filter-panel" open><summary>세부 필터 · 월 예산 / 데이터</summary>
        <div className="panel-head">
          <strong style={{ fontSize: 'var(--fs-13)' }}>필터</strong>
          <button className="text-btn"
            style={{ fontSize: 'var(--fs-11)', color: 'var(--accent)', cursor: 'pointer' }}
            onClick={() => {
              onStateChange({ category: 'all', view, filters: EMPTY, sort, q: '', page: 1 });
            }}
          >
            초기화
          </button>
        </div>
        <div className="f-sec">
          <span className="lbl">요금제명 검색</span>
          <input
            type="text"
            value={q}
            onChange={(e) => {
              updateState({ page: 1, q: e.target.value });
            }}
            placeholder="예: 다이렉트"
          />
        </div>
        {GROUPS.map((g, i) => (
          <div className="f-sec" key={g.group} style={i === GROUPS.length - 1 ? { borderBottom: 'none' } : undefined}>
            <span className="lbl">{g.label}</span>{g.group === 'price' && <p className="filter-hint">할인 적용 월 요금 · 시작 금액 이상 / 끝 금액 미만</p>}
            {g.options.map(([key, label]) => (
              <label className="f-row" key={key}>
                <input
                  type="checkbox"
                  checked={filters[g.group].includes(key)}
                  onChange={() => toggle(g.group, key)}
                />
                <span className="name">{label}</span>
                <span className="cnt num">{(facets[g.group]?.[key] ?? 0).toLocaleString()}</span>
              </label>
            ))}
          </div>
        ))}
      </details>

      <div style={{ flex: 1, minWidth: 0 }}>
        <h1 className="browse-title">내 생활에 맞는 요금제 찾기</h1>
        <div className="browse-categories">{categories.map(c => <button className={'btn '+(category === c.id ? 'selected' : '')} aria-pressed={category === c.id} key={c.id} onClick={() => updateState({ category: c.id, filters: {...categoryFilters(c.id), price: filters.price}, page: 1 })}>{c.icon} {c.title}</button>)}</div>

        <div className="view-switch"><button className="btn" aria-pressed={view === 'cards'} onClick={() => updateState({ view: 'cards' })}>카드 보기</button><button className="btn" aria-pressed={view === 'table'} onClick={() => updateState({ view: 'table' })}>표 보기</button></div>
        <div className="row-between" style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <strong style={{ fontSize: 18 }} className="num">
              {total.toLocaleString()}
            </strong>
            <span style={{ fontSize: 'var(--fs-12)', color: 'var(--t2)' }}>건</span>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <select
              className="btn btn-sm"
              value={sort}
              onChange={(e) => {
                updateState({ page: 1, sort: e.target.value });
              }}
            >
              {SORTS.map(([key, label]) => (
                <option value={key} key={key}>
                  정렬: {label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {active.length > 0 && (
          <div className="chips-active">
            <span style={{ fontSize: 'var(--fs-10)', color: 'var(--t3)' }}>적용된 필터</span>
            {active.map((a) => (
              <button
                className="chip-active"
                key={`${a.group}-${a.key}`}
                aria-label={`${a.label} 필터 해제`}
                onClick={() => toggle(a.group, a.key)}
              >
                {a.label} ×
              </button>
            ))}
          </div>
        )}

        <div className="card">
          {view === 'cards' && <div className="browse-plan-grid">{loading ? <p role="status">불러오는 중…</p> : error ? <p role="alert">{error}</p> : data?.plans.length === 0 ? <p>조건에 맞는 요금제가 없습니다. 필터를 줄여 보세요.</p> : data?.plans.map(plan => <PlanTile key={plan.id} plan={plan} selected={compare.some(p => p.id === plan.id)} onCompare={() => onToggleCompare(plan)} />)}</div>}
          <div style={{ overflowX: 'auto', display: view === 'table' ? 'block' : 'none' }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 28 }} />
                  <th>요금제명</th>
                  <th>사업자 / 망</th>
                  <th>월 요금</th>
                  <th>데이터</th>
                  <th>다 쓴 뒤</th>
                  <th>음성</th>
                  <th>{data?.plans[0]?.compareMonths ?? 12}개월 총비용</th>
                  <th>요금제 상세</th>
                </tr>
              </thead>
              <tbody>
                {error && (
                  <tr>
                    <td colSpan={9}>{error}</td>
                  </tr>
                )}
                {!error && loading && (
                  <tr>
                    <td colSpan={9}>불러오는 중…</td>
                  </tr>
                )}
                {!error && !loading && data?.plans.length === 0 && (
                  <tr>
                    <td colSpan={9}>조건에 맞는 요금제가 없습니다. 필터를 줄여 보세요.</td>
                  </tr>
                )}
                {!error &&
                  !loading &&
                  data?.plans.map((plan) => (
                    <tr key={plan.id}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`${plan.name} 비교함에 담기`}
                          checked={compare.some((c) => c.id === plan.id)}
                          onChange={() => onToggleCompare(plan)}
                        />
                      </td>
                      <td style={{ color: 'var(--t1)', fontWeight: 500 }}>
                        {plan.name}
                        {plan.ageCondition && (
                          <span className="tag tag-amber" style={{ marginLeft: 6 }}>
                            {plan.ageCondition}
                          </span>
                        )}
                      </td>
                      <td><BrandLogo carrier={plan.carrier}/></td>
                                            <td className="num">
                        {plan.price}원
                        {!plan.billingPriceKnown && <span className="promo-flag">페이백 반영 · 청구액 미확인</span>}
                        {plan.isPromo && <span className="promo-flag">프로모션</span>}
                      </td>
                      <td className="num">{plan.data}</td>
                      <td>
                        <span className={`tier-chip tier-${plan.dataTier}`}>{TIER_LABELS[plan.dataTier] ?? plan.dataTierLabel}</span>
                      </td>
                      <td>{plan.call}</td>
                      <td className="num">{plan.total}</td>
                      <td>
                        {plan.sourceUrl ? (
                          <a className="btn btn-sm" href={plan.sourceUrl} target="_blank" rel="noreferrer">
                            상세 보기
                          </a>
                        ) : (
                          <span className="btn btn-sm" aria-disabled="true" title="수집된 상세 주소가 없습니다">
                            상세 주소 없음
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          <div className="table-foot">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <strong>{compare.length}건 선택됨</strong>
              <button
                className="btn btn-sm btn-primary"
                disabled={compare.length === 0}
                onClick={onOpenCompare}
              >
                내 비교함 보기
              </button>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--t3)' }}>
              <span className="num">
                {from}–{to} / {total.toLocaleString()}
              </span>
              <button className="btn btn-sm" disabled={page <= 1} onClick={() => updateState({ page: page - 1 })}>
                ‹
              </button>
              <span className="num">
                {page} / {lastPage}
              </span>
              <button
                className="btn btn-sm"
                disabled={page >= lastPage}
                onClick={() => updateState({ page: page + 1 })}
              >
                ›
              </button>
            </div>
          </div>
        </div>
      </div>
      {chat}
    </div>
  );
}
