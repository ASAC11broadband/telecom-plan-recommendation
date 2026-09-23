import { PlanItem } from '../types';
export function PriceTerms({plan}:{plan:PlanItem}) {
 return <div className="price-terms">{plan.isPromo && plan.promoMonths > 0 ? <><span className="promo-duration">{plan.promoMonths}개월간 할인</span><p>{plan.promoMonths + 1}개월차부터 <strong>월 {plan.originalPrice.toLocaleString()}원</strong></p></> : <p>{plan.priceNote}</p>}</div>;
}
