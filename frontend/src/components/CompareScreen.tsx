import { PlanItem, ScreenType } from '../types';
import { CompareTable } from './ResultScreen';

export function CompareScreen({ plans, recommendedIds, onRemove, onNavigate }: {
  plans: PlanItem[];
  recommendedIds: string[];
  onRemove: (plan: PlanItem) => void;
  onNavigate: (screen: ScreenType) => void;
}) {
  const totals = plans.filter((plan) => plan.totalNum !== null);
  const lowestTotal = totals.reduce<PlanItem | null>(
    (best, plan) => !best || plan.totalNum! < best.totalNum! ? plan : best,
    null,
  );
  const fiveG = plans.filter((plan) => plan.networkGen === '5G');
  const promoRise = plans.filter((plan) => plan.priceRisesAfter !== null);

  return <main className="body">
    <div className="row-between section-heading">
      <div><h1>내 비교함 <span className="tag tag-accent">{plans.length}개</span></h1>
        <p>직접 찾은 요금제와 AI 추천 요금제를 함께 비교하세요.</p></div>
      <button className="btn" onClick={() => onNavigate('s-browse')}>요금제 더 찾기</button>
    </div>
    {plans.length === 0 ? <div className="card empty-state">
      <h2>아직 담은 요금제가 없습니다</h2><p>전체 요금제나 AI 추천 결과에서 ‘비교함에 담기’를 눌러보세요.</p>
      <button className="btn btn-primary" onClick={() => onNavigate('s-browse')}>전체 요금제 둘러보기</button>
    </div> : <>
      <section className="card compare-summary" aria-label="비교 요약">
        <div>
          <span className="k">가장 낮은 {plans[0].compareMonths}개월 총비용</span>
          <strong>{lowestTotal ? `${lowestTotal.name} · ${lowestTotal.total}` : '청구액 확인 필요'}</strong>
        </div>
        <div>
          <span className="k">5G 선택지</span>
          <strong>{fiveG.length ? `${fiveG.length}개 포함` : '담긴 상품에 없음'}</strong>
        </div>
        <div>
          <span className="k">프로모션 종료 주의</span>
          <strong>{promoRise.length ? `${promoRise.length}개 · 종료 뒤 요금 상승` : '비교 기간 내 상승 없음'}</strong>
        </div>
      </section>
      <div className="saved-plans">{plans.map(plan => <div className="card saved-plan" key={plan.id}>
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
          <span className="tag tag-muted">{plan.carrier}</span>
          <span className={`tag ${recommendedIds.includes(plan.id) ? 'tag-accent' : 'tag-green'}`}>
            {recommendedIds.includes(plan.id) ? 'AI 추천' : '직접 탐색'}
          </span>
        </div>
        <strong>{plan.name}</strong>
        <p>{plan.billingPriceKnown ? '월' : '페이백 반영 표시가'} {plan.price}원 · {plan.data}</p>
        <button className="btn btn-sm" onClick={() => onRemove(plan)} aria-label={`${plan.name} 비교함에서 삭제`}>삭제</button>
      </div>)}</div>
      {plans.length === 1 && <p className="notice">요금제를 하나 더 담으면 나란히 비교할 수 있습니다.</p>}
      <CompareTable plans={plans} />
      <p className="comparison-note">수집 당시 요금과 제공량 기준입니다. 혜택 차감 참고값은 개인의 실제 절약액이 아니며, 가입 조건과 할인 적용 여부를 확인해야 합니다. 비교함은 이 탭에서 새로고침해도 유지됩니다.</p>
    </>}
  </main>;
}
