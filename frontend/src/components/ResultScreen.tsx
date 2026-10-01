import { BrandLogo } from './BrandLogo';
import { RecommendationTrace } from './RecommendationTrace';
import { ReactNode } from 'react';
import { Blocker, PlanItem, Profile, RecommendResponse, ScreenType } from '../types';
import { ConditionKind, dataCondition } from '../profileText';

/** 직전 결과 대비 변동. 백엔드는 매 호출을 독립으로 처리하므로 여기서 계산한다. */
function deltaOf(plan: PlanItem, prev: PlanItem[]) {
  if (prev.length === 0) return null;
  const before = prev.find((p) => p.id === plan.id);
  if (!before) return { text: '신규 진입', changed: true };
  if (before.rank === plan.rank) return { text: '변동 없음', changed: false };
  const arrow = before.rank > plan.rank ? '▲' : '▼';
  return { text: `${arrow} ${before.rank}순위 → ${plan.rank}순위`, changed: true };
}
/** 조건 바. 사용자가 '말한 조건'과 시스템이 '추정한 목표'를 절대 같은 말로 쓰지 않는다.
 *  추정 128.7GB 를 "128.7GB 이상"이라고 쓰면 10GB 요금제를 추천했을 때 화면이 자기모순이 된다.
 *  데이터 조건 문구는 profileText 와 공유한다. 두 곳이 어긋나면 화면끼리 말이 달라진다. */
function conditionCells(result: RecommendResponse) {
  const p = result.profile ?? {};
  return [
    dataCondition(p),
    {
      k: '통화',
      value: p.voice_unlimited ? '무제한' : p.min_voice_minutes ? `${p.min_voice_minutes}분` : '미지정',
      kind: (p.voice_unlimited || p.min_voice_minutes ? '요청' : '') as ConditionKind,
    },
    {
      k: '문자',
      value: p.sms_unlimited ? '무제한' : '미지정',
      kind: (p.sms_unlimited ? '요청' : '') as ConditionKind,
    },
    {
      k: '예산',
      value: p.budget_max_won ? `${p.budget_max_won.toLocaleString()}원 이하` : '미지정',
      kind: (p.budget_max_won ? '요청' : '') as ConditionKind,
    },
    {
      k: '통신 세대',
      value: p.network_gen
        ? `${p.network_gen}만`
        : p.network_preference
          ? `${p.network_preference} 우선 · 두 세대 포함`
          : 'LTE · 5G 함께 비교',
      kind: (p.network_gen || p.network_preference ? '요청' : '기본') as ConditionKind,
    },
    {
      k: '가입 자격',
      value: p.age_condition || (p.user_age ? `만 ${p.user_age}세 기준` : '전용 상품 제외'),
      kind: (p.age_condition || p.user_age ? '요청' : '기본') as ConditionKind,
    },
  ];
}

/** 가격이 오르는 시점을 카드에서 바로 보이게 한다. 총비용만 보면 안 보인다. */
function PromoNote({ plan }: { plan: PlanItem }) {
  if (!plan.isPromo) return null;
  const after = plan.priceRisesAfter
    ? `${plan.priceRisesAfter}개월 뒤`
    : plan.promoMonths
      ? `${plan.promoMonths}개월 뒤`
      : '할인 종료 후';
  return (
    <div className="promo-note">
      {after} 월 {plan.originalPrice.toLocaleString()}원
    </div>
  );
}

/** 0건일 때 "없습니다"로 끝내지 않는다. 어느 조건이 막았고 풀면 몇 건이 되는지 보여주고,
 *  그 조건을 푸는 문장을 바로 보낼 수 있게 한다. */
