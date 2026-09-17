import { PlanItem, RecommendResponse } from '../types';
import { describeProfile } from '../profileText';

/** "왜 이게 1순위인가" 에 답한다.
 *
 *  내부 지표(기대순위·1위 수용도)는 싣지 않는다. 순위 1·2·3 으로 이미 보여준 것을 어려운 말로
 *  반복할 뿐이고, "만족 확률이 아니다" 라는 부인 문장을 달아야 할 만큼 오해를 부른다.
 *  대신 후보들이 실제로 갈린 지점(축별 충족도)과 비용 기준 차이를 보여준다.
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

export function RecommendationTrace({ result }: { result: RecommendResponse }) {
  const trace = result.trace;
  if (!trace?.rankedCount) return null;

  const axes = decidingAxes(result.plans);
  const conditions = describeProfile(trace.rankingProfile ?? result.profile);
  const estimated = result.plans.some((p) => p.costIsEstimate);
  const months = result.plans[0]?.rankingMonths ?? 12;
  const compareMonths = result.plans[0]?.compareMonths ?? 6;

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
                  <td>{months}개월 평균 월요금</td>
                  {result.plans.map((plan) => (
                    <td className="num" key={plan.id}>
                      {plan.rankingAverageFee.toLocaleString()}원{plan.costIsEstimate && '*'}
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

      <div className="trace-sec">
        <div className="section-label">4. 비용을 두 가지로 보는 이유</div>
        <p className="trace-note">
          순위는 할인이 끝난 뒤까지 포함한 <b>{months}개월 평균 월요금</b>으로 매기고, 카드에 적힌
          총비용은 지금 당장 얼마 나가는지 보는 <b>{compareMonths}개월</b> 기준입니다. 그래서 더 싼
          요금제가 아래 순위일 수 있습니다.
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
