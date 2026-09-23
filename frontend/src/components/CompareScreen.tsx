import { useState } from 'react';
import { PlanItem } from '../types';
import { BrandLogo } from './BrandLogo';
const money = (n: number) => n.toLocaleString() + '원';
type ComparisonRow = { label: string; value: (p: PlanItem) => string; cost?: (p: PlanItem) => number };
const rows: ComparisonRow[] = [
 {label:'월 납부 요금',value:p=>money(p.priceNum),cost:p=>p.priceNum},
 {label:'6개월 총비용',value:p=>money(p.totalNum),cost:p=>p.totalNum},
 {label:'기본 월 요금 (할인 전)',value:p=>money(p.originalPrice)},
 {label:'할인 조건',value:p=>p.priceNote},
 {label:'기본 데이터',value:p=>p.data},
 {label:'데이터 소진 후 속도',value:p=>p.qos === '-' ? '제공 정보 없음' : p.qos},
 {label:'음성통화',value:p=>p.call},
 {label:'문자',value:p=>p.sms},
 {label:'테더링',value:p=>p.tetheringGb == null ? '제공 정보 없음' : p.tetheringGb+'GB'},
 {label:'통신망',value:p=>p.network || '제공 정보 없음'},
 {label:'통신 방식',value:p=>p.networkGen || '제공 정보 없음'},
 {label:'사업자 유형',value:p=>p.carrierType === 'MVNO' ? '알뜰폰' : '통신 3사'},
 {label:'부가 혜택',value:p=>p.benefit || '제공 정보 없음'},
 {label:'가입 조건',value:p=>p.ageCondition || '별도 조건 정보 없음'},
 {label:'온라인 전용',value:p=>p.isOnlineOnly ? '온라인 전용' : '온라인 전용 아님'},
];
function differenceSummary(plan: PlanItem, baseline: PlanItem) {
 const cost = plan.totalNum - baseline.totalNum;
 const fee = cost === 0 ? '6개월 총비용이 같아요' : `6개월간 ${money(Math.abs(cost))} ${cost < 0 ? '저렴해요' : '더 들어요'}`;
 let data = '데이터 제공량은 상세 조건을 확인해 주세요';
 if(plan.dataUnlimited && baseline.dataUnlimited) data='둘 다 데이터 무제한';
 else if(plan.dataUnlimited) data='이 요금제는 데이터 무제한';
 else if(baseline.dataUnlimited) data=`기준 요금제는 무제한 · 이 요금제는 ${plan.data}`;
 else if(plan.dataNum !== null && baseline.dataNum !== null) {const diff = Math.round((plan.dataNum-baseline.dataNum)*1000)/1000;data=diff===0?'기본 데이터 제공량이 같아요':`월 기본 데이터 ${Math.abs(diff).toLocaleString()}GB ${diff>0?'더 제공':'적게 제공'}`;}
 return {fee,data};
}
export function CompareScreen({plans,onRemove,onBrowse,onClear}:{plans:PlanItem[];onRemove:(p:PlanItem)=>void;onBrowse:()=>void;onClear:()=>void}) {
 const [onlyDiff,setOnlyDiff]=useState(false);
 const differs=(row:ComparisonRow)=>new Set(plans.map(row.value)).size>1;
 const visible=onlyDiff && plans.length > 1?rows.filter(differs):rows;
 const minMonthly=Math.min(...plans.map(p=>p.priceNum));
 const minTotal=Math.min(...plans.map(p=>p.totalNum));
 const gapMonthly=Math.max(...plans.map(p=>p.priceNum))-minMonthly;
 const gapTotal=Math.max(...plans.map(p=>p.totalNum))-minTotal;
 return <main className="body compare-page"><div className="compare-heading"><div><div className="eyebrow">MY PLAN COMPARISON</div><h1>비교함 <span>{plans.length}</span></h1><p>담아둔 요금제, 어떤 점이 다른지 나란히 비교해 보세요.</p></div><button className="btn btn-primary" onClick={onBrowse}>+ 요금제 더 담기</button></div>
 {plans.length === 0 ? <section className="compare-empty"><div className="empty-symbol">⇄</div><h2>비교할 요금제를 담아보세요</h2><p>요금제 카드의 ‘비교 담기’를 누르면 이곳에서 함께 볼 수 있어요.</p><button className="btn btn-primary" onClick={onBrowse}>요금제 둘러보기 →</button></section> : <>
 {plans.length > 1 ? <section className="comparison-summary"><div><span>월 요금 차이</span><strong>{gapMonthly ? '최대 '+money(gapMonthly) : '월 요금이 같아요'}</strong></div><div><span>6개월 총비용 차이</span><strong>{gapTotal ? '최대 '+money(gapTotal) : '총비용이 같아요'}</strong></div><div><span>서로 다른 조건</span><strong>{rows.filter(differs).length}개 항목</strong></div></section> : <div className="compare-notice">요금제를 하나 더 담으면 가격 차이와 서로 다른 조건을 표시해 드려요.</div>}
 {plans.length>1 && <section className="comparison-insights" aria-label="요금제 차이 요약"><h2>한눈에 보는 차이</h2><p>먼저 담은 ‘{plans[0].name}’ 기준으로 비교했어요.</p><div>{plans.slice(1).map(p=>{const summary=differenceSummary(p,plans[0]);return <article key={p.id}><strong>{p.name}</strong><span>{summary.fee}</span><small>{summary.data}</small></article>;})}</div></section>}
 <div className="compare-toolbar"><button className="btn" onClick={onClear}>전체 비우기</button><label><input type="checkbox" checked={onlyDiff && plans.length > 1} disabled={plans.length<2} onChange={e=>setOnlyDiff(e.target.checked)}/> 다른 항목만 보기</label><span>보라색은 서로 다른 조건 · 최저 비용은 담은 요금제 기준</span></div>
 <div className="comparison-scroll" tabIndex={0} role="region" aria-label="담은 요금제 비교표"><table className="comparison-table"><thead><tr><th scope="col">비교 항목</th>{plans.map(p=><th scope="col" key={p.id}><BrandLogo carrier={p.carrier}/><h3>{p.name}</h3><div className="compare-badges">{plans.length>1&&p.totalNum===minTotal&&<span>6개월 최저 비용</span>}</div><button className="text-btn" onClick={()=>onRemove(p)} aria-label={p.name+' 비교함에서 빼기'}>삭제 ×</button></th>)}</tr></thead><tbody>{visible.map(row=><tr key={row.label} className={differs(row)?'is-different':''}><th scope="row">{row.label}{differs(row)&&<span className="diff-dot" aria-label="차이 있음"/>}</th>{plans.map(p=>{const cost=row.cost?.(p);const min=row.cost?Math.min(...plans.map(row.cost)):0;return <td key={p.id}><strong>{row.value(p)}</strong>{plans.length>1&&cost!==undefined&&<small className={cost===min?'lowest':'cost-difference'}>{cost===min?'최저 비용':'최저 대비 +'+money(cost-min)}</small>}</td>;})}</tr>)}{!visible.length&&<tr><td colSpan={plans.length+1}>표시할 차이가 없습니다. ‘다른 항목만 보기’를 해제해 전체 조건을 확인하세요.</td></tr>}</tbody></table></div>
 <p className="data-disclaimer">보유 요금제 데이터 기준입니다. 할인 기간과 가입 조건에 따라 실제 납부액이 달라질 수 있습니다. 제공 정보 없음은 혜택이 없다는 의미가 아닙니다.</p></>}
 </main>;
}
