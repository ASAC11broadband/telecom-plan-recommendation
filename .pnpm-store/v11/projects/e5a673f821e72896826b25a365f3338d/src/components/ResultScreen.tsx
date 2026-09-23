import { BrandLogo } from './BrandLogo';
import { useState, ReactNode } from 'react';
import { ChatMessage, HistoryRow, PlanItem, RecommendResponse, ScreenType } from '../types';

/** 직전 결과 대비 변동. 백엔드는 매 호출을 독립으로 처리하므로 여기서 계산한다. */
function deltaOf(plan: PlanItem, prev: PlanItem[]) {
  if (prev.length === 0) return null;
  const before = prev.find((p) => p.id === plan.id);
  if (!before) return { text: '신규 진입', changed: true };
  if (before.rank === plan.rank) return { text: '변동 없음', changed: false };
  const arrow = before.rank > plan.rank ? '▲' : '▼';
  return { text: `${arrow} ${before.rank}순위 → ${plan.rank}순위`, changed: true };
}

function conditionCells(result: RecommendResponse) {
  const p = result.profile ?? {};
  const dataCondition = p.data_unlimited
    ? '무제한'
    : p.min_data_gb || p.estimated_monthly_data_gb
      ? `${Math.max(p.min_data_gb ?? 0, p.estimated_monthly_data_gb ?? 0)}GB 이상${p.max_data_gb ? ` · ${p.max_data_gb}GB 이하` : ''}`
      : p.max_data_gb
        ? `${p.max_data_gb}GB 이하`
        : '미지정';
  return [
    ['데이터', dataCondition],
    ['통화', p.voice_unlimited ? '무제한' : p.min_voice_minutes ? `${p.min_voice_minutes}분` : '미지정'],
    ['문자', p.sms_unlimited ? '무제한' : '미지정'],
    ['예산', p.budget_max_won ? `${p.budget_max_won.toLocaleString()}원 이하` : '미지정'],
    ['연령', p.age_condition || '미지정'],
    ['후보군', `${result.candidateCount.toLocaleString()}건`],
  ] as const;
}

