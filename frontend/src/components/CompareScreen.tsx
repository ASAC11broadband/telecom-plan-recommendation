import { useState } from 'react';
import { PlanItem } from '../types';
import { BrandLogo } from './BrandLogo';

const money = (n: number) => n.toLocaleString() + '원';
type ComparisonRow = { label: string; value: (p: PlanItem) => string; cost?: (p: PlanItem) => number | null };
const rows: ComparisonRow[] = [
  { label: '월 납부 요금', value: p => money(p.priceNum), cost: p => p.priceNum },
  { label: '비교 기간 총비용', value: p => p.totalNum === null ? '청구액 확인 필요' : `${money(p.totalNum)} (${p.compareMonths}개월)`, cost: p => p.totalNum },
  { label: '기본 월 요금 (할인 전)', value: p => money(p.originalPrice) },
  { label: '할인 조건', value: p => p.priceNote },
  { label: '기본 데이터', value: p => p.data },
  { label: '데이터 소진 후 속도', value: p => p.qos === '-' ? '제공 정보 없음' : p.qos },
  { label: '음성통화', value: p => p.call },
  { label: '문자', value: p => p.sms },
  { label: '테더링', value: p => p.tetheringGb == null ? '제공 정보 없음' : p.tetheringGb + 'GB' },
  { label: '통신망', value: p => p.network || '제공 정보 없음' },
  { label: '통신 방식', value: p => p.networkGen || '제공 정보 없음' },
  { label: '사업자 유형', value: p => p.carrierType === 'MVNO' ? '알뜰폰' : '통신 3사' },
  { label: '부가 혜택', value: p => p.benefit || '제공 정보 없음' },
  { label: '가입 조건', value: p => p.ageCondition || '별도 조건 정보 없음' },
];

function differenceSummary(plan: PlanItem, baseline: PlanItem) {
  const cost = plan.totalNum === null || baseline.totalNum === null ? null : plan.totalNum - baseline.totalNum;
  const fee = cost === null
    ? '청구액 미확인이라 총비용을 비교할 수 없어요'
    : cost === 0
      ? `${plan.compareMonths}개월 총비용이 같아요`
      : `${plan.compareMonths}개월간 ${money(Math.abs(cost))} ${cost < 0 ? '저렴해요' : '더 들어요'}`;
  let data = '데이터 제공량은 상세 조건을 확인해 주세요';
  if (plan.dataUnlimited && baseline.dataUnlimited) data = '둘 다 데이터 무제한';
  else if (plan.dataUnlimited) data = '이 요금제는 데이터 무제한';
  else if (baseline.dataUnlimited) data = `기준 요금제는 무제한 · 이 요금제는 ${plan.data}`;
  else if (plan.dataNum !== null && baseline.dataNum !== null) {
    const diff = Math.round((plan.dataNum - baseline.dataNum) * 1000) / 1000;
    data = diff === 0
      ? '기본 데이터 제공량이 같아요'
      : `월 기본 데이터 ${Math.abs(diff).toLocaleString()}GB ${diff > 0 ? '더 제공' : '적게 제공'}`;
  }
  return { fee, data };
}

