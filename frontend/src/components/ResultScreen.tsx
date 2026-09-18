import { useState } from 'react';
import { Blocker, ChatMessage, HistoryRow, PlanItem, Profile, RecommendResponse, ScreenType } from '../types';
import { Conversation } from './Conversation';
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
      k: '가입 자격',
      value: p.age_condition || (p.user_age ? `만 ${p.user_age}세 기준` : '전용 상품 제외'),
      kind: (p.age_condition || p.user_age ? '요청' : '기본') as ConditionKind,
    },
    { k: '후보군', value: `${result.candidateCount.toLocaleString()}건`, kind: '' as ConditionKind },
  ];
}

const CRITERIA_LABELS: [string, string][] = [
  ['price', '가격'],
  ['data', '데이터'],
  ['qos', '소진 후 속도'],
  ['benefit', '혜택'],
  ['voice', '통화'],
  ['tethering', '테더링'],
];

/** 추천 후보 사이에서 값이 갈리는 축만 고른다. 전부 같은 값인 막대는 읽을 이유가 없다. */
function varyingCriteria(plans: PlanItem[]): string[] {
  return CRITERIA_LABELS.map(([key]) => key).filter((key) => {
    const values = plans.map((p) => p.criteriaFit[key]).filter((v) => v !== undefined);
    return values.length > 0 && Math.max(...values) - Math.min(...values) > 0.01;
  });
}

/** 총점 대신 축별 충족도를 보여준다. 후보가 2천 건이면 총점은 상위권이 전부 100 으로 포화한다. */
function FitBars({ fit, keys }: { fit: Record<string, number>; keys: string[] }) {
  const rows = CRITERIA_LABELS.filter(([key]) => keys.includes(key) && fit[key] !== undefined);
  if (rows.length === 0) return null;
  return (
    <div className="fitbars">
      {rows.map(([key, label]) => (
        <div className="fitbar" key={key}>
          <span className="lbl">{label}</span>
          <span className="track">
            <span className="fill" style={{ width: `${Math.round(fit[key] * 100)}%` }} />
          </span>
          <span className="pct num">{Math.round(fit[key] * 100)}</span>
        </div>
      ))}
    </div>
  );
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
      {plan.priceRisesAfter ? ' (비교 구간 안에서 인상)' : ''}
    </div>
  );
}