function EmptyResult({
  blockers,
  onRelaxCondition,
}: {
  blockers: Blocker[];
  onRelaxCondition: (text: string, field: string) => void;
}) {
  return (
    <div className="card" style={{ padding: 20, margin: '14px 0' }}>
      <strong style={{ fontSize: 'var(--fs-13)' }}>조건을 모두 만족하는 요금제가 없습니다</strong>
      {blockers.length === 0 ? (
        <p style={{ fontSize: 'var(--fs-12)', color: 'var(--t2)', marginTop: 8 }}>
          조건 두 개 이상이 동시에 걸려 있습니다. 오른쪽 상담 창에서 예산이나 데이터 조건을 조금 풀어
          다시 물어봐 주세요.
        </p>
      ) : (
        <>
          <p style={{ fontSize: 'var(--fs-12)', color: 'var(--t2)', margin: '8px 0 12px' }}>
            아래 조건 중 하나만 풀면 후보가 생깁니다.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {blockers.map((b) => (
              <div className="notice" key={b.field} style={{ justifyContent: 'space-between' }}>
                <span className="txt">
                  <strong>{b.label}</strong> 조건을 빼면 {b.candidates.toLocaleString()}건
                  {b.minimum_fee !== undefined &&
                    ` · 이 조건들로는 월 ${b.minimum_fee.toLocaleString()}원부터 가능합니다`}
                </span>
                <button
                  className="btn btn-sm"
                  onClick={() => onRelaxCondition(`${b.label} 조건은 빼고 다시 추천해줘`, b.field)}
                >
                  이 조건 풀기
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/** '무제한' 요청에만 뜨는 안내 + 방향성 질문.
 *
 *  사용자가 말한 '무제한'은 두 가지를 가리킬 수 있다 — 기본 제공량 자체가 무제한인 상품과,
 *  제공량을 다 써도 속도가 유지되는 대용량 상품. 어느 쪽인지 코드가 짐작하지 않고 묻는다.
 *  기준 숫자는 서버가 내려준 unlimitedPolicy 를 그대로 쓴다(화면에 상수를 복제하지 않는다).
 */
function UnlimitedBasis({ result, loading, onFollowup }: {
  result: RecommendResponse; loading: boolean; onFollowup: (text: string) => void;
}) {
  if (!result.profile?.data_unlimited) return null;
  const { minGb, qosMbps } = result.unlimitedPolicy;
  const strict = result.profile.require_full_unlimited === true;
  const full = result.plans.filter((plan) => plan.dataUnlimited).length;
  const qosKept = result.plans.length - full;
  return (
    <section className="card result-overview">
      <h3>‘무제한’을 이렇게 봤습니다</h3>
      <p>
        {strict
          ? '기본 제공량 자체가 무제한인 상품만 골랐습니다.'
          : `기본 제공량이 무제한이거나, 제공량 ${minGb.toLocaleString()}GB 이상이면서 소진 후 속도가 ${qosMbps}Mbps 이상으로 유지되는 상품을 ‘무제한’으로 봤습니다.`}
        {' '}이번 추천 {result.plans.length}개 중 기본량 무제한 {full}개, 대용량＋속도 유지 {qosKept}개입니다.
      </p>
      <p className="comparison-note">
        제공량과 소진 후 속도를 함께 보는 기준입니다. 통신 3사는 100GB대＋5Mbps 상품을 무제한으로
        부르지 않아, 알뜰폰도 소진 후 {qosMbps}Mbps 이상만 ‘무제한’으로 봤습니다. 규제가 정한 값이
        아니라 저희가 정한 기준입니다.
      </p>
      <fieldset disabled={loading} className="plain-fieldset">
        <div className="pref-switch">
          <button
            className={`btn btn-sm${strict ? ' btn-primary' : ''}`}
            aria-pressed={strict}
            onClick={() => onFollowup('완전 무제한, 속도 제한 없는 요금제만 추천해줘.')}
          >
            기본 제공량 자체가 무제한인 상품만
          </button>
          <button
            className={`btn btn-sm${strict ? '' : ' btn-primary'}`}
            aria-pressed={!strict}
            onClick={() => onFollowup('완전 무제한이 아니어도 괜찮아. 소진 후 속도가 유지되는 상품도 포함해서 추천해줘.')}
          >
            소진 후 속도가 유지되면 괜찮아요
          </button>
        </div>
      </fieldset>
    </section>
  );
}

/** 선호 축을 바꿔 다시 추천받는 버튼.
 *
 *  새 가중치 공식이나 슬라이더를 만들지 않는다. 기존 후속 질문 경로로 "무엇을 가장
 *  중요하게 볼지"만 한 문장 더 보내고, 가중치 보정은 그대로 agent/mcda.py 의 SMAA-2
 *  우선순위 가산(_boosted)이 한다.
 *
 *  필수 조건은 문장에 다시 적어서 보낸다. 프로파일링은 매 턴 대화 전체에서 조건을 다시
 *  뽑는데, 짧은 후속 문장만 보내면 예산 상한·필수 데이터량이 조용히 빠질 수 있다.
 */
const PRIORITY_CHOICES: { key: string; label: string; phrase: string }[] = [
  { key: 'price', label: '월 요금', phrase: '가격을 가장 중요하게' },
  { key: 'data', label: '데이터 제공량', phrase: '데이터 제공량을 가장 중요하게' },
  { key: 'qos', label: '소진 후 속도', phrase: '데이터 소진 후 속도를 가장 중요하게' },
  { key: 'benefit', label: '부가 혜택', phrase: '부가 혜택을 가장 중요하게' },
];

function keptConditions(profile: Profile | null, includeNetworkPreference = true): string[] {
  const p = profile ?? {};
  const kept: string[] = [];
  const hard = new Set(p.hard_constraints ?? []);
  const has = (field: string, value: unknown) => Boolean(value !== undefined && value !== null && value !== false && value !== '' && hard.has(field));

  if (has('budget_min_won', p.budget_min_won)) kept.push(`월 ${p.budget_min_won!.toLocaleString()}원 이상`);
  if (has('budget_max_won', p.budget_max_won)) kept.push(`월 ${p.budget_max_won!.toLocaleString()}원 이하`);
  if (has('min_data_gb', p.min_data_gb)) kept.push(`데이터 ${p.min_data_gb!.toLocaleString()}GB 이상`);
  if (has('min_monthly_base_data_gb', p.min_monthly_base_data_gb)) kept.push(`월 기본 ${p.min_monthly_base_data_gb!.toLocaleString()}GB 이상`);
  if (has('min_daily_data_gb', p.min_daily_data_gb)) kept.push(`매일 ${p.min_daily_data_gb!.toLocaleString()}GB 이상 제공`);
  if (has('max_data_gb', p.max_data_gb)) kept.push(`데이터 ${p.max_data_gb!.toLocaleString()}GB 이하`);
  if (has('require_full_unlimited', p.require_full_unlimited)) kept.push('속도 제한 없는 완전 무제한만');
  else if (has('data_unlimited', p.data_unlimited)) kept.push('데이터 무제한');
  if (has('min_qos_mbps', p.min_qos_mbps)) kept.push(`소진 후 ${p.min_qos_mbps}Mbps 이상`);
  else if (has('requires_qos', p.requires_qos)) kept.push('데이터 소진 후에도 사용 가능');
  if (has('min_tethering_gb', p.min_tethering_gb)) kept.push(`테더링 ${p.min_tethering_gb!.toLocaleString()}GB 이상`);
  if (has('voice_unlimited', p.voice_unlimited)) kept.push('통화 무제한');
  else if (has('min_voice_minutes', p.min_voice_minutes)) kept.push(`통화 ${p.min_voice_minutes!.toLocaleString()}분 이상`);
  if (has('sms_unlimited', p.sms_unlimited)) kept.push('문자 무제한');
  if (has('carrier_type', p.carrier_type)) kept.push(p.carrier_type === 'MNO' ? '통신 3사만' : '알뜰폰만');
  if (has('host_mno', p.host_mno)) kept.push(`${p.host_mno}망`);
  if (has('mvno_brand', p.mvno_brand)) kept.push(`${p.mvno_brand} 브랜드`);
  if (has('network_gen', p.network_gen)) kept.push(`${p.network_gen}만`);
  if (has('age_condition', p.age_condition)) kept.push(`가입 대상 ${p.age_condition}`);

  const benefits = has('wanted_benefits', p.wanted_benefits) ? p.wanted_benefits ?? [] : [];
  const categories = has('wanted_benefit_categories', p.wanted_benefit_categories)
    ? p.wanted_benefit_categories ?? []
    : [];
  const requestedBenefits = [...benefits, ...categories];
  if (requestedBenefits.length) {
    kept.push(`${requestedBenefits.join(p.benefit_match_mode === 'any' ? ' 또는 ' : ' 및 ')} 혜택 필수`);
  }
  if (has('min_discount_period_months', p.min_discount_period_months)) {
    kept.push(`할인 기간 ${p.min_discount_period_months!.toLocaleString()}개월 이상`);
  }

  // 필터 조건은 아니지만 가입 가능 여부와 직전 정렬 설정도 순위 변경 때 유지한다.
  if (p.user_age) kept.push(`만 ${p.user_age}세 가입 기준`);
  if (p.include_mno && !hard.has('carrier_type')) kept.push('통신 3사도 후보에 포함');
  if (includeNetworkPreference && p.network_preference && !hard.has('network_gen')) {
    kept.push(`${p.network_preference} 우선·반대 세대도 후보에 포함`);
  }
  return kept;
}

function PreferenceSwitch({ profile, loading, onFollowup }: {
  profile: Profile | null; loading: boolean; onFollowup: (text: string) => void;
}) {
  const current = (profile?.priorities ?? []).filter((key) => PRIORITY_CHOICES.some((c) => c.key === key));
  const kept = keptConditions(profile);
  return (
    <div className="ranking-control-row">
      <div className="ranking-control-copy">
        <strong>가장 중요한 항목</strong>
        <p>{current.length
          ? `${current.map((key) => PRIORITY_CHOICES.find((c) => c.key === key)?.label ?? key).join(' → ')} 기준으로 순위를 계산 중입니다.`
          : '가격·데이터·소진 후 속도·혜택을 함께 반영하고 있습니다.'}</p>
      </div>
      <fieldset disabled={loading} className="plain-fieldset">
        <div className="pref-switch">
          {PRIORITY_CHOICES.map((choice) => (
            <button
              key={choice.key}
              className={`btn btn-sm${current[0] === choice.key ? ' btn-primary' : ''}`}
              aria-pressed={current[0] === choice.key}
              onClick={() => onFollowup(
                `${kept.length ? `${kept.join(', ')} 조건은 그대로 두고, ` : ''}${choice.phrase} 봐서 다시 추천해줘.`
              )}
            >
              {choice.label}
            </button>
          ))}
        </div>
      </fieldset>
    </div>
  );
}

const NETWORK_PREFERENCE_CHOICES = [
  { key: '', label: '상관없음' },
  { key: 'LTE', label: 'LTE 우선' },
  { key: '5G', label: '5G 우선' },
];

function NetworkPreferenceSwitch({ profile, loading, onFollowup }: {
  profile: Profile | null; loading: boolean; onFollowup: (text: string) => void;
}) {
  if (profile?.network_gen) return null;
  const current = profile?.network_preference ?? '';
  // 이 버튼은 통신 세대 선호 자체를 바꾸므로 기존 세대 선호는 유지 문장에 넣지 않는다.
  const kept = keptConditions(profile, false);
  const sentence = (generation: string) => generation
    ? `통신 세대 우선순위는 ${generation}로 하고, ${generation === 'LTE' ? '5G' : 'LTE'}도 후보에 포함해줘.`
    : '통신 세대 우선순위는 상관없음으로 하고, LTE와 5G를 동등하게 비교해줘.';

  return (
    <div className="ranking-control-row network-control-row">
      <div className="ranking-control-copy">
        <strong>선호하는 통신 세대</strong>
        <p>{current ? `${current}를 우선하지만 반대 세대도 후보에 남겨둡니다.` : 'LTE와 5G를 함께 비교합니다.'}</p>
      </div>
      <fieldset disabled={loading} className="plain-fieldset">
        <div className="pref-switch">
          {NETWORK_PREFERENCE_CHOICES.map((choice) => (
            <button
              key={choice.key || 'any'}
              className={`btn btn-sm${current === choice.key ? ' btn-primary' : ''}`}
              aria-pressed={current === choice.key}
              onClick={() => onFollowup(
                `${kept.length ? `${kept.join(', ')} 조건은 그대로 두고, ` : ''}${sentence(choice.key)}`
              )}
            >
              {choice.label}
            </button>
          ))}
        </div>
      </fieldset>
    </div>
  );
}

export function ResultScreen({
  chat,
  result,
  prevPlans,
  loading,
  error,
  onFollowup,
  onRankingFollowup,
  onRelaxCondition,
  onReport,
  compare,
  onToggleCompare,
  onNavigate,
}: {
  chat: ReactNode;
  compare: PlanItem[];
  onToggleCompare: (plan: PlanItem) => void;
  result: RecommendResponse | null;
  prevPlans: PlanItem[];
  loading: boolean;
  error: string | null;
  onFollowup: (text: string) => void;
  onRankingFollowup: (text: string) => void;
  onRelaxCondition: (text: string, field: string) => void;
  onReport: (planId: string) => void;
  onNavigate: (s: ScreenType) => void;
}) {
  const shell = (content: ReactNode) => <div className="body recommendation-layout"><section className="recommendation-results" aria-label="추천 결과" aria-busy={loading}>{content}</section>{chat}</div>;

  if (!result) return shell(
    <section className="card empty-state"><h2>{loading ? '조건을 확인하고 있어요' : '추천을 시작해보세요'}</h2>
      {error && <p role="alert">{error}</p>}
      <p>예산이나 데이터 사용량 하나만 알려주면 맞는 요금제를 찾아드릴게요.</p>
      {!loading && <button className="btn btn-primary" onClick={() => onNavigate('s-browse')}>전체 요금제에서 찾아보기</button>}
    </section>
  );

  if (result.needsMoreInput) return shell(
    <section className="card empty-state">
      <span className="tag tag-amber">추가 정보 필요</span>
      <h2>추천 전에 한 가지만 더 알려주세요.</h2>
      <p>{result.followupQuestion || '월 데이터 사용량이나 희망 예산을 알려주세요.'}</p>
      <button className="btn btn-primary" onClick={() => onNavigate('s-browse')}>대화로 돌아가기</button>
    </section>
  );

  const isUpdate = prevPlans.length > 0;
  const visibleConditions = conditionCells(result).filter(({ value }) => value !== '미지정').slice(0, 5);
  const verdict = result.referenceVerdict;
  const verdictTitle = verdict?.status === 'keep'
    ? '기준 요금제보다 확실히 나은 후보는 확인되지 않았어요'
    : verdict?.status === 'switch'
      ? '현재보다 유리한 전환 후보가 있어요'
      : verdict?.status === 'tradeoff'
        ? verdict.goal === 'more_data'
          ? '데이터가 더 많은 후보를 찾았어요'
          : '원하는 개선 후보를 찾았어요'
      : verdict?.missing.length
        ? '판단에 정보가 더 필요해요'
        : result.candidateCount === 0
          ? '비교할 후보가 없어 판단할 수 없어요'
          : '후보의 정보가 부족해 판단할 수 없어요';
  const verdictBadge = verdict?.status === 'keep'
    ? '우위 후보 없음'
    : verdict?.status === 'switch'
      ? `전환 후보 ${(verdict.betterCount ?? result.plans.length).toLocaleString()}개`
      : verdict?.status === 'tradeoff'
        ? '조건별 비교'
      : result.candidateCount === 0 && !verdict?.missing.length ? '비교 후보 없음' : '추가 확인 필요';
  const verdictIcon = verdict?.status === 'keep' ? '–' : verdict?.status === 'switch' ? '↗' : verdict?.status === 'tradeoff' ? '⇄' : '?';
  const referenceName = result.referencePlan?.name || result.profile?.reference_plan_name || '입력한 현재 요금제';

  return shell(
    <div className="result-page">
        <div className="row-between result-header">
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <h2 style={{ fontSize: 'var(--fs-20)', fontWeight: 700 }}>추천 결과</h2><span className="recommendation-complete" role="status">✓ 추천 완료</span>
              {isUpdate && <span className="tag tag-amber">갱신됨</span>}
            </div>
            <p style={{ fontSize: 'var(--fs-12)', color: 'var(--t2)' }}>
              {result.totalCount.toLocaleString()}건 중 {result.candidateCount.toLocaleString()}건을
              후보로 선정하고 상위 {result.plans.length}건을 제시합니다.
            </p>
          </div>
        </div>

        <div className="result-main">
        {(loading || error) && <div className="notice" role="status">{loading ? '새 조건으로 다시 추천 중입니다.' : '새 조건의 추천을 완료하지 못했습니다.'} 아래는 이전 조건의 결과입니다.</div>}
        {result.plans.length > 0 && result.evaluation && !result.evaluation.passed && (
          <div className="notice" role="alert">설명 검증을 통과하지 못한 잠정 결과입니다. 요금·조건은 원문에서 확인해 주세요. </div>
        )}
        {verdict && (
          <section className={`card comparison-verdict comparison-verdict-${verdict.status}`}>
            <div className="comparison-verdict-head">
              <span className="comparison-verdict-icon" aria-hidden="true">{verdictIcon}</span>
              <div>
                <span className="comparison-verdict-eyebrow">기준 요금제 비교 결과</span>
                <h3>{verdictTitle}</h3>
              </div>
              <span className="comparison-verdict-badge">{verdictBadge}</span>
            </div>

            <div className="comparison-verdict-summary">
              <div className="comparison-baseline">
                <span>비교 기준 요금제</span>
                <strong>{referenceName}</strong>
              </div>
              <p>{verdict.reason}</p>
            </div>

            {verdict.missing.length > 0 && (
              <div className="comparison-missing">
                <strong>더 정확히 비교하려면</strong>
                <span>{verdict.missing.join(', ')}을(를) 알려주세요.</span>
              </div>
            )}

            {verdict.confirm.length > 0 && (
              <div className="comparison-checks">
                <strong>전환 전에 확인해 주세요</strong>
                <ul>
                  {verdict.confirm.map((note) => (
                    <li key={note}><span aria-hidden="true">!</span><p>{note}</p></li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}
        {result.plans.length === 0 ? (
          <fieldset disabled={loading} className="plain-fieldset"><EmptyResult blockers={result.blockers} onRelaxCondition={onRelaxCondition} /></fieldset>
        ) : (
          <div className="recommendation-showcase">
            <div className="recommendation-intro">
              <span>모모플랜 추천</span>
              <strong>조건에 맞는 요금제 {result.plans.length}개를 찾았어요</strong>
              <p>월 요금과 제공 조건을 비교한 뒤, 궁금한 상품의 상세 리포트를 확인해 보세요.</p>
              {visibleConditions.length > 0 && <div className="recommendation-condition-chips">
                {visibleConditions.map(({ k, value }) => <span key={k}><b>{k}</b>{value}</span>)}
              </div>}
            </div>
            <div className="recommendation-plan-list">
              {result.plans.map((plan) => <PlanCard key={plan.id} plan={plan} delta={deltaOf(plan, prevPlans)} onReport={() => onReport(plan.id)} saved={compare.some(item => item.id === plan.id)} onSave={() => onToggleCompare(plan)} />)}
            </div>
          </div>
        )}

        {result.plans.length > 0 && <details className="card recommendation-settings" id="ranking-controls">
          <summary>
            <span><strong>추천 순서 바꾸기</strong><small>더 중요하게 보는 기준을 선택하면 같은 조건으로 다시 추천해요.</small></span>
          </summary>
          <div className="recommendation-settings-body">
            <UnlimitedBasis result={result} loading={loading} onFollowup={onFollowup} />

            <section className="card ranking-controls">
              <div className="ranking-controls-head">
                <strong>무엇을 우선해서 볼까요?</strong>
                <span>선택한 항목을 더 중요하게 반영해 Top 3를 다시 계산합니다.</span>
              </div>
              <PreferenceSwitch profile={result.profile} loading={loading} onFollowup={onRankingFollowup} />
              <NetworkPreferenceSwitch profile={result.profile} loading={loading} onFollowup={onRankingFollowup} />
            </section>
          </div>
        </details>}

        {result.plans.length > 0 && <RecommendationTrace result={result} />}

        </div>
    </div>
  );
}

function PlanCard({
  plan,
  delta,
  saved,
  onSave,
  onReport,
}: {
  plan: PlanItem;
  delta: { text: string; changed: boolean } | null;
  saved: boolean;
  onSave: () => void;
  onReport: () => void;
}) {
  const benefits = [...new Set(plan.benefits.filter(Boolean))];
  return (
    <div className={`card recommendation-plan-card${plan.best ? ' accent' : ''}`}>
      <div className="plan-head">
        <div style={{ display: 'flex', gap: 5, alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', gap: 5 }}>
            <span className={`tag ${plan.best ? 'tag-accent' : 'tag-muted'}`}>{plan.rank}순위</span>
            {plan.best && !delta && <span className="tag tag-green">종합 추천</span>}
            {delta?.changed && <span className="tag tag-amber">갱신</span>}
            {plan.networkGen && <span className="tag tag-muted">{plan.networkGen}</span>}
          </div>
          <span className={`tier-chip tier-${plan.dataTier}`}>{plan.dataTierLabel}</span>
        </div>
        {delta && <div className={`delta ${delta.changed ? 'up' : 'flat'}`}>{delta.text}</div>}
        <div className="plan-name">{plan.name}</div>
        <div className="plan-carrier"><BrandLogo carrier={plan.carrier}/></div>
      </div>

      <div className="price-block">
        <div className="line">
          <span style={{ fontSize: 'var(--fs-11)', color: 'var(--t2)' }}>{plan.billingPriceKnown ? '월 요금' : '페이백 반영 표시가'}</span>
          <span>
            <strong className="amt num">{plan.price}</strong>
            <span className="unit">원/월</span>
          </span>
        </div>
        <div className="list">{plan.priceNote}</div>
        <PromoNote plan={plan} />
      </div>

      <div className="spec3">
        <div>
          <div className="k">데이터</div>
          <div className="v num">
            {plan.data}
            {plan.qosKnown && <span className="qos"> {plan.qos}</span>}
          </div>
        </div>
        <div>
          <div className="k">음성통화</div>
          <div className="v">{plan.call}</div>
        </div>
        <div>
          <div className="k">문자</div>
          <div className="v">{plan.sms}</div>
        </div>
      </div>

      {benefits.length > 0 && (
        <details className="tile-benefits recommendation-benefits">
          <summary>부가혜택 보기 <span>{benefits.length}</span></summary>
          <ul>{benefits.map((benefit) => <li key={benefit}>{benefit}</li>)}</ul>
        </details>
      )}
      {plan.dataWarnings.map(warning => <div className="promo-note" key={warning}>{warning}</div>)}
      {plan.costIsEstimate && <div className="promo-note">할인 기간 미확인 · 아래 비용은 현재가 유지 가정</div>}
      <div className="plan-foot">
        <button className="btn" aria-pressed={saved} onClick={onSave}>{saved ? '담기 취소' : '비교함에 담기'}</button>
        <button className={`btn${plan.best ? ' btn-primary' : ''}`} style={{ flex: 1 }} onClick={onReport}>
          상세 리포트 보기
        </button>
        {plan.sourceUrl && (
          <a className="btn" href={plan.sourceUrl} target="_blank" rel="noreferrer">
            가입하러 가기
          </a>
        )}
      </div>
    </div>
  );
}