export function CompareScreen({ plans, onRemove, onBrowse, onClear }: { plans: PlanItem[]; onRemove: (p: PlanItem) => void; onBrowse: () => void; onClear: () => void }) {
  const [onlyDiff, setOnlyDiff] = useState(false);
  const [baselineId, setBaselineId] = useState(plans[0]?.id ?? '');
  const baseline = plans.find(plan => plan.id === baselineId) ?? plans[0];
  const orderedPlans = baseline ? [baseline, ...plans.filter(plan => plan.id !== baseline.id)] : [];
  const differs = (row: ComparisonRow) => new Set(plans.map(row.value)).size > 1;
  const visible = onlyDiff && plans.length > 1 ? rows.filter(differs) : rows;

  return <main className="body compare-page">
    <div className="compare-heading"><div><div className="eyebrow">MY PLAN COMPARISON</div><h1>비교함 <span>{plans.length}</span></h1><p>비교 기준으로 삼을 요금제를 선택해 차이를 확인해 보세요.</p></div><button className="btn btn-primary" disabled={plans.length >= 5} onClick={onBrowse}>+ 요금제 더 담기</button></div>
    {plans.length === 0 ? <section className="compare-empty"><div className="empty-symbol">⇄</div><h2>비교할 요금제를 담아보세요</h2><p>요금제 카드의 ‘비교 담기’를 누르면 이곳에서 함께 볼 수 있어요.</p><button className="btn btn-primary" onClick={onBrowse}>요금제 둘러보기 →</button></section> : <>
      <section className="compare-plan-picker" aria-label="비교 기준 요금제 선택">
        {plans.map(plan => <button type="button" className={plan.id === baseline?.id ? 'plan-choice selected' : 'plan-choice'} aria-pressed={plan.id === baseline?.id} key={plan.id} onClick={() => setBaselineId(plan.id)}><BrandLogo carrier={plan.carrier}/><strong>{plan.name}</strong></button>)}
      </section>

      {plans.length > 1 ? <section className="comparison-insights" aria-label="요금제 차이 요약"><h2>한눈에 보는 차이</h2><p>‘{baseline.name}’ 기준으로 비교했어요.</p><div>{plans.filter(plan => plan.id !== baseline.id).map(plan => { const summary = differenceSummary(plan, baseline); return <article key={plan.id}><strong>{plan.name}</strong><span>{summary.fee}</span><small>{summary.data}</small></article>; })}</div></section> : <div className="compare-notice">요금제를 하나 더 담으면 가격 차이와 서로 다른 조건을 표시해 드려요.</div>}

      <div className="compare-toolbar"><button className="btn" onClick={onClear}>전체 비우기</button><label><input type="checkbox" checked={onlyDiff && plans.length > 1} disabled={plans.length < 2} onChange={e => setOnlyDiff(e.target.checked)}/> 다른 항목만 보기</label><span>보라색은 서로 다른 조건 · 최저 비용은 담은 요금제 기준</span></div>
      <div className="comparison-scroll" tabIndex={0} role="region" aria-label="담은 요금제 비교표"><table className="comparison-table"><thead><tr><th scope="col">비교 항목</th>{orderedPlans.map(plan => <th scope="col" key={plan.id}><BrandLogo carrier={plan.carrier}/><h3>{plan.name}</h3><button className="text-btn" onClick={() => onRemove(plan)} aria-label={plan.name + ' 비교함에서 빼기'}>삭제 ×</button></th>)}</tr></thead><tbody>{visible.map(row => <tr key={row.label} className={differs(row) ? 'is-different' : ''}><th scope="row">{row.label}{differs(row) && <span className="diff-dot" aria-label="차이 있음"/>}</th>{orderedPlans.map(plan => { const cost = row.cost?.(plan); const known = row.cost ? orderedPlans.flatMap(item => { const value = row.cost!(item); return value === null ? [] : [value]; }) : []; const min = Math.min(...known); return <td key={plan.id}><strong>{row.value(plan)}</strong>{known.length > 1 && cost !== undefined && cost !== null && <small className={cost === min ? 'lowest' : 'cost-difference'}>{cost === min ? '최저 비용' : '최저 대비 +' + money(cost - min)}</small>}</td>; })}</tr>)}{!visible.length && <tr><td colSpan={plans.length + 1}>표시할 차이가 없습니다. ‘다른 항목만 보기’를 해제해 전체 조건을 확인하세요.</td></tr>}</tbody></table></div>
      <p className="data-disclaimer">보유 요금제 데이터 기준입니다. 할인 기간과 가입 조건에 따라 실제 납부액이 달라질 수 있습니다. 제공 정보 없음은 혜택이 없다는 의미가 아닙니다.</p>
    </>}
  </main>;
}
