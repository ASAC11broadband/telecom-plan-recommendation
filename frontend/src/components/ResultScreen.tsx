import { useState } from 'react';
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
  return [
    ['데이터', p.data_unlimited ? '무제한' : p.min_data_gb ? `${p.min_data_gb}GB` : '미지정'],
    ['통화', p.voice_unlimited ? '무제한' : p.min_voice_minutes ? `${p.min_voice_minutes}분` : '미지정'],
    ['문자', p.sms_unlimited ? '무제한' : '미지정'],
    ['예산', p.budget_max_won ? `${p.budget_max_won.toLocaleString()}원 이하` : '미지정'],
    ['연령', p.age_condition || '미지정'],
    ['후보군', `${result.candidateCount.toLocaleString()}건`],
  ] as const;
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
  result,
  prevPlans,
  messages,
  history,
  loading,
  error,
  onFollowup,
  onNavigate,
}: {
  result: RecommendResponse | null;
  prevPlans: PlanItem[];
  messages: ChatMessage[];
  history: HistoryRow[];
  loading: boolean;
  error: string | null;
  onFollowup: (text: string) => void;
  onNavigate: (s: ScreenType) => void;
}) {
  const [text, setText] = useState('');
  const [expanded, setExpanded] = useState(false);

  if (loading) {
    return (
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
    return (
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

  if (result.needsMoreInput && result.followupQuestion) {
    return (
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
          <div className="plan-grid">
            {result.plans.map((plan) => (
              <PlanCard
                key={plan.id}
                plan={plan}
                delta={deltaOf(plan, prevPlans)}
                onReport={() => onNavigate('s-report')}
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

      <div className="card chat-panel">
        <div className="panel-head">
          <strong style={{ fontSize: 'var(--fs-13)' }}>상담 세션</strong>
          <span style={{ fontSize: 'var(--fs-11)', color: 'var(--t3)' }}>
            {result.evaluation?.passed ? '검증 통과' : '검증 미달'}
          </span>
        </div>
        <div className="chat-collapsed-row" onClick={() => setExpanded((v) => !v)} style={{ cursor: 'pointer' }}>
          <span>이용 패턴 입력 대화 {messages.length}건</span>
          <span>{expanded ? '접기' : '펼치기'}</span>
        </div>
        <div className="chat-msgs">
          {(expanded ? messages : messages.slice(-2)).map((m, i) => (
            <div key={i} className={`msg ${m.role === 'user' ? 'user' : ''}`}>
              <span className="role">{m.role === 'user' ? '사용자' : 'ASSISTANT'}</span>
              <div className="bubble">{m.content}</div>
            </div>
          ))}
          {result.assumptions.length > 0 && (
            <div className="msg system">
              <span className="role">SYSTEM</span>
              <div className="bubble">적용한 가정: {result.assumptions.join(' · ')}</div>
            </div>
          )}
        </div>
        <form
          className="chat-input"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            onFollowup(text.trim());
            setText('');
          }}
        >
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="추가 질문 입력"
          />
          <button className="btn btn-primary" type="submit" disabled={!text.trim()}>
            전송
          </button>
        </form>
      </div>
    </div>
  );
}

function PlanCard({
  plan,
  delta,
  onReport,
}: {
  plan: PlanItem;
  delta: { text: string; changed: boolean } | null;
  onReport: () => void;
}) {
  return (
    <div className={`card${plan.best ? ' accent' : ''}`}>
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
        <div className="plan-carrier">{plan.carrier}</div>
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
      <div className="benefit">{plan.reason || plan.benefit}</div>
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
