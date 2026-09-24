import { PlanItem, Profile, RecommendResponse, ReferenceDelta, ScreenType } from '../types';

function reasonPoints(reason: string) {
  return reason
    .replace(/\r/g, '')
    .replace(/\s*#(?:온라인전용|요금제한정)\b/g, '')
    .split(/\n+|(?<=[.!?。])\s+/)
    .map((line) => line.replace(/^\s*(?:[-*•]+|\d+[.)、])\s*/, '').replace(/^#+\s*/, '').trim())
    .filter((line) => line && !/^추천\s*근거\s*$/i.test(line));
}

/** 비용 표는 LLM 서술이 아니라 요금제 숫자로 직접 계산한다. 돈 얘기는 틀리면 안 된다. */
function costRows(plan: PlanItem) {
  const months = plan.compareMonths;
  const promo = plan.priceRisesAfter ?? months;
  const promoCost = plan.priceNum * promo;
  const regularMonths = months - promo;
  return {
    months,
    promo,
    promoCost,
    regularMonths,
    regularCost: plan.originalPrice * regularMonths,
    total: plan.totalNum,
  };
}

function gb(value: number) {
  return `${Number(value.toFixed(3))}GB`;
}

/** 리포트에서는 월 예상 사용량과 바로 비교할 수 있도록 일 제공량도 월 기준으로 환산한다. */
function reportDataAmount(plan: PlanItem) {
  if (!plan.dailyDataGb || plan.dataNum === null) return plan.data;
  return `월 ${gb(plan.dataNum)}`;
}

function qosForSentence(qos: string) {
  return qos.replace(/^\+/, '');
}

function verdicts(plan: PlanItem, profile: Profile | null) {
  const rows: [string, string][] = [];
  const p = profile ?? {};
  const dataNeed = p.min_data_gb ?? p.target_data_gb ?? p.estimated_monthly_data_gb ?? 0;
  if (p.data_unlimited)
    rows.push([
      '데이터 충족도',
      plan.dataUnlimited
        ? '기본 제공량 무제한'
        : `기본 ${plan.data} + ${plan.dataTierLabel} — 소진 후에도 계속 사용`,
    ]);
  else if (dataNeed)
    rows.push([
      '데이터 충족도',
      `${p.min_data_gb ? '요구량' : '예상 사용량 기준'} ${dataNeed}GB 대비 ${reportDataAmount(plan)} 제공` +
        (plan.qosKnown && !plan.dataUnlimited ? ` · 소진 후 ${qosForSentence(plan.qos)}` : ''),
    ]);
  if (p.max_data_gb)
    rows.push([
      '데이터 상한',
      `최대 ${p.max_data_gb}GB 조건 대비 ${reportDataAmount(plan)} 제공`,
    ]);
  if (p.voice_unlimited) rows.push(['통화 충족도', '통화 무제한 제공']);
  else if (p.min_voice_minutes)
    rows.push(['통화 충족도', `${p.min_voice_minutes}분 요구 대비 ${plan.call} 제공`]);
  if (p.budget_max_won)
    rows.push([
      '예산 적합성',
      `예산 대비 ${(p.budget_max_won - plan.priceNum).toLocaleString()}원 여유`,
    ]);
  rows.push(['가입 조건', plan.ageCondition || '별도 가입 조건 없음']);
  return rows;
}

function benefitItems(plan: PlanItem) {
  return plan.benefit === '부가 혜택 없음'
    ? []
    : plan.benefit.split(' · ').map((item) => item.trim()).filter(Boolean);
}

function dataForSentence(plan: PlanItem) {
  return plan.dailyDataGb ? plan.data.replace(/\/월\s+/, '/월 + ') : plan.data;
}

function dataDifference(base: PlanItem, target: PlanItem) {
  if (base.dataUnlimited && target.dataUnlimited) return '데이터는 모두 무제한입니다.';
  if (!base.dataUnlimited && target.dataUnlimited) return `데이터가 ${dataForSentence(base)}에서 무제한으로 늘어납니다.`;
  if (base.dataUnlimited && !target.dataUnlimited) return `데이터가 무제한에서 ${dataForSentence(target)}로 줄어듭니다.`;
  const baseGb = base.dataNum ?? 0;
  const targetGb = target.dataNum ?? 0;
  if (targetGb === baseGb) return `데이터 제공량은 ${dataForSentence(target)}로 같습니다.`;
  return targetGb > baseGb
    ? `데이터가 ${dataForSentence(base)}에서 ${dataForSentence(target)}로 늘어납니다.`
    : `데이터가 ${dataForSentence(base)}에서 ${dataForSentence(target)}로 줄어듭니다.`;
}

function alternativeSummary(selected: PlanItem, other: PlanItem) {
  const priceGap = Math.abs(other.priceNum - selected.priceNum).toLocaleString();
  const price = other.priceNum === selected.priceNum
    ? '월 요금은 같습니다'
    : other.priceNum < selected.priceNum
      ? `월 ${priceGap}원 더 저렴합니다`
      : `월 ${priceGap}원 더 비쌉니다`;
  const data = dataDifference(selected, other).replace(/^데이터(?:가| 제공량은)\s*/, '데이터는 ');
  const benefits = benefitItems(other);
  const benefit = benefits.length > 0
    ? `주요 혜택은 ${benefits.join(', ')}입니다.`
    : '확인된 부가 혜택은 없습니다.';
  return `${price}. ${data} ${benefit}`;
}

function won(value: number | null | undefined, fallback = '확인 필요') {
  return value === null || value === undefined ? fallback : `${value.toLocaleString()}원`;
}

/** 현재 요금제 대비 변화. 금액은 전부 서버가 준 referenceDelta 를 그대로 쓴다.
 *  화면에서 다시 계산하면 총비용 기준이 서버와 갈라진다(예전에 6개월/12개월이 갈렸다). */
function deltaRows(delta: ReferenceDelta): [string, string][] {
  const monthly = delta.monthlyDiff === null
    ? '차이를 계산하려면 현재 월 납부액이 필요합니다.'
    : delta.monthlyDiff === 0
      ? '초기 월 요금은 같습니다.'
      : `초기 월 요금이 ${Math.abs(delta.monthlyDiff).toLocaleString()}원 ${delta.monthlyDiff < 0 ? '적습니다' : '많습니다'}.`;
  const total = delta.totalDiff === null
    ? '현재 납부액이나 후보의 청구액이 확인되지 않아 총비용을 비교하지 않았습니다.'
    : delta.totalDiff === 0
      ? `${delta.months}개월 총비용이 같습니다.`
      : `${delta.months}개월 총비용이 ${Math.abs(delta.totalDiff).toLocaleString()}원 ${delta.totalDiff < 0 ? '적습니다' : '많습니다'}.`;
  const dataChange = delta.dataDiffGb === null
    ? '한쪽 제공량이 확인되지 않아 증감을 계산하지 않았습니다.'
    : delta.dataDiffGb === 0
      ? '제공량이 같습니다.'
      : `30일 월 환산 기준 약 ${Math.abs(delta.dataDiffGb).toLocaleString()}GB ${delta.dataDiffGb > 0 ? '늘어납니다' : '줄어듭니다'}.`;
  const currentDiscount = delta.currentDiscountEndsAfterMonths == null
    ? null
    : `현재: ${delta.currentDiscountEndsAfterMonths}개월 후 ${won(delta.currentFeeAfterDiscount)}`;
  const candidateDiscount = delta.discountEndsAfterMonths == null
    ? null
    : `후보: ${delta.discountEndsAfterMonths}개월 후 ${won(delta.feeAfterDiscount)}`;
  const discountTiming = [currentDiscount, candidateDiscount].filter(Boolean).join(' / ');
  return [
    ['현재 월 납부액 / 후보 초기 월 요금', `${won(delta.currentMonthlyFee)} → ${won(delta.candidateMonthlyFee)} · ${monthly}`],
    [`${delta.months}개월 총비용`, `${won(delta.currentTotal)} → ${won(delta.candidateTotal)} · ${total}`],
    ['데이터 제공량', `${delta.currentData} → ${delta.candidateData} · ${dataChange}`],
    ['소진 후 속도', `${delta.currentQos} → ${delta.candidateQos}`],
    ['할인 종료 시점', discountTiming
      ? `${discountTiming}으로 오릅니다.`
      : '현재·후보에 비교 구간 안에서 끝나는 요금 할인이 확인되지 않았습니다.'],
  ];
}

/** 월별 청구액 변화. 할인이 끝나는 달에 막대가 뛰는 것을 그대로 보여준다.
 *  차트 라이브러리를 쓰지 않는다 - 값이 12개뿐이라 CSS 막대로 충분하다. */
function FeeChart({ delta }: { delta: ReferenceDelta }) {
  const values = delta.schedule.flatMap((point) => [point.current, point.candidate])
    .filter((value): value is number => value !== null);
  if (!values.length) return null;
  const max = Math.max(...values, 1);
  return (
    <div className="fee-chart" role="img"
         aria-label={`${delta.months}개월 월별 청구액 변화. 현재와 후보를 나란히 비교합니다.`}>
      {delta.schedule.map((point) => (
        <div className="fee-chart-col" key={point.month}>
          <div className="fee-chart-bars">
            <span className="cur" style={{ height: `${((point.current ?? 0) / max) * 100}%` }}
                  title={`${point.month}개월차 현재 ${won(point.current)}`} />
            <span className="cand" style={{ height: `${((point.candidate ?? 0) / max) * 100}%` }}
                  title={`${point.month}개월차 후보 ${won(point.candidate)}`} />
          </div>
          <em>{point.month}</em>
        </div>
      ))}
    </div>
  );
}

/** 시안의 "실효 월 비용 시뮬레이션"을 대체한다.
 *  사용량 분산 데이터가 없어 금액 분포는 만들 수 없고, 요금제 스펙으로 판정 가능한 것은
 *  "초과 요금이 날 수 있는가" 뿐이다. 종량 단가는 수집 데이터에 없어 금액은 산정하지 않는다. */
function overageRisks(plan: PlanItem, profile: Profile | null): [string, string, string][] {
  const rows: [string, string, string][] = [];

  if (plan.dataUnlimited) {
    rows.push(['데이터', '수집 기준 기본량 무제한 · 이용 정책 확인', 'tag-green']);
  } else if (plan.qosKnown) {
    rows.push([
      '데이터 소진 후',
      `${plan.qos}로 계속 사용 가능 · 속도·과금 조건은 사업자 확인 (${plan.dataTierLabel})`,
      plan.dataTier === 'qos_text' ? 'tag-amber' : 'tag-green',
    ]);
  } else {
    // 자료에 없는 것을 '속도 제어 없음'으로 단정하면 종량 과금이 없다고 읽힌다.
    rows.push(['데이터 소진 후', '자료에서 확인되지 않음 · 사업자 고지 확인 필요', 'tag-amber']);
  }

  const need = profile?.min_data_gb ?? profile?.target_data_gb ?? profile?.estimated_monthly_data_gb ?? 0;
  const label = profile?.min_data_gb ? '요구' : '예상 사용량';
  if (need && plan.dataNum !== null && !plan.dataUnlimited && plan.dataNum < need) {
    rows.push([
      '데이터 제공량',
      plan.qosKnown && plan.dataTier !== 'qos_text' && plan.dataTier !== 'capped'
        ? `${label} ${need}GB 대비 ${plan.dataNum}GB — 초과분은 ${plan.qos}로 사용`
        : `${label} ${need}GB 대비 ${plan.dataNum}GB — 매월 부족`,
      plan.qosKnown && plan.dataTier !== 'qos_text' && plan.dataTier !== 'capped'
        ? 'tag-muted'
        : 'tag-amber',
    ]);
  }
  if (plan.dailyDataGb) {
    rows.push(['일 제공량', `하루 ${plan.dailyDataGb}GB 초과 시 속도 제한`, 'tag-muted']);
  }

  rows.push(
    plan.call === '무제한'
      ? ['음성통화', '기본 음성 무제한 · 부가통화 조건 확인', 'tag-green']
      : ['음성통화', `기본 ${plan.call} 초과 시 종량 과금`, 'tag-amber']
  );
  rows.push(
    plan.sms === '무제한'
      ? ['문자', '기본 문자 무제한 · 이용 정책 확인', 'tag-green']
      : ['문자', '기본 제공량 초과 시 종량 과금', 'tag-muted']
  );
  if (plan.priceRisesLater) {
    rows.push([
      '요금 인상',
      `프로모션 ${plan.promoMonths}개월 뒤 월 ${plan.originalPrice.toLocaleString()}원`,
      'tag-amber',
    ]);
  }
  return rows;
}

export function ReportScreen({
  result,
  selectedPlanId,
  onNavigate,
}: {
  result: RecommendResponse | null;
  selectedPlanId: string | null;
  onNavigate: (s: ScreenType) => void;
}) {
  if (!result || result.plans.length === 0) {
    return (
      <div className="body">
        <div className="card" style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <strong style={{ fontSize: 'var(--fs-14)' }}>리포트를 만들 추천 결과가 없습니다.</strong>
          <div>
            <button className="btn btn-primary" onClick={() => onNavigate('s-browse')}>
              전체 요금제에서 찾아보기
            </button>
          </div>
        </div>
      </div>
    );
  }

  const plan = result.plans.find((item) => item.id === selectedPlanId) ?? result.plans[0];
  const alternatives = result.plans.filter((item) => item.id !== plan.id);
  const cost = costRows(plan);
  const reasons = reasonPoints(plan.reason || '');
  // 섹션 번호는 렌더링되는 순서대로 매긴다. 조건부 섹션을 건너뛰면 01·02·03·04·06 이 된다.
  let sectionNo = 0;
  const nextNo = () => String(++sectionNo).padStart(2, '0');
  return (
    <div className="body report-body">
      <div className="report-document">
        <div className="report-compact-header">
          <button className="linklike report-back" onClick={() => onNavigate('s-result')}>← 추천 결과로 돌아가기</button>
          <div>
            <span>{plan.rank}순위 추천 리포트</span>
            <h1>{plan.name}</h1>
          </div>
        </div>

        <div className="report-sec">
          <h4>
            <span className="no">{nextNo()}</span>왜 이 요금제가 잘 맞나요?
          </h4>
          <div className="reason-points report-main-reasons">
            {reasons.slice(0, 3).map((point, index) => <div className="reason-point" key={`${index}-${point}`}><span>{String(index + 1).padStart(2, '0')}</span><p>{point}</p></div>)}
          </div>
          <div className="mini-table">
            <div className="r">
              <span>입력한 조건</span>
              <span>요금제 기준</span>
            </div>
            {verdicts(plan, result.profile).map(([k, v]) => (
              <div className="r" key={k}>
                <span>{k}</span>
                <span className="v">{v}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="report-sec">
          <h4>
            <span className="no">{nextNo()}</span>실제로 얼마를 내나요?
          </h4>
          {cost.total === null ? <p>페이백 반영 표시가입니다. 실제 청구액이 확인될 때까지 비용 계산에서 제외합니다.</p> : <>
          <p>
            비교 기준은 {cost.months}개월입니다.{' '}
            {plan.costIsEstimate ? '할인 기간 미확인으로 현재 가격이 유지된다고 가정했습니다.' : cost.regularMonths === 0
              ? '비교 구간 전체에서 같은 가격이 유지됩니다.'
              : `할인 ${cost.promo}개월이 끝난 뒤 ${cost.regularMonths}개월은 정가로 계산했습니다.`}
          </p>
          <div className="mini-table">
            <div className="r">
              <span>구분</span>
              <span>금액</span>
            </div>
            <div className="r">
              <span>
                할인 가격 ({cost.promo}개월)
              </span>
              <span className="v num">
                {plan.priceNum.toLocaleString()}원 × {cost.promo} = {cost.promoCost.toLocaleString()}원
              </span>
            </div>
            {cost.regularMonths > 0 && (
              <div className="r">
                <span>정가 ({cost.regularMonths}개월)</span>
                <span className="v num">
                  {plan.originalPrice.toLocaleString()}원 × {cost.regularMonths} ={' '}
                  {cost.regularCost.toLocaleString()}원
                </span>
              </div>
            )}
            <div className="r">
              <span>{cost.months}개월 총비용</span>
              <span className="v num">{cost.total.toLocaleString()}원</span>
            </div>
            <div className="r">
              <span>월 환산 실효 요금</span>
              <span className="v num">
                {Math.round(cost.total / cost.months).toLocaleString()}원
              </span>
            </div>
            {plan.benefitValue > 0 && (
              <>
                <div className="r">
                  <span>혜택 월 환산 가치{plan.benefitValueEstimated ? ' (추정)' : ''}</span>
                  <span className="v num">{plan.benefitValue.toLocaleString()}원 / 월</span>
                </div>
              </>
            )}
          </div>
          {plan.benefitValue > 0 && (
            <p style={{ marginTop: 8, fontSize: 'var(--fs-11)', color: 'var(--t3)' }}>
              혜택 월 환산 가치는 납부액 할인이 아닙니다. 해당 서비스를 실제 이용하고 직접
              결제 중일 때만 절약이 되므로 총비용에서 빼지 않았습니다. 금액 미확인 혜택은
              합계에서 제외했습니다.
              {plan.benefitValueEstimated &&
                ' 제공 기간이 확인되지 않은 혜택이 있어 월 환산액은 추정입니다.'}
              {plan.benefitConditionalCount > 0 &&
                ` 카드 실적·별도 가입 같은 조건이 붙은 혜택 ${plan.benefitConditionalCount}건은 차감하지 않았습니다.`}
            </p>
          )}
          {plan.isPromo && (
            <p style={{ marginTop: 8, fontSize: 'var(--fs-11)', color: 'var(--amber)' }}>
              참고: {plan.promoMonths ? `할인 기간 ${plan.promoMonths}개월이 끝나면` : '할인이 끝나면'} 정가{' '}
              {plan.originalPrice.toLocaleString()}원으로 변경될 수 있습니다.
              {plan.priceRisesLater &&
                ` ${cost.months}개월 총비용에는 인상분이 포함되지 않았습니다.`}
            </p>
          )}
          </>}
        </div>

        <div className="report-sec">
          <h4>
            <span className="no">{nextNo()}</span>좋은 점과 확인할 점
          </h4>
          {plan.benefit && plan.benefit !== '부가 혜택 없음' && <div className="reason-benefits"><strong>제공 혜택</strong><div>{benefitItems(plan).map((item) => <span className="hash" key={item}>{item}</span>)}</div></div>}
          <div className="report-check-grid">
            {overageRisks(plan, result.profile).map(([k, v, tone]) => (
              <article key={k}>
                <span>{k}</span>
                <strong>{v}</strong>
                <i className={`tag ${tone}`}>{tone === 'tag-green' ? '조건 충족' : '확인 권장'}</i>
              </article>
            ))}
          </div>
        </div>

        <div className="report-sec">
          <h4>
            <span className="no">{nextNo()}</span>다른 Top 3와 무엇이 다른가요?
          </h4>
          {alternatives.length > 0 ? (
            <div className="mini-table alternative-table">
              <div className="r">
                <span>대안</span>
                <span>선택 요금제와의 차이</span>
              </div>
              {alternatives.map((other) => (
                <div className="r" key={other.id}>
                  <span className="alternative-name">{other.rank}순위 · {other.name}</span>
                  <span className="v alternative-copy">{alternativeSummary(plan, other)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p>비교할 다른 추천 후보가 없습니다.</p>
          )}
        </div>

        {plan.referenceDelta && (
          <div className="report-sec">
            <h4>
              <span className="no">{nextNo()}</span>현재 요금제 대비 변화
            </h4>
            <p>
              {result.referencePlan
                ? `현재 이용 중인 “${result.referencePlan.name}”과 선택한 “${plan.name}”을 비교했습니다.`
                : `지금 쓰고 계신 요금제와 선택한 “${plan.name}”을 비교했습니다. 상품명 대신 알려주신 현재 월 납부액과 데이터량을 기준으로 삼았습니다.`}
            </p>
            <div className="mini-table">
              <div className="r">
                <span>비교 항목</span>
                <span>변화</span>
              </div>
              {deltaRows(plan.referenceDelta).map(([key, value]) => (
                <div className="r" key={key} style={{ gap: 18 }}>
                  <span>{key}</span>
                  <span className="v" style={{ textAlign: 'right' }}>{value}</span>
                </div>
              ))}
            </div>
            <FeeChart delta={plan.referenceDelta} />
            <p className="chart-legend">
              <span className="swatch cur" /> 현재 요금제
              <span className="swatch cand" /> {plan.name}
              <span> · 가로축은 가입 후 개월 수입니다.</span>
            </p>
            <p>{plan.referenceDelta.assumption}</p>
            <p>
              이 비교는 요금만 본 차이이고 확정 절약액이 아닙니다.
              반영하지 못한 항목: {plan.referenceDelta.unknowns.join(', ')}.
            </p>
          </div>
        )}

        <div className="report-sec" style={{ borderBottom: 'none' }}>
          <h4>
            <span className="no">{nextNo()}</span>가입 전 확인
          </h4>
          <p>
            할인 기간과 종료 후 정상가, 가입 대상 조건을 통신사 공식 페이지에서 다시 확인해 주세요.
            테더링 제공량은 {plan.tethering}, 데이터 소진 후 속도는 {plan.qos}입니다.
          </p>
          {plan.signupNotice && <p>사업자 고지: {plan.signupNotice}</p>}
          {plan.dataWarnings.map(warning => <p key={warning}>{warning}</p>)}
          <p>
            본 산정은 {result.dataAsOf} 수집 데이터 기준이며 프로모션은 사업자 정책에 따라 변경될 수 있습니다.
          </p>
          <div className="report-end-actions">
            <button className="btn" onClick={() => onNavigate('s-result')}>다른 추천 보기</button>
            {plan.sourceUrl && <a className="btn btn-primary" href={plan.sourceUrl} target="_blank" rel="noreferrer">가입하러 가기</a>}
          </div>
        </div>
      </div>
    </div>
  );
}
