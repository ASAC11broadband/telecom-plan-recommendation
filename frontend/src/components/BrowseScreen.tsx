import { useEffect, useState } from 'react';
import { BrowseFilters, Facets, PlanItem, PlanPage } from '../types';
import { fetchStats, listPlans } from '../api';

const GROUPS: { group: keyof BrowseFilters; label: string; options: [string, string][] }[] = [
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
      ['lt3', '3GB 미만'],
      ['3to10', '3~10GB'],
      ['10to20', '10~20GB'],
      ['gte20', '20GB 이상'],
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

const EMPTY: BrowseFilters = { networks: [], data: [], voice: [], flags: [] };
const PAGE_SIZE = 20;

const labelOf = (group: keyof BrowseFilters, key: string) =>
  GROUPS.find((g) => g.group === group)?.options.find(([k]) => k === key)?.[1] ?? key;

function toCsv(plans: PlanItem[]) {
  const head = ['요금제명', '사업자/망', '월요금', '데이터', '소진후속도', '음성통화', '문자', '6개월총비용'];
  const body = plans.map((p) =>
    [p.name, p.carrier, p.price, p.data, p.qos, p.call, p.sms, p.total]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`)
      .join(',')
  );
  // BOM 을 붙여야 엑셀이 한글을 깨뜨리지 않는다.
  return '﻿' + [head.join(','), ...body].join('\n');
}

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
  const [facets, setFacets] = useState<Facets>({});
  const [filters, setFilters] = useState<BrowseFilters>(EMPTY);
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

  const exportCsv = () => {
    if (!data) return;
    const url = URL.createObjectURL(new Blob([toCsv(data.plans)], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `momoplan_plans_p${page}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="body split">
      <div className="card filter-panel">
        <div className="panel-head">
          <strong style={{ fontSize: 'var(--fs-13)' }}>필터</strong>
          <span
            style={{ fontSize: 'var(--fs-11)', color: 'var(--accent)', cursor: 'pointer' }}
            onClick={() => {
              setFilters(EMPTY);
              setQ('');
              setPage(1);
            }}
          >
            초기화
          </span>
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
            <button className="btn btn-sm" onClick={exportCsv} disabled={!data || data.plans.length === 0}>
              CSV 내보내기 (현재 페이지)
            </button>
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
          <div style={{ overflowX: 'auto' }}>
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
                      <td>{plan.carrier}</td>
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
                disabled={compare.length < 2}
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
    </div>
  );
}
