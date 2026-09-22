import { useState } from 'react';
import { ChatMessage, RecommendResponse } from '../types';

const QUICK = [
  { label: '월 3만원 이하 가성비', query: '월 3만원 이하인 요금제 추천해줘' },
  { label: '데이터 무제한', query: '데이터 무제한인 요금제 추천해줘' },
  { label: '혜택 포함 5만원 이하', query: '페이백 혜택이 있고 요금이 5만원 이하인 요금제 추천해줘' },
  { label: 'QoS 3Mbps 이상', query: 'QoS 3Mbps 이상이고 요금이 3만원 이하인 요금제 추천해줘' },
];

const FOLLOWUP_CHOICES = [
  { label: '월 20GB 정도', query: '데이터 월 20GB 정도 써' },
  { label: '데이터 무제한', query: '데이터 무제한으로 찾아줘' },
  { label: '월 3만원 이하', query: '월 예산 3만원 이하야' },
  { label: '월 5만원 이하', query: '월 예산 5만원 이하야' },
];

/** 직접 선택도 채팅과 같은 자연어 경로로 보낸다. 추천 규칙이 두 갈래로 갈라지지 않는다. */
function toSentence(data: number, call: number, budget: number, age: string, networkGen: string) {
  return (
    [
      data >= 31 ? '데이터 무제한' : `데이터 월 ${data}GB 정도`,
      call >= 21 ? '통화 무제한' : call === 0 ? '통화는 거의 안 함' : `통화 월 ${call * 10}분 정도`,
      `월 예산 ${budget.toLocaleString()}원 이하`,
      age ? `만 ${age}세` : '나이는 미입력',
      networkGen ? `${networkGen} 요금제만` : 'LTE와 5G 모두 가능',
    ].join(', ') + '. 요금제 추천해줘'
  );
}

