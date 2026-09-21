import { PlanItem, RecommendResponse } from '../types';
import { describeProfile } from '../profileText';

/** "왜 이게 1순위인가" 에 답한다.
 *
 *  후보들이 실제로 갈린 지점(축별 충족도)과 비용 기준 차이를 보여주는 게 기본이다.
 *  기대순위·1위 수용도 같은 내부 지표는 "만족 확률이 아니다"라는 부인 문장을 달아야 할 만큼
 *  오해를 부르기 쉬워 여기서는 "5. 추천 지표 해석" 절 하나에 정의를 명확히 달아 모아 둔다.
 */

const AXES: [string, string][] = [
  ['price', '가격'],
  ['data', '데이터'],
  ['qos', '소진 후 속도'],
  ['benefit', '혜택'],
  ['voice', '통화'],
  ['tethering', '테더링'],
];

/** 추천 후보 사이에서 값이 갈리는 축만. 전부 같은 값인 행은 순위를 설명하지 못한다. */
function decidingAxes(plans: PlanItem[]): [string, string][] {
  return AXES.filter(([key]) => {
    const values = plans.map((p) => p.criteriaFit[key]).filter((v) => v !== undefined);
    return values.length > 1 && Math.max(...values) - Math.min(...values) > 0.01;
  });
}

export function RecommendationTrace({ result, plan }: { result: RecommendResponse; plan?: PlanItem }) {
  const trace = result.trace;
  if (!trace?.rankedCount) return null;

  const axes = decidingAxes(result.plans);
  const conditions = describeProfile(trace.rankingProfile ?? result.profile);
  const estimated = result.plans.some((p) => p.costIsEstimate);
  const compareMonths = result.plans[0]?.compareMonths ?? 12;

  return (
    <details className="card recommendation-trace">
      <summary>어떻게 이 결과가 나왔나요?</summary>

      <div className="trace-sec">
        <div className="section-label">1. 이렇게 좁혔습니다</div>
        <div className="funnel">
          <span>
            전체 <b className="num">{result.totalCount.toLocaleString()}</b>건
          </span>
          <span className="arrow">→</span>
          <span>
            조건 충족 <b className="num">{result.candidateCount.toLocaleString()}</b>건
          </span>
          <span className="arrow">→</span>
          <span>
            중복 정리 <b className="num">{trace.rankedCount.toLocaleString()}</b>건
          </span>
          <span className="arrow">→</span>
          <span>
            추천 <b className="num">{result.plans.length}</b>건
          </span>
        </div>
        <p className="trace-note">
          {trace.deduplication}
          {trace.referenceBaselineApplied &&
            ' 지금 쓰시는 요금제의 데이터 수준보다 낮아지지 않도록 기준을 잡았습니다.'}
        </p>
        {trace.diversification && <p className="trace-note">{trace.diversification}</p>}
      </div>

      {conditions.length > 0 && (
        <div className="trace-sec">
          <div className="section-label">2. 이 조건으로 찾았습니다</div>
          <div className="trace-conditions">
            {conditions.map((c) => (
              <span className="trace-cond" key={c.k}>
                <span className="k">{c.k}</span>
                <span className="v">{c.value}</span>
                {c.kind && c.kind !== '요청' && (
                  <span className={`kind ${c.kind === '추정' ? 'guess' : ''}`}>{c.kind}</span>
                )}
              </span>
            ))}
          </div>
          <p className="trace-note">
            잘못 읽은 조건이 있으면 아래 상담 창에 바로 말씀해 주세요. 다시 계산합니다.
          </p>
        </div>
      )}

      {axes.length > 0 && (
        <div className="trace-sec">
          <div className="section-label">3. 순위가 갈린 지점</div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>항목</th>
                  {result.plans.map((plan) => (
                    <th key={plan.id}>
                      {plan.rank}. {plan.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {axes.map(([key, label]) => {
                  const best = Math.max(...result.plans.map((p) => p.criteriaFit[key] ?? 0));
                  return (
                    <tr key={key}>
                      <td>{label}</td>
                      {result.plans.map((plan) => {
                        const value = plan.criteriaFit[key] ?? 0;
                        return (
                          <td className="num" key={plan.id}>
                            <span className={value === best ? 'axis-best' : undefined}>
                              {Math.round(value * 100)}
                            </span>
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
                <tr>
                  <td>순위에 쓴 월 요금</td>
                  {result.plans.map((plan) => (
                    <td className="num" key={plan.id}>
                      {plan.rankingAverageFee === null ? '확인 필요' : `${plan.rankingAverageFee.toLocaleString()}원`}{plan.costIsEstimate && '*'}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
          <p className="trace-note">
            항목별 점수는 효용값을 0~100으로 표시한 비교 지표이며, 만족 확률이나 조건 충족률이 아닙니다. 순위는 한
            항목이 아니라 여섯 항목을 함께 보고 정합니다.
          </p>
        </div>
      )}

      {plan && (
        <div className="trace-sec">
          <div className="section-label">4. 추천 지표 해석</div>
          <div className="mini-table">
            <div className="r">
              <span>지표</span>
              <span>값</span>
            </div>
            <div className="r">
              <span>추천 적합도</span>
              <span className="v num">
                {plan.recommendationFit === null ? '확인 필요' : `${Math.round(plan.recommendationFit)}점`}
              </span>
            </div>
            <div className="r">
              <span>상위권 안정성</span>
              <span className="v num">
                {plan.top3Acceptability === null ? '확인 필요' : `${Math.round(plan.top3Acceptability * 100)}%`}
              </span>
            </div>
            <div className="r">
              <span>1위 수용도</span>
              <span className="v num">
                {plan.firstRankAcceptability === null ? '확인 필요' : `${Math.round(plan.firstRankAcceptability * 100)}%`}
              </span>
            </div>
          </div>
          <p className="trace-note">
            추천 적합도는 현재 조건에서 가격·데이터·속도·혜택 등을 함께 반영한 상대적 적합도입니다.
            상위권 안정성은 가중치를 달리한 300개 시나리오 중 이 요금제가 상위 3위 안에 든 비율이고,
            1위 수용도는 그중 1위가 된 비율입니다. 세 수치 모두 만족도·가입 성공 확률이 아니라 후보
            간 상대 비교 지표입니다.
          </p>
        </div>
      )}

      <div className="trace-sec">
        <div className="section-label">5. 비용 비교 기준</div>
        <p className="trace-note">
          순위의 가격 평가는 <b>지금 내는 월 요금(할인가)</b> 기준입니다. 카드의 총비용은
          할인이 끝난 뒤의 정가까지 합산한 <b>{compareMonths}개월</b> 기준이라 두 숫자가 다를 수 있습니다.
          할인이 끝나면 요금이 오르는 상품은 각 카드에 인상 시점과 인상 후 금액을 함께 표시합니다.
          {estimated && ' *표시는 할인 기간이 공개되지 않아 현재 요금이 유지된다고 가정한 경우입니다.'}
        </p>
      </div>

      <small className="trace-foot">
        {result.dataAsOf} 수집 데이터 기준
        {result.evaluation?.passed
          ? ' · 추천 내용이 원본 데이터와 일치하는지 자동 대조를 마쳤습니다'
          : ' · 자동 대조에서 확인되지 않은 항목이 있어 잠정 결과입니다'}
      </small>
    </details>
  );
}
