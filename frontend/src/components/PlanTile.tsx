import { PriceTerms } from './PriceTerms';
import { BrandLogo } from './BrandLogo';
import { PlanItem } from '../types';
export function PlanTile({ plan, selected, onCompare, onAsk }: { plan: PlanItem; selected: boolean; onCompare: () => void; onAsk: () => void }) {
 return <article className="plan-tile"><div className="row-between"><BrandLogo carrier={plan.carrier}/><span className="tag">{plan.networkGen || '요금제'}</span></div>
 <h3 title={plan.name}>{plan.name}</h3><div className="plan-data">{plan.data}<span> / 월</span></div><p className="data-speed">소진 후 {plan.qos === '-' ? '추가 제공 정보 없음' : plan.qos}</p>
 <div className="plan-price"><span>월 </span>{plan.price}<span>원</span></div><PriceTerms plan={plan}/><div className="tile-voice"><span>음성통화 <strong>{plan.call}</strong></span><span>문자 <strong>{plan.sms}</strong></span></div><div className="cost-line">{plan.compareMonths}개월 총비용 <strong>{plan.total}</strong></div>
 {plan.ageCondition && <p className="price-note">가입 조건: {plan.ageCondition}</p>}
 <div className="tile-actions"><button className={'btn ' + (selected ? 'selected' : '')} onClick={onCompare}>{selected ? '✓ 담김 · 삭제' : '+ 비교 담기'}</button><button className="btn" onClick={onAsk}>자세히 보기 ↗</button></div></article>;
}
