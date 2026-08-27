import { useState } from 'react';
import { ChatMessage, RecommendResponse, ScreenType } from '../types';

const QUICK = [
  '월 3만원 이하 가성비',
  '데이터 무제한 (OTT/동영상)',
  '통화 위주 요금제',
  '통신 3사 가족결합 기준',
];

const AGES = ['20대', '30대', '40대', '50대+'];

/** 슬라이더 값을 그대로 자연어로 바꿔 채팅과 같은 입력 경로로 보낸다.
 *  UserProfile 을 직접 주입하는 두 번째 경로를 만들면 결과가 갈리므로 만들지 않는다. */
function toSentence(data: number, call: number, budget: number, age: string) {
  return (
    [
      data >= 31 ? '데이터 무제한' : `데이터 월 ${data}GB 정도`,
      call >= 21 ? '통화 무제한' : call === 0 ? '통화는 거의 안 함' : `통화 월 ${call * 10}분 정도`,
      `월 예산 ${budget.toLocaleString()}원 이하`,
      age,
    ].join(', ') + '. 요금제 추천해줘'
  );
}

export function InputScreen({
  messages,
  result,
  loading,
  onSubmit,
  onNavigate,
}: {
  messages: ChatMessage[];
  result: RecommendResponse | null;
  loading: boolean;
  onSubmit: (text: string) => void;
  onNavigate: (s: ScreenType) => void;
}) {
  const [mode, setMode] = useState<'chat' | 'form'>('chat');
  const [text, setText] = useState('');
  const [data, setData] = useState(20);
  const [call, setCall] = useState(0);
  const [budget, setBudget] = useState(30000);
  const [age, setAge] = useState('20대');

  const isChat = mode === 'chat';
  const profile = result?.profile ?? null;

  // 추출된 프로필: 추천을 한 번 돌린 뒤에는 백엔드가 뽑은 값이 정답이다.
  const shownData = profile
    ? profile.data_unlimited
      ? '무제한'
      : profile.min_data_gb
        ? `${profile.min_data_gb}GB`
        : '미확인'
    : data >= 31
      ? '무제한'
      : `${data}GB`;
  const shownCall = profile
    ? profile.voice_unlimited
      ? '무제한'
      : profile.min_voice_minutes
        ? `월 ${profile.min_voice_minutes}분`
        : '거의 없음'
    : call >= 21
      ? '무제한'
      : call === 0
        ? '거의 없음'
        : `월 ${call * 10}분`;
  const shownBudget = profile?.budget_max_won
    ? `${profile.budget_max_won.toLocaleString()}원 이하`
    : `${budget.toLocaleString()}원 이하`;
  const shownAge = profile?.age_condition || age;

  const submit = (value: string) => {
    if (!value.trim() || loading) return;
    onSubmit(value.trim());
    setText('');
  };

  return (
    <div className="body split">
      <div className="card" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <div className="panel-head">
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <strong style={{ fontSize: 'var(--fs-13)' }}>이용 패턴 입력</strong>
            <span style={{ fontSize: 'var(--fs-11)', color: 'var(--accent)' }}>
              한마디 입력 시 즉시 1차 추천 및 꼬리질문 진행
            </span>
          </div>
          <div className="toggle">
            <button className={isChat ? 'on' : ''} onClick={() => setMode('chat')}>
              대화로 입력 (추천)
            </button>
            <button className={!isChat ? 'on' : ''} onClick={() => setMode('form')}>
              직접 선택 입력
            </button>
          </div>
        </div>

        {isChat ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <div
              style={{
                flex: 1,
                padding: '18px 20px',
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
                minHeight: 320,
              }}
            >
              <div className="section-label" style={{ marginBottom: 0 }}>
                상담 컨설턴트
              </div>
              <div className="msg">
                <div className="bubble">
                  생각하고 계신 조건이나 현재 통신 습관을 편하게 말씀해 주세요. 어떤 조건이든
                  말씀해주시면 바로 맞춤 요금제 후보를 먼저 찾아드릴게요.
                </div>
              </div>
              {messages.map((m, i) => (
                <div key={i} className={`msg ${m.role === 'user' ? 'user' : ''}`}>
                  <span className="role">{m.role === 'user' ? '사용자' : 'ASSISTANT'}</span>
                  <div className="bubble">{m.content}</div>
                </div>
              ))}
            </div>

            <div style={{ padding: '0 20px 12px' }}>
              <div className="section-label" style={{ marginBottom: 8 }}>
                자주 찾는 이용 조건:
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {QUICK.map((q) => (
                  <button key={q} className="btn btn-sm" disabled={loading} onClick={() => submit(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>

            <form
              className="chat-input"
              onSubmit={(e) => {
                e.preventDefault();
                submit(text);
              }}
            >
              <input
                type="text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={'예: "월 3만원 이하로 찾아요", "유튜브 많이 보고 통화는 적어요"'}
              />
              <button className="btn btn-primary" type="submit" disabled={!text.trim() || loading}>
                전송
              </button>
            </form>
          </div>
        ) : (
          <div
            style={{
              flex: 1,
              padding: 20,
              display: 'flex',
              flexDirection: 'column',
              gap: 20,
            }}
          >
            <Field label="데이터 사용량" value={data >= 31 ? '무제한' : `${data}GB`} ticks={['0GB', '30GB', '무제한']}>
              <input type="range" min={0} max={31} value={data} onChange={(e) => setData(Number(e.target.value))} />
            </Field>
            <Field
              label="음성통화"
              value={call >= 21 ? '무제한' : call === 0 ? '거의 없음' : `${call * 10}분`}
              ticks={['0분', '200분', '무제한']}
            >
              <input type="range" min={0} max={21} value={call} onChange={(e) => setCall(Number(e.target.value))} />
            </Field>
            <div className="field">
              <div className="top">
                <span className="k">연령대</span>
              </div>
              <div className="chip-row" style={{ gridTemplateColumns: 'repeat(4,1fr)' }}>
                {AGES.map((a) => (
                  <button
                    key={a}
                    className={`chip ${age === a ? 'on' : ''}`}
                    onClick={() => setAge(a)}
                  >
                    {a}
                  </button>
                ))}
              </div>
            </div>
            <Field
              label="월 예산"
              value={`${budget.toLocaleString()}원 이하`}
              ticks={['10,000원', '60,000원']}
            >
              <input
                type="range"
                min={10000}
                max={60000}
                step={5000}
                value={budget}
                onChange={(e) => setBudget(Number(e.target.value))}
              />
            </Field>
            <button
              className="btn btn-primary btn-block"
              disabled={loading}
              onClick={() => submit(toSentence(data, call, budget, age))}
            >
              추천 결과 보기
            </button>
          </div>
        )}
      </div>

      <div className="card" style={{ width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
        <div className="panel-head">
          <strong style={{ fontSize: 'var(--fs-13)' }}>추출된 프로필</strong>
          <span style={{ fontSize: 'var(--fs-11)', color: 'var(--accent)' }}>실시간 동기화</span>
        </div>
        <div className="spec-row">
          <span className="k">데이터 사용량</span>
          <span className="v num">{shownData}</span>
        </div>
        <div className="spec-row">
          <span className="k">음성통화</span>
          <span className="v">{shownCall}</span>
        </div>
        <div className="spec-row">
          <span className="k">희망 월 예산</span>
          <span className="v num">{shownBudget}</span>
        </div>
        <div className="spec-row">
          <span className="k">연령대</span>
          <span className="v">{shownAge}</span>
        </div>
        <div className="spec-row">
          <span className="k">상태</span>
          {loading ? (
            <span className="tag tag-amber">분석 중</span>
          ) : result ? (
            <span className="tag tag-green">1차 후보 산출 완료</span>
          ) : (
            <span className="tag tag-muted">입력 대기</span>
          )}
        </div>

        <div style={{ flex: 1 }} />

        <div style={{ padding: '14px 16px', borderTop: '1px solid var(--border)' }}>
          <button
            className="btn btn-primary btn-block"
            disabled={!result || loading}
            onClick={() => onNavigate('s-result')}
          >
            추천 결과 보기{result ? ` (상위 ${result.plans.length}건)` : ''}
          </button>
          <p
            style={{
              fontSize: 'var(--fs-10)',
              color: 'var(--t3)',
              textAlign: 'center',
              marginTop: 8,
            }}
          >
            결과 화면에서도 꼬리질문으로 언제든 재계산 가능합니다
          </p>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  ticks,
  children,
}: {
  label: string;
  value: string;
  ticks: string[];
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      <div className="top">
        <span className="k">{label}</span>
        <span className="v num">{value}</span>
      </div>
      {children}
      <div className="ticks">
        {ticks.map((t) => (
          <span key={t}>{t}</span>
        ))}
      </div>
    </div>
  );
}
