import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { PlanItem, Profile, RecommendResponse, ScreenType } from '../types';

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

function verdicts(plan: PlanItem, profile: Profile | null) {
  const rows: [string, string][] = [];
  const p = profile ?? {};
  const dataNeed = Math.max(p.min_data_gb ?? 0, p.estimated_monthly_data_gb ?? 0);
  if (p.data_unlimited) rows.push(['데이터 충족도', '무제한 요구 충족']);
  else if (dataNeed)
    rows.push([
      '데이터 충족도',
      `${p.estimated_monthly_data_gb ? '예상 사용량 기준' : '요구량'} ${dataNeed}GB 대비 ${plan.data} 제공`,
    ]);
  if (p.max_data_gb)
    rows.push([
      '데이터 상한',
      `최대 ${p.max_data_gb}GB 조건 대비 ${plan.data} 제공`,
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

function dataDifference(base: PlanItem, target: PlanItem) {
  if (base.dataUnlimited && target.dataUnlimited) return '데이터는 모두 무제한입니다.';
  if (!base.dataUnlimited && target.dataUnlimited) return `데이터가 ${base.data}에서 무제한으로 늘어납니다.`;
  if (base.dataUnlimited && !target.dataUnlimited) return `데이터가 무제한에서 ${target.data}로 줄어듭니다.`;
  const baseGb = base.dataNum ?? 0;
  const targetGb = target.dataNum ?? 0;
  if (targetGb === baseGb) return `데이터 제공량은 ${target.data}로 같습니다.`;
  return targetGb > baseGb
    ? `데이터가 ${base.data}에서 ${target.data}로 늘어납니다.`
    : `데이터가 ${base.data}에서 ${target.data}로 줄어듭니다.`;
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

function referenceRows(current: PlanItem, selected: PlanItem): [string, string][] {
  const feeGap = selected.priceNum - current.priceNum;
  const priceChange = feeGap === 0
    ? '월 요금이 같습니다.'
    : feeGap < 0
      ? `월 ${Math.abs(feeGap).toLocaleString()}원 저렴해집니다.`
      : `월 ${feeGap.toLocaleString()}원 비싸집니다.`;
  const currentBenefits = benefitItems(current);
  const selectedBenefits = benefitItems(selected);
  const benefitChange = selectedBenefits.length === currentBenefits.length
    ? `확인된 혜택 수는 ${selectedBenefits.length}개로 같습니다.`
    : selectedBenefits.length > currentBenefits.length
      ? `확인된 혜택이 ${currentBenefits.length}개에서 ${selectedBenefits.length}개로 늘어납니다.`
      : `확인된 혜택이 ${currentBenefits.length}개에서 ${selectedBenefits.length}개로 줄어듭니다.`;
  return [
    ['월 요금', `${current.price}원 → ${selected.price}원 · ${priceChange}`],
    ['데이터', `${current.data} → ${selected.data} · ${dataDifference(current, selected)}`],
    ['주요 혜택', `${current.benefit} → ${selected.benefit} · ${benefitChange}`],
  ];
}

/** 시안의 "실효 월 비용 시뮬레이션"을 대체한다.
 *  사용량 분산 데이터가 없어 금액 분포는 만들 수 없고, 요금제 스펙으로 판정 가능한 것은
 *  "초과 요금이 날 수 있는가" 뿐이다. 종량 단가는 수집 데이터에 없어 금액은 산정하지 않는다. */
function overageRisks(plan: PlanItem, profile: Profile | null): [string, string, string][] {
  const rows: [string, string, string][] = [];

  if (plan.dataUnlimited) {
    rows.push(['데이터', '무제한 — 초과 요금 없음', 'tag-green']);
  } else if (plan.qos !== '-') {
    rows.push(['데이터 소진 후', `${plan.qos} 속도로 계속 사용 · 추가 요금 없음`, 'tag-green']);
  } else {
    rows.push(['데이터 소진 후', '속도 제어 없음 · 초과분 종량 과금 발생', 'tag-amber']);
  }

  const need = Math.max(profile?.min_data_gb ?? 0, profile?.estimated_monthly_data_gb ?? 0);
  if (need && plan.dataNum !== null && !plan.dataUnlimited && plan.dataNum < need) {
    rows.push(['요구 데이터', `요구 ${need}GB 대비 ${plan.dataNum}GB — 매월 부족`, 'tag-amber']);
  }

  rows.push(
    plan.call === '무제한'
      ? ['음성통화', '무제한 — 초과 요금 없음', 'tag-green']
      : ['음성통화', `기본 ${plan.call} 초과 시 종량 과금`, 'tag-amber']
  );
  rows.push(
    plan.sms === '무제한'
      ? ['문자', '무제한 — 초과 요금 없음', 'tag-green']
      : ['문자', '기본 제공량 초과 시 종량 과금', 'tag-muted']
  );
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
            <button className="btn btn-primary" onClick={() => onNavigate('s-input')}>
              조건 입력하러 가기
            </button>
          </div>
        </div>
      </div>
    );
  }

  const plan = result.plans.find((item) => item.id === selectedPlanId) ?? result.plans[0];
  const alternatives = result.plans.filter((item) => item.id !== plan.id);
  const cost = costRows(plan);
  const meta = [
    plan.network ? `${plan.network}망` : '',
    plan.isOnlineOnly ? '온라인 전용' : '',
    plan.ageCondition ? `${plan.ageCondition} 대상` : '',
    plan.isPromo && plan.promoMonths ? `프로모션 ${plan.promoMonths}개월` : '',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div className="body split">
      <div className="card" style={{ flex: 1 }}>
        <div style={{ padding: '18px 24px', borderBottom: '1px solid var(--border)' }}>
          <div
            style={{ fontSize: 'var(--fs-11)', color: 'var(--t3)', cursor: 'pointer', marginBottom: 8 }}
            onClick={() => onNavigate('s-result')}
          >
            ← 추천 결과로 돌아가기
          </div>
          <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            <span className="tag tag-accent">{plan.rank}순위</span>
            <span className="tag tag-green">적합도 {plan.score}</span>
          </div>
          <h2 style={{ fontSize: 19, fontWeight: 700 }}>{plan.name}</h2>
          <p style={{ fontSize: 'var(--fs-11)', color: 'var(--t3)', marginTop: 4 }}>{meta}</p>
        </div>

        <div className="report-sec">
          <h4>
            <span className="no">01</span>선택 요금제 요약
          </h4>
          <div className="mini-table">
            <div className="r">
              <span>평가 항목</span>
              <span>판정</span>
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
            <span className="no">02</span>비용 산정 내역
          </h4>
          <p>
            비교 기준은 {cost.months}개월입니다.{' '}
            {cost.regularMonths === 0
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
          </div>
          {plan.isPromo && (
            <p style={{ marginTop: 8, fontSize: 'var(--fs-11)', color: 'var(--amber)' }}>
              참고: {plan.promoMonths ? `할인 기간 ${plan.promoMonths}개월이 끝나면` : '할인이 끝나면'} 정가{' '}
              {plan.originalPrice.toLocaleString()}원으로 변경될 수 있습니다.
            </p>
          )}
        </div>

        <div className="report-sec">
          <h4>
            <span className="no">03</span>추천 요금제 선정 이유
          </h4>
          <div className="md">
            <Markdown remarkPlugins={[remarkGfm]}>
              {plan.reason || '선정 사유가 제공되지 않았습니다.'}
            </Markdown>
          </div>
        </div>

        <div className="report-sec">
          <h4>
            <span className="no">04</span>다른 추천 후보와 비교
          </h4>
          {alternatives.length > 0 ? (
            <div className="mini-table">
              <div className="r">
                <span>대안</span>
                <span>선택 요금제와의 차이</span>
              </div>
              {alternatives.map((other) => (
                <div className="r" key={other.id} style={{ alignItems: 'flex-start', gap: 18 }}>
                  <span style={{ flex: '0 0 32%' }}>{other.rank}순위 · {other.name}</span>
                  <span className="v" style={{ textAlign: 'right' }}>{alternativeSummary(plan, other)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p>비교할 다른 추천 후보가 없습니다.</p>
          )}
        </div>

        {result.referencePlan && (
          <div className="report-sec">
            <h4>
              <span className="no">05</span>현재 요금제와 비교
            </h4>
            <p>
              현재 이용 중인 “{result.referencePlan.name}”과 선택한 “{plan.name}”을 비교했습니다.
            </p>
            <div className="mini-table">
              <div className="r">
                <span>비교 항목</span>
                <span>변화</span>
              </div>
              {referenceRows(result.referencePlan, plan).map(([key, value]) => (
                <div className="r" key={key} style={{ gap: 18 }}>
                  <span>{key}</span>
                  <span className="v" style={{ textAlign: 'right' }}>{value}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="report-sec" style={{ borderBottom: 'none' }}>
          <h4>
            <span className="no">06</span>가입 전 확인
          </h4>
          <p>
            할인 기간과 종료 후 정상가, 가입 대상 조건을 통신사 공식 페이지에서 다시 확인해 주세요.
            테더링 제공량은 {plan.tetheringGb !== null ? `${plan.tetheringGb}GB` : '자료에서 확인되지 않았으며'},
            데이터 소진 후 속도는 {plan.qos === '-' ? '확인이 필요합니다' : `${plan.qos}입니다`}.
          </p>
          <p>본 산정은 2026년 8월 21일 수집 데이터 기준이며 프로모션은 사업자 정책에 따라 변경될 수 있습니다.</p>
          {result.evaluation && !result.evaluation.passed && result.evaluation.feedback && (
            <p style={{ fontSize: 'var(--fs-11)', color: 'var(--t3)' }}>
              검증 메모: {result.evaluation.feedback}
            </p>
          )}
        </div>
      </div>

      <div style={{ width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="card">
          <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
              <span className="num" style={{ fontSize: 24, fontWeight: 700 }}>
                {plan.price}
              </span>
              <span style={{ fontSize: 'var(--fs-12)', color: 'var(--t2)' }}>원 / 월</span>
            </div>
            <p style={{ fontSize: 'var(--fs-11)', color: 'var(--t3)', marginTop: 2 }}>
              {plan.priceNote}
            </p>
          </div>
          <div className="spec-row">
            <span className="k">데이터</span>
            <span className="v num">{plan.data}</span>
          </div>
          <div className="spec-row">
            <span className="k">소진 후 속도</span>
            <span className="v">{plan.qos}</span>
          </div>
          <div className="spec-row">
            <span className="k">음성통화</span>
            <span className="v">{plan.call}</span>
          </div>
          <div className="spec-row">
            <span className="k">문자</span>
            <span className="v">{plan.sms}</span>
          </div>
          <div className="spec-row">
            <span className="k">테더링</span>
            <span className="v num">{plan.tetheringGb ? `${plan.tetheringGb}GB` : '미제공'}</span>
          </div>
          <div className="spec-row">
            <span className="k">결합 부가서비스</span>
            <span className="v">{plan.hasAddon ? plan.benefit : '없음'}</span>
          </div>
          <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border)' }}>
            <a
              className="btn btn-primary btn-block"
              href={plan.sourceUrl || '#'}
              target="_blank"
              rel="noreferrer"
              style={{ textAlign: 'center', pointerEvents: plan.sourceUrl ? 'auto' : 'none', opacity: plan.sourceUrl ? 1 : 0.45 }}
            >
              사업자 페이지로 이동
            </a>
          </div>
        </div>

        <div className="card">
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
            <strong style={{ fontSize: 'var(--fs-12)' }}>초과 요금 리스크</strong>
            <p style={{ fontSize: 'var(--fs-10)', color: 'var(--t3)', marginTop: 3 }}>
              요금제 스펙 기준 · 종량 단가는 사업자 고지 확인 필요
            </p>
          </div>
          {overageRisks(plan, result.profile).map(([k, v, tone]) => (
            <div
              className="spec-row"
              key={k}
              style={{ alignItems: 'flex-start', gap: 10 }}
            >
              <span className="k" style={{ flexShrink: 0 }}>{k}</span>
              <span className={`tag ${tone}`} style={{ textAlign: 'right' }}>{v}</span>
            </div>
          ))}
        </div>

        <div className="card">
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
            <strong style={{ fontSize: 'var(--fs-12)' }}>다른 후보</strong>
          </div>
          {result.plans.slice(1).map((other) => (
            <div className="spec-row" key={other.id}>
              <span className="k">
                {other.rank}. {other.name}
              </span>
              <span className="v num">{other.total}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