function reasonSentences(reason: string) {
  return reason
    .replace(/\r/g, '')
    .replace(/\s*#(?:온라인전용|요금제한정)\b/g, '')
    .split(/\n+|(?<=[.!?。])\s+/)
    .map((line) => line.replace(/^\s*(?:[-*•]+|\d+[.)、])\s*/, '').replace(/^#+\s*/, '').trim())
    .filter((line) => line.length > 12 && !/^추천\s*근거\s*$/i.test(line));
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

export function ResultScreen({
  chat,
  result,
  prevPlans,
  messages,
  history,
  loading,
  error,
  onFollowup,
  onReport,
  onNavigate,
}: {
  chat: ReactNode;
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
  const shell = (content: ReactNode) => <div className="body recommendation-layout"><section className="recommendation-results" aria-label="추천 결과" aria-busy={loading}>{content}</section>{chat}</div>;

  if (loading) {
    return shell(
      <div className="body" style={{ display: 'grid', placeItems: 'center', minHeight: 420 }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
          <div className="spinner" />
          <strong style={{ fontSize: 'var(--fs-13)' }}>조건에 맞는 요금제를 고르는 중입니다</strong>
          <p style={{ fontSize: 'var(--fs-11)', color: 'var(--t3)' }}>
            조건 정리 → 후보 선별 → 리포트 작성 → 검증 순으로 진행합니다. 40초 안팎 걸립니다.
          </p>
        </div>
      </div>
    );
  }

  if (error || !result) {
    return shell(
      <div className="body">
        <div className="card" style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <strong style={{ fontSize: 'var(--fs-14)' }}>
            {error ? '추천을 받지 못했습니다' : '아직 추천 결과가 없습니다'}
          </strong>
          {error && <p style={{ fontSize: 'var(--fs-12)', color: 'var(--t2)' }}>{error}</p>}
          <div>
            <button className="btn btn-primary" onClick={() => onNavigate('s-input')}>
              조건 입력하러 가기
            </button>
          </div>
        </div>
      </div>
    );
  }

  const isUpdate = prevPlans.length > 0 && !result.needsMoreInput;
  const latest = history[0];
  const winner = result.plans.find((plan) => plan.best) ?? result.plans[0];

  if (result.needsMoreInput && result.followupQuestion) {
    return shell(
      <div className="body">
        <div className="row-between" style={{ marginBottom: 14 }}>
          <div>
            <h2 style={{ fontSize: 'var(--fs-20)', fontWeight: 700 }}>조건을 조금만 더</h2>
            <p style={{ fontSize: 'var(--fs-12)', color: 'var(--t2)' }}>
              데이터 사용량이나 월 예산 중 하나는 있어야 {result.totalCount.toLocaleString()}건을 의미
              있게 좁힐 수 있습니다.
            </p>
          </div>
          <button className="btn" onClick={() => onNavigate('s-input')}>
            입력 화면으로
          </button>
        </div>
        <FollowupNotice question={result.followupQuestion} onAnswer={onFollowup} blocking />
      </div>
    );
  }

  return shell(
    <div className="body split">
      <div style={{ flex: 1 }}>
        <div className="row-between" style={{ marginBottom: 14 }}>
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
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn" onClick={() => onNavigate('s-input')}>
              조건 수정
            </button>
            <button className="btn" onClick={() => window.print()}>
              리포트 다운로드
            </button>
          </div>
        </div>

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

        {result.followupQuestion && (
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
          {conditionCells(result).map(([k, v]) => (
            <div className="cond-cell" key={k}>
              <div className="k">{k}</div>
              <div className="v num">{v}</div>
            </div>
          ))}
        </div>

        {result.plans.length === 0 ? (
          <div className="card" style={{ padding: 20, margin: '14px 0', fontSize: 'var(--fs-12)', color: 'var(--t2)' }}>
            조건을 모두 만족하는 요금제가 없습니다. 오른쪽 상담 창에서 예산이나 데이터 조건을 조금 풀어
            다시 물어봐 주세요.
          </div>
        ) : (
          <div className="recommendation-showcase">
            <div className="recommendation-intro"><span>모모플랜 추천 1순위</span><strong>조건에 맞는 요금제를 찾았어요</strong><p>{winner.name}의 핵심 정보와 추천 이유를 먼저 확인해 보세요.</p></div>
            <PlanCard key={winner.id} plan={winner} delta={deltaOf(winner, prevPlans)} onReport={() => onReport(winner.id)} featured />
            {result.plans.length > 1 && <details className="other-recommendations"><summary>다른 추천 요금제 {result.plans.length - 1}개 함께 보기</summary><div className="plan-grid">{result.plans.filter((plan) => plan.id !== winner.id).map((plan) => <PlanCard key={plan.id} plan={plan} delta={deltaOf(plan, prevPlans)} onReport={() => onReport(plan.id)} />)}</div></details>}
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


    </div>
  );
}

function PlanCard({
  plan,
  delta,
  onReport,
  featured = false,
}: {
  plan: PlanItem;
  delta: { text: string; changed: boolean } | null;
  onReport: () => void;
  featured?: boolean;
}) {
  return (
    <div className={`card recommendation-plan-card${featured ? ' featured-plan' : ''}${plan.best ? ' accent' : ''}`}>
      <div className="plan-head">
        <div style={{ display: 'flex', gap: 5, alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', gap: 5 }}>
            <span className={`tag ${plan.best ? 'tag-accent' : 'tag-muted'}`}>{plan.rank}순위</span>
            {plan.best && !delta && <span className="tag tag-green">최적합</span>}
            {delta?.changed && <span className="tag tag-amber">갱신</span>}
          </div>
          <span
            style={{
              fontSize: 'var(--fs-11)',
              fontWeight: 600,
              color: plan.best ? 'var(--accent)' : 'var(--t2)',
            }}
          >
            적합도 {plan.score}
          </span>
        </div>
        {delta && <div className={`delta ${delta.changed ? 'up' : 'flat'}`}>{delta.text}</div>}
        <div className="plan-name">{plan.name}</div>
        <div className="plan-carrier"><BrandLogo carrier={plan.carrier}/></div>
      </div>

      <div className="price-block">
        <div className="line">
          <span style={{ fontSize: 'var(--fs-11)', color: 'var(--t2)' }}>월 실 납부액</span>
          <span>
            <strong className="amt num">{plan.price}</strong>
            <span className="unit">원/월</span>
          </span>
        </div>
        <div className="list">{plan.priceNote}</div>
      </div>

      <div className="spec3">
        <div>
          <div className="k">데이터</div>
          <div className="v num">
            {plan.data}
            {plan.qos !== '-' && <span className="qos"> {plan.qos}</span>}
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

      {plan.hash.length > 0 && (
        <div className="hashline">
          {plan.hash.map((h) => (
            <span className="hash" key={h}>
              {h}
            </span>
          ))}
        </div>
      )}
      <div className="result-benefits">
        <div className="result-benefit-item"><span>통신망 · 가입</span><strong>{plan.carrierType === 'MVNO' ? '알뜰폰' : '통신 3사'} · {plan.networkGen || plan.network || 'LTE/5G'}{plan.isOnlineOnly ? ' · 온라인 전용' : ''}</strong></div>
        <div className="result-benefit-item"><span>요금제 혜택</span><strong>{plan.benefit || '별도 혜택 정보 없음'}</strong></div>
      </div>
      <div className="recommendation-reason"><div className="reason-heading"><span>WHY THIS PLAN</span><strong>이 요금제를 추천한 이유</strong></div><div className="reason-list">{reasonSentences(plan.reason).slice(0, 3).map((line, index) => <div className="reason-list-item" key={`${index}-${line}`}><span>{String(index + 1).padStart(2, '0')}</span><p>{line}</p></div>)}</div></div>
      <div className="total-row">
        <span className="k">{plan.compareMonths}개월 총비용</span>
        <span className="v num">{plan.total}</span>
      </div>
      <div className="plan-foot">
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

function CompareTable({ plans }: { plans: PlanItem[] }) {
  const rows: [string, (p: PlanItem) => string][] = [
    ['월 기본료', (p) => `${p.price}원`],
    ['정가', (p) => `${p.originalPrice.toLocaleString()}원`],
    ['프로모션 기간', (p) => (p.isPromo ? (p.promoMonths ? `${p.promoMonths}개월` : '약정 유지') : '없음')],
    ['데이터', (p) => p.data],
    ['소진 후 속도', (p) => p.qos],
    ['음성통화', (p) => p.call],
    ['문자', (p) => p.sms],
    ['주요 혜택', (p) => p.benefit],
    [`${plans[0].compareMonths}개월 총비용`, (p) => p.total],
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
                  {p.rank}. {p.name}
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