export function InputScreen({
  messages,
  result,
  loading,
  error,
  onSubmit,
}: {
  messages: ChatMessage[];
  result: RecommendResponse | null;
  loading: boolean;
  error: string | null;
  onSubmit: (text: string) => void;
}) {
  const [mode, setMode] = useState<'chat' | 'form'>('chat');
  const [text, setText] = useState('');
  const [data, setData] = useState(20);
  const [call, setCall] = useState(0);
  const [budget, setBudget] = useState(30000);
  const [age, setAge] = useState('');
  const [networkGen, setNetworkGen] = useState('');

  const isChat = mode === 'chat';
  const profile = result?.profile ?? null;
  const formMode = !isChat && !profile;
  const needsMoreInput = result?.needsMoreInput === true;
  const showSummary = !needsMoreInput && (formMode || !!profile);

  const shownData = profile
    ? profile.data_unlimited
      ? '무제한'
      : profile.min_data_gb
        ? `${profile.min_data_gb}GB 이상`
        : profile.target_data_gb
          ? `${profile.target_data_gb}GB 내외`
          : profile.estimated_monthly_data_gb
            ? `월 ${profile.estimated_monthly_data_gb}GB 예상`
            : '미확인'
    : formMode
      ? data >= 31 ? '무제한' : `${data}GB`
      : '미입력';
  const shownCall = profile
    ? profile.voice_unlimited ? '무제한' : profile.min_voice_minutes ? `월 ${profile.min_voice_minutes}분` : '미확인'
    : formMode
      ? call >= 21 ? '무제한' : call === 0 ? '거의 없음' : `월 ${call * 10}분`
      : '미입력';
  const shownBudget = profile?.budget_max_won
    ? `${profile.budget_max_won.toLocaleString()}원 이하`
    : formMode ? `${budget.toLocaleString()}원 이하` : '미입력';
  const shownAge = profile
    ? profile.age_condition || (profile.user_age ? `만 ${profile.user_age}세` : '미확인')
    : formMode ? (age ? `만 ${age}세` : '미입력') : '미입력';
  const shownNetwork = profile?.network_gen ?? (formMode ? (networkGen || '상관없음') : '미입력');

  const submit = (value: string) => {
    if (!value.trim() || loading) return;
    onSubmit(value.trim());
    setText('');
  };

  return (
    <main className={`body input-layout${showSummary ? '' : ' no-summary'}`}>
      <section className="card input-main">
        <div className="panel-head input-head">
          <div>
            <strong>이용 패턴 입력</strong>
            <p>예산 또는 데이터 사용량 하나만 알려주면 추천을 시작할 수 있어요.</p>
          </div>
          <div className="toggle" aria-label="입력 방식">
            <button className={isChat ? 'on' : ''} aria-pressed={isChat} onClick={() => setMode('chat')}>대화로 입력</button>
            <button className={!isChat ? 'on' : ''} aria-pressed={!isChat} onClick={() => setMode('form')}>직접 선택</button>
          </div>
        </div>

        {loading && <div className="input-loading" role="status"><span className="spinner" />조건을 확인하고 있어요.</div>}
        {error && <div className="notice input-error" role="alert">{error} 입력 내용은 유지됩니다. 잠시 후 다시 시도해 주세요.</div>}

        {isChat ? (
          <div className="input-chat">
            {messages.length === 0 ? (
              <div className="input-intro">
                <span className="tag tag-accent">30초 맞춤 추천</span>
                <h2>예산이나 데이터 중 하나만 알려주세요.</h2>
                <p>자세한 조건이 생각나지 않아도 괜찮아요. 아래 예시를 고르거나 편하게 입력하면 됩니다.</p>
              </div>
            ) : (
              <div className="input-messages" aria-live="polite">
                {messages.map((message, index) => (
                  <div className={`msg ${message.role === 'user' ? 'user' : ''}`} key={index}>
                    <span className="role">{message.role === 'user' ? '나' : '모모플랜'}</span>
                    <div className="bubble">{message.content}</div>
                  </div>
                ))}
              </div>
            )}

            {needsMoreInput && (
              <section className="input-followup" aria-live="polite">
                <div className="input-followup-copy">
                  <span className="tag tag-amber">한 가지만 더</span>
                  <strong>아래에서 가장 가까운 조건 하나를 골라주세요.</strong>
                </div>
                <div className="quick-row">
                  {FOLLOWUP_CHOICES.map(({ label, query }) => (
                    <button className="btn btn-sm" disabled={loading} key={label} onClick={() => submit(query)}>{label}</button>
                  ))}
                </div>
              </section>
            )}

            {!needsMoreInput && <div className="input-quick">
              <span className="section-label">빠른 시작</span>
              <div className="quick-row">
                {QUICK.map(({ label, query }) => (
                  <button className="btn btn-sm" disabled={loading} key={label} onClick={() => submit(query)}>{label}</button>
                ))}
              </div>
            </div>}

            <form className="chat-input" onSubmit={(event) => { event.preventDefault(); submit(text); }}>
              <input
                type="text"
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="예: 월 3만원 이하, 유튜브를 자주 봐요"
              />
              <button className="btn btn-primary" type="submit" disabled={!text.trim() || loading}>전송</button>
            </form>
          </div>
        ) : (
          <div className="input-form">
            <div className="form-fields">
              <Field label="데이터 사용량" value={data >= 31 ? '무제한' : `${data}GB`} ticks={['0GB', '30GB', '무제한']}>
                <input type="range" min={0} max={31} value={data} onChange={(event) => setData(Number(event.target.value))} />
              </Field>
              <Field label="월 예산" value={`${budget.toLocaleString()}원 이하`} ticks={['10,000원', '60,000원']}>
                <input type="range" min={10000} max={60000} step={5000} value={budget} onChange={(event) => setBudget(Number(event.target.value))} />
              </Field>
              <Field label="음성통화" value={call >= 21 ? '무제한' : call === 0 ? '거의 없음' : `${call * 10}분`} ticks={['0분', '200분', '무제한']}>
                <input type="range" min={0} max={21} value={call} onChange={(event) => setCall(Number(event.target.value))} />
              </Field>
              <div className="field">
                <div className="top"><span className="k">통신 세대</span><span className="v">{networkGen || '상관없음'}</span></div>
                <div className="chip-row generation-choice">
                  {['', 'LTE', '5G'].map((generation) => (
                    <button className={`chip${networkGen === generation ? ' on' : ''}`} type="button" key={generation || 'any'} onClick={() => setNetworkGen(generation)}>
                      {generation || '상관없음'}
                    </button>
                  ))}
                </div>
              </div>
              <div className="field age-field">
                <div className="top"><label className="k" htmlFor="user-age">만 나이 (선택)</label></div>
                <input id="user-age" type="number" min={5} max={99} value={age} onChange={(event) => setAge(event.target.value)} placeholder="입력하면 연령 전용 상품까지 함께 확인" />
              </div>
            </div>
            <button className="btn btn-primary input-submit" disabled={loading || (!!age && (Number(age) < 5 || Number(age) > 99))} onClick={() => submit(toSentence(data, call, budget, age, networkGen))}>
              이 조건으로 추천받기
            </button>
          </div>
        )}
      </section>

      {showSummary && (
        <aside className="card input-summary">
          <div className="panel-head"><strong>현재 조건 요약</strong><span className="tag tag-muted">자동 반영</span></div>
          <div className="spec-row"><span className="k">데이터</span><span className="v num">{shownData}</span></div>
          <div className="spec-row"><span className="k">월 예산</span><span className="v num">{shownBudget}</span></div>
          <div className="spec-row"><span className="k">통화</span><span className="v">{shownCall}</span></div>
          <div className="spec-row"><span className="k">통신 세대</span><span className="v">{shownNetwork}</span></div>
          <div className="spec-row"><span className="k">연령</span><span className="v">{shownAge}</span></div>
        </aside>
      )}
    </main>
  );
}

function Field({ label, value, ticks, children }: { label: string; value: string; ticks: string[]; children: React.ReactNode }) {
  return (
    <div className="field">
      <div className="top"><span className="k">{label}</span><span className="v num">{value}</span></div>
      {children}
      <div className="ticks">{ticks.map((tick) => <span key={tick}>{tick}</span>)}</div>
    </div>
  );
}