function FollowupNotice({
  question,
  onAnswer,
  blocking,
}: {
  question: string;
  onAnswer: (text: string) => void;
  blocking: boolean;
}) {
  const [answer, setAnswer] = useState('');
  return (
    <div className="notice" style={{ alignItems: 'flex-start', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="tag tag-amber" style={{ background: '#fff' }}>
          {blocking ? '추가 정보 필요' : '확인 질문'}
        </span>
        <span className="txt">{question}</span>
      </div>
      <form
        style={{ display: 'flex', gap: 8, width: '100%' }}
        onSubmit={(e) => {
          e.preventDefault();
          if (!answer.trim()) return;
          onAnswer(answer.trim());
          setAnswer('');
        }}
      >
        <input
          type="text"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          placeholder={blocking ? '예: 20GB 정도 / 3만원 이하' : '답변을 입력하면 순위를 다시 매깁니다'}
          autoFocus={blocking}
        />
        <button className="btn btn-primary" type="submit" disabled={!answer.trim()}>
          전송
        </button>
      </form>
    </div>
  );
}

/** 0건일 때 "없습니다"로 끝내지 않는다. 어느 조건이 막았고 풀면 몇 건이 되는지 보여주고,
 *  그 조건을 푸는 문장을 바로 보낼 수 있게 한다. */
function EmptyResult({
  blockers,
  onFollowup,
}: {
  blockers: Blocker[];
  onFollowup: (text: string) => void;
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
                  onClick={() =>
                    onFollowup(
                      b.minimum_fee !== undefined
                        ? `예산을 ${b.minimum_fee.toLocaleString()}원까지 올릴게요`
                        : `${b.label} 조건은 빼고 다시 추천해줘`
                    )
                  }
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
        제공량과 소진 후 속도를 함께 보는 기준입니다. 속도만 보면 소량 요금제가, 제공량만 보면
        소진 뒤 문자만 되는 상품이 섞입니다. {minGb.toLocaleString()}GB는 수집 데이터에서 소진 후
        속도가 한 단계 올라가는 경계이고, 규제가 정한 값이 아니라 저희가 정한 기준입니다.
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
  { key: 'price', label: '가격 우선', phrase: '가격을 가장 중요하게' },
  { key: 'data', label: '데이터 우선', phrase: '데이터 제공량을 가장 중요하게' },
  { key: 'qos', label: '소진 후 속도 우선', phrase: '데이터 소진 후 속도를 가장 중요하게' },
  { key: 'benefit', label: '혜택 우선', phrase: '부가 혜택을 가장 중요하게' },
];

function keptConditions(profile: Profile | null): string[] {
  const p = profile ?? {};
  const kept: string[] = [];
  if (p.budget_max_won) kept.push(`월 ${p.budget_max_won.toLocaleString()}원 이하`);
  if (p.min_data_gb) kept.push(`데이터 ${p.min_data_gb.toLocaleString()}GB 이상`);
  if (p.data_unlimited) kept.push('데이터 무제한');
  if (p.min_voice_minutes) kept.push(`통화 ${p.min_voice_minutes.toLocaleString()}분 이상`);
  if (p.voice_unlimited) kept.push('통화 무제한');
  if (p.min_qos_mbps) kept.push(`소진 후 ${p.min_qos_mbps}Mbps 이상`);
  if (p.user_age) kept.push(`만 ${p.user_age}세`);
  return kept;
}

function PreferenceSwitch({ profile, loading, onFollowup }: {
  profile: Profile | null; loading: boolean; onFollowup: (text: string) => void;
}) {
  const current = (profile?.priorities ?? []).filter((key) => PRIORITY_CHOICES.some((c) => c.key === key));
  const kept = keptConditions(profile);
  return (
    <section className="card result-overview">
      <h3>무엇을 더 중요하게 볼까요</h3>
      <p>
        {current.length
          ? `지금 순위는 ${current.map((key) => PRIORITY_CHOICES.find((c) => c.key === key)?.label ?? key).join(' → ')} 기준입니다.`
          : '지금은 특정 축을 우선하지 않고 전체를 함께 보고 있습니다.'}
      </p>
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
      <p className="comparison-note">
        {kept.length
          ? `필수 조건(${kept.join(', ')})은 그대로 두고 순위만 다시 계산합니다.`
          : '필수 조건은 그대로 두고 순위만 다시 계산합니다.'}{' '}
        대화 내용과 비교함은 유지됩니다.
      </p>
    </section>
  );
}

export function ResultScreen({
  result,
  prevPlans,
  messages,
  history,
  loading,
  error,
  onFollowup,
  onReport,
  compare,
  onToggleCompare,
  onNavigate,
}: {
  compare: PlanItem[];
  onToggleCompare: (plan: PlanItem) => void;
  result: RecommendResponse | null;
  prevPlans: PlanItem[];
  messages: ChatMessage[];
  history: HistoryRow[];
  loading: boolean;
  error: string | null;
  onFollowup: (text: string) => void;
  onReport: (planId: string) => void;
  onNavigate: (s: ScreenType) => void;
}) {
  if (!result) return <div className="body split">
    <div className="card empty-state" style={{ flex: 1 }}><h2>{loading ? '나에게 맞는 요금제를 찾고 있어요' : '추천을 시작해보세요'}</h2>
      <p>대화는 오른쪽에서 계속 확인할 수 있습니다. 추천 결과가 나오면 요금과 제공량을 한눈에 비교하세요.</p>
      {!loading && <button className="btn" onClick={() => onNavigate('s-input')}>조건 입력하기</button>}
    </div>
    <Conversation messages={messages} loading={loading} error={error} onSubmit={onFollowup} />
  </div>;

  const isUpdate = prevPlans.length > 0 && !result.needsMoreInput;
  const latest = history[0];
  const fitKeys = varyingCriteria(result.plans);
  const prices = result.plans.map(plan => plan.priceNum);
  const cheapest = result.plans.filter(p => p.totalNum !== null).reduce<PlanItem | null>((best, plan) => !best || plan.totalNum! < best.totalNum! ? plan : best, null);

  return (
    <div className="body split">
      <div style={{ flex: 1 }}>
        <div className="row-between" style={{ marginBottom: 14 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <h2 style={{ fontSize: 'var(--fs-20)', fontWeight: 700 }}>추천 결과</h2>
              {isUpdate && <span className="tag tag-amber">갱신됨</span>}
            </div>
            <p style={{ fontSize: 'var(--fs-12)', color: 'var(--t2)' }}>
              {result.totalCount.toLocaleString()}건 중 {result.candidateCount.toLocaleString()}건을
              후보로 선정하고 상위 {result.plans.length}건을 제시합니다.
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn" onClick={() => onNavigate('s-input')}>
              조건 수정
            </button>
            <button className="btn" onClick={() => window.print()}>
              인쇄 / PDF 저장
            </button>
          </div>
        </div>

        {(loading || error) && <div className="notice" role="status">{loading ? '새 조건으로 다시 추천 중입니다.' : '새 조건의 추천을 완료하지 못했습니다.'} 아래는 이전 조건의 결과입니다.</div>}
        {result.plans.length > 0 && result.evaluation && !result.evaluation.passed && (
          <div className="notice" role="alert">설명 검증을 통과하지 못한 잠정 결과입니다. 요금·조건은 원문에서 확인해 주세요. </div>
        )}
        {result.referenceVerdict && (
          <section className="card result-overview">
            <h3>
              {result.referenceVerdict.status === 'keep'
                ? '현재 수준을 유지하려면 기존 요금제도 고려하세요'
                : result.referenceVerdict.status === 'switch'
                  ? '확인된 조건에서 유리한 후보가 있습니다'
                  : '지금이 유리한지 판단하지 못했습니다'}
            </h3>
            <p>{result.referenceVerdict.reason}</p>
            {result.referenceVerdict.missing.length > 0 && (
              <p>
                {result.referenceVerdict.missing.join(', ')}을(를) 알려주시면 현재 요금제와 다시 비교해
                드리겠습니다.
              </p>
            )}
            <ul className="comparison-note" style={{ margin: '8px 0 0', paddingLeft: 18 }}>
              {result.referenceVerdict.confirm.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </section>
        )}
        {result.plans.length > 0 && <section className="card result-overview">
          <h3>이번 추천 한눈에 보기</h3>
          <p>월 요금 {Math.min(...prices).toLocaleString()}~{Math.max(...prices).toLocaleString()}원 · 추천 {result.plans.length}개</p>
          {cheapest && <p>추천 후보 중 {cheapest.compareMonths}개월 총비용이 가장 낮은 상품은 <strong>{cheapest.name}</strong> ({cheapest.total})입니다.{cheapest.costIsEstimate ? ' 할인 기간 미확인으로 추정한 비용입니다.' : ''}</p>}
          {result.plans[0].rankingMonths !== result.plans[0].compareMonths && <p className="comparison-note">추천 순위의 가격 평가는 {result.plans[0].rankingMonths}개월 평균요금, 아래 총비용 비교는 {result.plans[0].compareMonths}개월 기준입니다.</p>}
          <p className="comparison-note">순위는 가격·데이터·혜택 등을 함께 고려한 상대 평가입니다. 만족 확률이나 가입 적합도 백분율이 아닙니다. 마음에 드는 상품은 비교함에 담아 직접 찾은 상품과 비교하세요.</p>
        </section>}

        {result.plans.length > 0 && (
          <UnlimitedBasis result={result} loading={loading} onFollowup={onFollowup} />
        )}

        {result.plans.length > 0 && (
          <PreferenceSwitch profile={result.profile} loading={loading} onFollowup={onFollowup} />
        )}

        {isUpdate && latest && (
          <div className="notice">
            <span className="tag tag-amber" style={{ background: '#fff' }}>
              조건 변경
            </span>
            <span className="txt">
              “{latest.change}” 조건으로 후보군을 재산정했습니다. {latest.before} → {latest.after} ·{' '}
              {latest.result}
            </span>
          </div>
        )}

        {!loading && result.followupQuestion && (
          <FollowupNotice question={result.followupQuestion} onAnswer={onFollowup} blocking={false} />
        )}

        {result.profile?.estimated_monthly_data_gb &&
          result.profile.usage_estimate_notes &&
          result.profile.usage_estimate_notes.length > 0 && (
            <div className="notice" style={{ alignItems: 'flex-start', gap: 10 }}>
              <span className="tag tag-green" style={{ background: '#fff', flexShrink: 0 }}>
                사용량 추정
              </span>
              <div className="txt">
                <strong>
                  {result.profile.smartchoice_usage_pattern ? '추천 데이터 기준' : '월 예상 사용량'}{' '}
                  {result.profile.estimated_monthly_data_gb.toLocaleString()}GB
                </strong>
                <div style={{ marginTop: 4, color: 'var(--t3)', lineHeight: 1.6 }}>
                  {result.profile.usage_estimate_notes.map((note) => (
                    <div key={note}>· {note}</div>
                  ))}
                </div>
              </div>
            </div>
          )}

        <div className="card cond-bar">
          {conditionCells(result).map(({ k, value, kind }) => (
            <div className="cond-cell" key={k}>
              <div className="k">
                {k}
                {kind && <span className={`kind ${kind === '추정' ? 'guess' : ''}`}>{kind}</span>}
              </div>
              <div className="v num">{value}</div>
            </div>
          ))}
        </div>

        {result.plans.length === 0 ? (
          !result.needsMoreInput && <fieldset disabled={loading} className="plain-fieldset"><EmptyResult blockers={result.blockers} onFollowup={onFollowup} /></fieldset>
        ) : (
          <div className="plan-grid">
            {result.plans.map((plan) => (
              <PlanCard
                key={plan.id}
                plan={plan}
                delta={deltaOf(plan, prevPlans)}
                fitKeys={fitKeys}
                onReport={() => onReport(plan.id)}
                saved={compare.some(item => item.id === plan.id)}
                onSave={() => onToggleCompare(plan)}
              />
            ))}
          </div>
        )}

        {result.plans.length > 1 && <CompareTable plans={result.plans} />}


        {history.length > 0 && (
          <div className="card" style={{ marginTop: 14 }}>
            <div
              className="row-between"
              style={{ padding: '11px 14px', borderBottom: '1px solid var(--border)' }}
            >
              <strong style={{ fontSize: 'var(--fs-13)' }}>변경 이력</strong>
              <span style={{ fontSize: 'var(--fs-11)', color: 'var(--t3)' }}>
                총 {history.length}회 산정
              </span>
            </div>
            <table>
              <thead>
                <tr>
                  <th>시각</th>
                  <th>입력</th>
                  <th>이전</th>
                  <th>변경</th>
                  <th>결과</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row, i) => (
                  <tr key={i}>
                    <td>{row.time}</td>
                    <td>{row.change}</td>
                    <td className="num">{row.before}</td>
                    <td className="num">{row.after}</td>
                    <td>{row.result}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Conversation messages={messages} loading={loading} error={error} onSubmit={onFollowup} />
    </div>
  );
}

function PlanCard({
  plan,
  delta,
  fitKeys,
  saved,
  onSave,
  onReport,
}: {
  plan: PlanItem;
  delta: { text: string; changed: boolean } | null;
  fitKeys: string[];
  saved: boolean;
  onSave: () => void;
  onReport: () => void;
}) {
  return (
    <div className={`card${plan.best ? ' accent' : ''}`}>
      <div className="plan-head">
        <div style={{ display: 'flex', gap: 5, alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', gap: 5 }}>
            <span className={`tag ${plan.best ? 'tag-accent' : 'tag-muted'}`}>{plan.rank}순위</span>
            {plan.best && !delta && <span className="tag tag-green">종합 추천</span>}
            {delta?.changed && <span className="tag tag-amber">갱신</span>}
          </div>
          <span className={`tier-chip tier-${plan.dataTier}`}>{plan.dataTierLabel}</span>
        </div>
        {delta && <div className={`delta ${delta.changed ? 'up' : 'flat'}`}>{delta.text}</div>}
        <div className="plan-name">{plan.name}</div>
        <div className="plan-carrier">{plan.carrier}</div>
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

      <details className="score-details"><summary>항목별 비교 점수</summary>
        <p>후보 비교를 위한 항목별 효용값을 0~100으로 표시합니다. 충족률이나 만족 확률이 아니며, 점수가 높은 항목을 더 유리하게 평가합니다.</p>
        <FitBars fit={plan.criteriaFit} keys={fitKeys} />
      </details>

      {plan.hash.length > 0 && (
        <div className="hashline">
          {plan.hash.map((h) => (
            <span className="hash" key={h}>
              {h}
            </span>
          ))}
        </div>
      )}
      <div className="benefit">{plan.reason || plan.benefit}</div>
      {plan.dataWarnings.map(warning => <div className="promo-note" key={warning}>{warning}</div>)}
      {plan.costIsEstimate && <div className="promo-note">할인 기간 미확인 · 아래 비용은 현재가 유지 가정</div>}
      <div className="total-row">
        <span className="k">{plan.compareMonths}개월 총비용</span>
        <span className="v num">{plan.total}</span>
      </div>
      {plan.benefitDeductible > 0 && (
        <div className="total-row sub">
          <span className="k">현금성 혜택 차감 참고값</span>
          <span className="v num">
            {plan.benefitExceedsFee ? '0원 (혜택이 요금 초과)' : plan.effectiveTotal}
          </span>
        </div>
      )}
      <div className="plan-foot">
        <button className="btn" aria-pressed={saved} onClick={onSave}>{saved ? '담기 취소' : '비교함에 담기'}</button>
        <button className={`btn${plan.best ? ' btn-primary' : ''}`} style={{ flex: 1 }} onClick={onReport}>
          추천 근거 보기
        </button>
        {plan.sourceUrl && (
          <a className="btn" href={plan.sourceUrl} target="_blank" rel="noreferrer">
            상세
          </a>
        )}
      </div>
    </div>
  );
}

export function CompareTable({ plans }: { plans: PlanItem[] }) {
  const rows: [string, (p: PlanItem) => string][] = [
    ['사업자 / 망', (p) => p.carrier],
    ['월 요금 / 표시가', (p) => `${p.price}원${p.billingPriceKnown ? '' : ' (페이백 반영)'}`],
    ['정가', (p) => p.billingPriceKnown ? `${p.originalPrice.toLocaleString()}원` : '청구액 확인 필요'],
    ['프로모션 기간', (p) => !p.billingPriceKnown ? '청구 조건 확인 필요' : (p.isPromo ? (p.promoMonths ? `${p.promoMonths}개월` : '확인 필요') : '없음')],
    ['데이터', (p) => p.data],
    ['소진 후', (p) => p.dataTierLabel],
    ['소진 후 속도', (p) => p.qos],
    ['음성통화', (p) => p.call],
    ['문자', (p) => p.sms],
    ['테더링', (p) => p.tethering],
    ['가입 조건', (p) => p.ageCondition || '수집된 제한 없음'],
    ['비용 계산 안내', (p) => !p.billingPriceKnown ? '실제 청구액 미확인 · 계산 제외' : p.costIsEstimate ? '할인 기간 미확인 · 현재가 유지 가정' : `${p.compareMonths}개월 기준`],
    ['주요 혜택', (p) => p.benefit],
    ['혜택 월 환산 (참고값)', (p) => (p.benefitValue ? `${p.benefitValue.toLocaleString()}원${p.benefitValueEstimated ? ' (추정)' : ''}` : '확인 필요')],
    [`${plans[0].compareMonths}개월 총비용`, (p) => p.total],
    [`${plans[0].compareMonths}개월 현금성 혜택 차감 참고값`, (p) => (p.benefitDeductible ? p.effectiveTotal : '해당 없음')],
  ];

  return (
    <div className="card">
      <div className="row-between" style={{ padding: '11px 14px', borderBottom: '1px solid var(--border)' }}>
        <strong style={{ fontSize: 'var(--fs-13)' }}>항목별 상세 비교</strong>
        <span style={{ fontSize: 'var(--fs-11)', color: 'var(--t3)' }}>
          {plans[0].compareMonths}개월 기준 · 프로모션 가격 반영
        </span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th>항목</th>
              {plans.map((p) => (
                <th key={p.id}>
                  {p.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, get]) => (
              <tr key={label}>
                <td>{label}</td>
                {plans.map((p) => (
                  <td className="num" key={p.id}>
                    {get(p)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
