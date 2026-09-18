import { PlanItem, ScreenType } from '../types';
import { CompareTable } from './ResultScreen';

export function CompareScreen({ plans, onRemove, onNavigate }: {
  plans: PlanItem[];
  onRemove: (plan: PlanItem) => void;
  onNavigate: (screen: ScreenType) => void;
}) {
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
      <div className="saved-plans">{plans.map(plan => <div className="card saved-plan" key={plan.id}>
        <span className="tag tag-muted">{plan.carrier}</span><strong>{plan.name}</strong>
        <p>{plan.billingPriceKnown ? '월' : '페이백 반영 표시가'} {plan.price}원 · {plan.data}</p>
        <button className="btn btn-sm" onClick={() => onRemove(plan)} aria-label={`${plan.name} 비교함에서 삭제`}>삭제</button>
      </div>)}</div>
      {plans.length === 1 && <p className="notice">요금제를 하나 더 담으면 나란히 비교할 수 있습니다.</p>}
      <CompareTable plans={plans} />
      <p className="comparison-note">수집 당시 요금과 제공량 기준입니다. 혜택 차감 참고값은 개인의 실제 절약액이 아니며, 가입 조건과 할인 적용 여부를 확인해야 합니다. 비교함은 이 탭에서 새로고침해도 유지됩니다.</p>
    </>}
  </main>;
}
