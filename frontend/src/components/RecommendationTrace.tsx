import { RecommendResponse } from '../types';

const PRIORITY_LABELS: Record<string, string> = {
  price: '월 요금',
  data: '데이터',
  qos: '소진 후 속도',
  benefit: '부가 혜택',
  voice: '통화',
  tethering: '테더링',
};

/** 내부 점수표 대신 사용자가 추천 흐름을 이해할 수 있는 말로 설명한다. */
export function RecommendationTrace({ result }: { result: RecommendResponse }) {
  const trace = result.trace;
  if (!trace?.rankedCount) return null;

  const priorities = trace.rankingProfile?.priorities ?? result.profile?.priorities ?? [];
  const priorityText = priorities
    .map((key) => PRIORITY_LABELS[key] ?? key)
    .filter(Boolean)
    .join(', ');

  return (
    <details className="card recommendation-trace">
      <summary>
        <span>추천 결과가 나온 과정</span>
        <small>어떤 기준으로 Top {result.plans.length}를 골랐는지 확인할 수 있어요.</small>
      </summary>

      <div className="trace-sec">
        <div className="section-label">1. 입력한 조건으로 후보를 줄였어요</div>
        <div className="funnel">
          <span>전체 <b className="num">{result.totalCount.toLocaleString()}</b>개</span>
          <span className="arrow">→</span>
          <span>조건에 맞는 요금제 <b className="num">{result.candidateCount.toLocaleString()}</b>개</span>
          <span className="arrow">→</span>
          <span>중복 정리 <b className="num">{trace.rankedCount.toLocaleString()}</b>개</span>
          <span className="arrow">→</span>
          <span>최종 추천 <b className="num">{result.plans.length}</b>개</span>
        </div>
        <p className="trace-note">
          조건에 맞지 않는 상품을 제외한 뒤, 이름과 제공 조건이 같은 상품은 하나로 정리했어요.
          {trace.referenceBaselineApplied && ' 현재 요금제를 알려주신 경우에는 데이터 수준이 크게 낮아지지 않도록 함께 확인했어요.'}
        </p>
      </div>

      <div className="trace-sec">
        <div className="section-label">2. 여러 조건을 함께 비교했어요</div>
        <div className="trace-basis-list">
          {Object.values(PRIORITY_LABELS).map((label) => <span key={label}>{label}</span>)}
        </div>
        <p className="trace-note">
          한 가지 조건만 보고 고르지 않고 여러 조건들을 함께 비교했어요.
          {priorityText
            ? ` 대화에서 중요하다고 말씀하신 ${priorityText} 항목은 더 비중 있게 반영했어요.`
            : ''}
        </p>
      </div>

      <div className="trace-sec">
        <div className="section-label">3. 서로 다른 장점이 있는 요금제를 골랐어요</div>
        <p className="trace-note">
          비슷한 상품만 반복해서 보여주지 않고, 가격·데이터·소진 후 속도·혜택에서 비교할 만한 차이가 있는 요금제를 함께 골랐어요.
        </p>
      </div>
    </details>
  );
}
