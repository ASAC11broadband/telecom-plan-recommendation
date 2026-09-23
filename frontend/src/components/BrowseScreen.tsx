import { BrandLogo } from './BrandLogo';
import { categories, categoryFilters } from '../categories';
import { PlanTile } from './PlanTile';
import { useEffect, useState, ReactNode } from 'react';
import { BrowseFilters, Facets, PlanItem, PlanPage } from '../types';
import { fetchStats, listPlans } from '../api';

const PRICE_OPTIONS: [string, string][] = [['lt10k','1만원 미만'],['10to20k','1~2만원'],['20to30k','2~3만원'],['30to50k','3~5만원'],['50to70k','5~7만원'],['gte70k','7만원 이상']];
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
    group: 'data',
    label: '데이터 제공량',
    options: [
      ['lt10', '0~10GB 미만'],
      ['10to30', '10~30GB 미만'],
      ['30to50', '30~50GB 미만'],
      ['50to100', '50~100GB 미만'],
      ['gte100', '100GB 이상'],
      ['unlimited', '무제한'],
    ],
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
  {
    group: 'flags',
    label: '추가 조건',
    options: [
      ['online_only', '온라인 전용'],
      ['addon', '결합 부가서비스 있음'],
      ['promo', '프로모션 적용중'],
    ],
  },
];

const SORTS: [string, string][] = [
  ['fee_asc', '월 요금 낮은순'],
  ['fee_desc', '월 요금 높은순'],
  ['data_desc', '데이터 많은순'],
  ['total_asc', '6개월 총비용 낮은순'],
];

const EMPTY: BrowseFilters = { networks: [], data: [], voice: [], flags: [], price: [] };
const PAGE_SIZE = 20;

const labelOf = (group: keyof BrowseFilters, key: string) =>
  GROUPS.find((g) => g.group === group)?.options.find(([k]) => k === key)?.[1] ?? key;

export function BrowseScreen({
  chat,
  initialCategory = 'all',
  compare,
  onToggleCompare,
  onOpenCompare,
  onAskPlan,
}: {
  chat: ReactNode;
  initialCategory?: string;
  compare: PlanItem[];
  onToggleCompare: (plan: PlanItem) => void;
  onOpenCompare: () => void;
  onAskPlan: (plan: PlanItem) => void;
}) {
  const [category, setCategory] = useState(initialCategory);
  const [view, setView] = useState<'cards' | 'table'>('cards');
  const [facets, setFacets] = useState<Facets>({});
  const [filters, setFilters] = useState<BrowseFilters>(() => categoryFilters(initialCategory));
  const [sort, setSort] = useState('fee_asc');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<PlanPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchStats()
      .then((s) => setFacets(s.facets))
      .catch(() => setFacets({}));
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
    setPage(1);
    if (group !== 'price') setCategory('custom');
    setFilters((prev) => ({
      ...prev,
      [group]: prev[group].includes(key)
        ? prev[group].filter((k) => k !== key)
        : [...prev[group], key],
    }));
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
              setFilters(EMPTY);
              setCategory('all');
              setQ('');
              setPage(1);
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
              setPage(1);
              setQ(e.target.value);
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
        <div className="browse-categories">{categories.map(c => <button className={'btn '+(category === c.id ? 'selected' : '')} aria-pressed={category === c.id} key={c.id} onClick={() => { setCategory(c.id); setFilters(prev => ({...categoryFilters(c.id), price: prev.price})); setPage(1); }}>{c.icon} {c.title}</button>)}</div>
        <p className="muted">{categories.find(c => c.id === category)?.description ?? '직접 선택한 조건'} · 카테고리 간 요금제가 중복될 수 있습니다.</p>

        <div className="view-switch"><button className="btn" aria-pressed={view === 'cards'} onClick={() => setView('cards')}>카드 보기</button><button className="btn" aria-pressed={view === 'table'} onClick={() => setView('table')}>표 보기</button></div>
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
                setPage(1);
                setSort(e.target.value);
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
              <span
                className="chip-active"
                key={`${a.group}-${a.key}`}
                style={{ cursor: 'pointer' }}
                onClick={() => toggle(a.group, a.key)}
              >
                {a.label} ×
              </span>
            ))}
          </div>
        )}

        <div className="card">
          {view === 'cards' && <div className="browse-plan-grid">{loading ? <p role="status">불러오는 중…</p> : error ? <p role="alert">{error}</p> : data?.plans.length === 0 ? <p>조건에 맞는 요금제가 없습니다. 필터를 줄여 보세요.</p> : data?.plans.map(plan => <PlanTile key={plan.id} plan={plan} selected={compare.some(p => p.id === plan.id)} onCompare={() => onToggleCompare(plan)} onAsk={() => onAskPlan(plan)} />)}</div>}
          <div style={{ overflowX: 'auto', display: view === 'table' ? 'block' : 'none' }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 28 }} />
                  <th>요금제명</th>
                  <th>사업자 / 망</th>
                  <th>월 요금</th>
                  <th>데이터</th>
                  <th>소진 후 속도</th>
                  <th>음성</th>
                  <th>문자</th>
                  <th>액션</th>
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
                          checked={compare.some((c) => c.id === plan.id)}
                          onChange={() => onToggleCompare(plan)}
                        />
                      </td>
                      <td style={{ color: 'var(--t1)', fontWeight: 500 }}>{plan.name}</td>
                      <td><BrandLogo carrier={plan.carrier}/></td>
                      <td className="num">{plan.price}원</td>
                      <td className="num">{plan.data}</td>
                      <td>{plan.qos}</td>
                      <td>{plan.call}</td>
                      <td>{plan.sms}</td>
                      <td>
                        <button className="btn btn-sm" onClick={() => onAskPlan(plan)}>
                          AI 질의
                        </button>
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
                선택 항목 비교
              </button>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--t3)' }}>
              <span className="num">
                {from}–{to} / {total.toLocaleString()}
              </span>
              <button className="btn btn-sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                ‹
              </button>
              <span className="num">
                {page} / {lastPage}
              </span>
              <button
                className="btn btn-sm"
                disabled={page >= lastPage}
                onClick={() => setPage((p) => p + 1)}
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
