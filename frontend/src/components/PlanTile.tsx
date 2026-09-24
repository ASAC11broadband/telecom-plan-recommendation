import { PriceTerms } from './PriceTerms';
import { BrandLogo } from './BrandLogo';
import { PlanItem } from '../types';
const gb = (value: number) => Number.isInteger(value) ? String(value) : String(Math.round(value * 1000) / 1000);
const dataAmount = (value: number) => value < 1 ? `${gb(value * 1000)}MB` : `${gb(value)}GB`;
export function PlanTile({ plan, selected, onCompare }: { plan: PlanItem; selected: boolean; onCompare: () => void }) {
 const monthlyBaseGb = plan.dailyDataGb && plan.dataNum !== null ? Math.max(0, plan.dataNum - plan.dailyDataGb * 30) : null;
 return <article className="plan-tile"><div className="row-between"><BrandLogo carrier={plan.carrier}/><span className="tag">{plan.networkGen || '요금제'}</span></div>
 <h3 title={plan.name}>{plan.name}</h3>{plan.dailyDataGb ? <div className="plan-data plan-data-daily">{monthlyBaseGb !== null && monthlyBaseGb > 0 && <><strong>{dataAmount(monthlyBaseGb)}</strong><span>/월</span></>}<strong>{dataAmount(plan.dailyDataGb)}</strong><span>/일</span></div> : <div className="plan-data">{plan.data}<span> / 월</span></div>}<p className="data-speed">소진 후 {plan.qos === '-' ? '추가 제공 정보 없음' : plan.qos}</p>
 <div className="plan-price"><span>월 </span>{plan.price}<span>원</span></div><PriceTerms plan={plan}/><div className="tile-voice"><span>음성통화 <strong>{plan.call}</strong></span><span>문자 <strong>{plan.sms}</strong></span></div>
 {plan.benefits.length > 0 ? <details className="tile-benefits"><summary>부가혜택 보기 <span>{plan.benefits.length}</span></summary><ul>{plan.benefits.map((benefit, index) => <li key={`${benefit}-${index}`}>{benefit}</li>)}</ul></details> : <p className="tile-benefits-empty">부가혜택 없음</p>}
 <div className="cost-line">{plan.compareMonths}개월 총비용 <strong>{plan.total}</strong></div>
 {plan.ageCondition && <p className="price-note">가입 조건: {plan.ageCondition}</p>}
 <div className="tile-actions"><button className={'btn ' + (selected ? 'selected' : '')} onClick={onCompare}>{selected ? '✓ 담김 · 삭제' : '+ 비교 담기'}</button>{plan.sourceUrl ? <a className="btn" href={plan.sourceUrl} target="_blank" rel="noreferrer">자세히 보기 ↗</a> : <span className="btn" aria-disabled="true" title="수집된 상세 주소가 없습니다">상세 주소 없음</span>}</div></article>;
}
