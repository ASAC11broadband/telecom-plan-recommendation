import { useEffect, useState } from 'react';
import { BrowseFilters, EMPTY_FILTERS, Facets, PlanItem, PlanPage, Stats } from '../types';
import { fetchStats, listPlans } from '../api';

const GROUPS: { group: keyof BrowseFilters; label: string; options: [string, string][] }[] = [
  {
    group: 'price',
    label: '월 요금',
    options: [
      ['lt10k', '1만원 미만'],
      ['10to20k', '1~2만원'],
      ['20to30k', '2~3만원'],
      ['30to50k', '3~5만원'],
      ['gte50k', '5만원 이상'],
    ],
  },
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
      ['lt3', '3GB 미만'],
      ['3to10', '3~10GB 미만'],
      ['10to20', '10~20GB 미만'],
      ['gte20', '20GB 이상'],
      // 아래 '다 쓴 뒤에는' 그룹과 같은 말을 쓴다. 그냥 '무제한'이라고 두면 AI 추천이 말하는
      // '무제한'(기본량 무제한 + 대용량·속도 유지 상품)과 같은 단어인데 건수가 달라 보인다.
      ['unlimited', '기본량 무제한'],
    ],
  },
  {
    // 기본량보다 이쪽이 체감을 가른다. '무제한'(소진 후 100Kbps)보다 '100GB+5Mbps'가 빠르다.
    group: 'tier',
    label: '다 쓴 뒤에는',
    options: [
      ['unlimited_full', '기본량 무제한'],
      ['qos_hd', 'HD 참고 등급'],
      ['qos_sd', '480p 참고 등급'],
      ['qos_lite', '저화질·음악'],
      ['qos_text', '문자·웹만'],
      ['capped', '소진 후 정책 미확인'],
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
      ['no_age_limit', '가입 자격 제한 없음'],
      ['online_only', '온라인 전용'],
      ['addon', '결합 부가서비스 있음'],
      ['promo', '프로모션 적용중'],
      ['benefit_value', '혜택 금액 확인됨'],
    ],
  },
];

const SORTS: [string, string][] = [
  ['fee_asc', '월 요금 낮은순'],
  ['fee_desc', '월 요금 높은순'],
  ['data_desc', '데이터 많은순'],
  ['qos_desc', '소진 후 속도 빠른순'],
  ['total_asc', '총비용 낮은순'],
  ['effective_asc', '현금성 혜택 차감 참고값 낮은순'],
];

const EMPTY = EMPTY_FILTERS;
const PAGE_SIZE = 20;

const labelOf = (group: keyof BrowseFilters, key: string) =>
  GROUPS.find((g) => g.group === group)?.options.find(([k]) => k === key)?.[1] ?? key;

export function BrowseScreen({
  compare,
  onToggleCompare,
  onOpenCompare,
  onAskPlan,
}: {
  compare: PlanItem[];
  onToggleCompare: (plan: PlanItem) => void;
  onOpenCompare: () => void;
  onAskPlan: (plan: PlanItem) => void;
}) {
  const [stats, setStats] = useState<Stats | null>(null);
  const facets: Facets = stats?.facets ?? {};
  const [filters, setFilters] = useState<BrowseFilters>(EMPTY);
  const [sort, setSort] = useState('fee_asc');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
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
    setPage(1);
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
    <div className="body">
      <div className="section-heading"><h1>전체 요금제 둘러보기</h1><p>가격과 제공량을 살펴보고, 관심 있는 요금제를 비교함에 담아보세요.</p></div>
      {stats && <section className="card catalog-overview">
        <div className="row-between"><h2>요금제 현황</h2><span className="comparison-note">수집 기준 {stats.dataAsOf}</span></div>
        <p>전체 <strong>{stats.total.toLocaleString()}개</strong> · 통신 3사 {stats.mno.toLocaleString()}개 · 알뜰폰 {stats.mvno.toLocaleString()}개 · 알뜰폰 브랜드 {stats.brands}개</p>
        <div className="distribution-grid">{(['price', 'data', 'networks'] as const).map(group => {
          const definition = GROUPS.find(item => item.group === group)!;
          const options = definition.options.filter(([key]) => key !== 'MNO');
          const counted = options.reduce((sum, [key]) => sum + (facets[group]?.[key] ?? 0), 0);
          return <div key={group}><h3>{definition.label} 분포</h3>
            {options.map(([key, label]) => {
              const count = facets[group]?.[key] ?? 0;
              return <button className="distribution-row" key={key} aria-pressed={filters[group].includes(key)} onClick={() => toggle(group, key)}>
                <span>{label}</span><span className="distribution-track"><span style={{ width: `${stats.total ? count / stats.total * 100 : 0}%` }} /></span><span>{count.toLocaleString()}개</span>
              </button>;
            })}
            {counted < stats.total && <small>정보 미확인 {(stats.total - counted).toLocaleString()}개</small>}
          </div>;
        })}</div>
        <p className="comparison-note">필터 적용 전 수집 상품 기준이며 가입 조건·옵션별 상품을 포함합니다. 요금 분포에는 페이백 반영 표시가도 포함됩니다. 청구액 미확인 상품은 AI 추천과 총비용 계산에서 제외합니다. 이용자 수나 시장점유율이 아닙니다. 막대를 누르면 목록에 필터가 적용됩니다.</p>
      </section>}
      <div className="split catalog-layout">
      <div className="card filter-panel">
        <div className="panel-head">
          <strong style={{ fontSize: 'var(--fs-13)' }}>필터</strong>
          <button
            className="linklike accent"
            onClick={() => {
              setFilters(EMPTY);
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
            <span className="lbl">{g.label}</span>
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
      </div>

      <div style={{ flex: 1 }}>
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
          <div style={{ overflowX: 'auto' }}>
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
                  <th>현금성 혜택 차감 참고값</th>
                  <th>액션</th>
                </tr>
              </thead>
              <tbody>
                {error && (
                  <tr>
                    <td colSpan={10}>{error}</td>
                  </tr>
                )}
                {!error && loading && (
                  <tr>
                    <td colSpan={10}>불러오는 중…</td>
                  </tr>
                )}
                {!error && !loading && data?.plans.length === 0 && (
                  <tr>
                    <td colSpan={10}>조건에 맞는 요금제가 없습니다. 필터를 줄여 보세요.</td>
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
                      <td>{plan.carrier}</td>
                      <td className="num">
                        {plan.price}원
                        {!plan.billingPriceKnown && <span className="promo-flag">페이백 반영 · 청구액 미확인</span>}
                        {plan.isPromo && <span className="promo-flag">프로모션</span>}
                      </td>
                      <td className="num">{plan.data}</td>
                      <td>
                        <span className={`tier-chip tier-${plan.dataTier}`}>{plan.dataTierLabel}</span>
                      </td>
                      <td>{plan.call}</td>
                      <td className="num">{plan.total}</td>
                      <td className="num">
                        {plan.benefitDeductible > 0
                          ? plan.benefitExceedsFee
                            ? '0원*'
                            : plan.effectiveTotal
                          : '—'}
                      </td>
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
                내 비교함 보기
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
      </div>
    </div>
  );
}
