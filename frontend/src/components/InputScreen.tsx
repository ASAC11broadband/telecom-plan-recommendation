import { useState } from 'react';
import { ChatMessage, RecommendResponse, ScreenType } from '../types';

const QUICK = [
  {
    label: '월 3만원 이하 가성비인 요금제',
    query: '월 3만원 이하인 요금제 추천해줘',
  },
  {
    label: '데이터 무제한인 요금제',
    query: '데이터 무제한인 요금제 추천해줘',
  },
  {
    label: '페이백 혜택이 있고 요금이 5만원 이하인 요금제',
    query: '페이백 혜택이 있고 요금이 5만원 이하인 요금제 추천해줘',
  },
  {
    label: 'QoS 3Mbps 이상이고 요금이 3만원 이하인 요금제',
    query: 'QoS 3Mbps 이상이고 요금이 3만원 이하인 요금제 추천해줘',
  },
];

/** 슬라이더 값을 그대로 자연어로 바꿔 채팅과 같은 입력 경로로 보낸다.
 *  UserProfile 을 직접 주입하는 두 번째 경로를 만들면 결과가 갈리므로 만들지 않는다. */
function toSentence(data: number, call: number, budget: number, age: string) {
  return (
    [
      data >= 31 ? '데이터 무제한' : `데이터 월 ${data}GB 정도`,
      call >= 21 ? '통화 무제한' : call === 0 ? '통화는 거의 안 함' : `통화 월 ${call * 10}분 정도`,
      `월 예산 ${budget.toLocaleString()}원 이하`,
      age ? `만 ${age}세` : '나이는 미입력',
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
  const [age, setAge] = useState('');

  const isChat = mode === 'chat';
  const profile = result?.profile ?? null;

  // 추출된 프로필. 아직 아무것도 말하지 않았으면 '미입력'이다.
  // 슬라이더 기본값(20GB/3만원/20대)을 추출 결과처럼 보여주면 사용자는 자기가 하지 않은
  // 말이 잡힌 줄 알고, 실제 추천 조건과도 어긋난다. 직접 선택 모드에서만 현재 값을 비춘다.
  const formMode = !isChat && !profile;
  const shownData = profile
    ? profile.data_unlimited
      ? '무제한'
      : profile.min_data_gb
        ? `${profile.min_data_gb}GB 이상${profile.max_data_gb ? ` · 최대 ${profile.max_data_gb}GB` : ''}`
        : profile.target_data_gb
          ? `${profile.target_data_gb}GB 내외`
          : profile.max_data_gb
            ? `최대 ${profile.max_data_gb}GB`
            : profile.estimated_monthly_data_gb
              ? `월 ${profile.estimated_monthly_data_gb}GB 예상`
              : '미확인'
    : formMode
      ? data >= 31
        ? '무제한'
        : `${data}GB`
      : '미입력';
  const shownCall = profile
    ? profile.voice_unlimited
      ? '무제한'
      : profile.min_voice_minutes
        ? `월 ${profile.min_voice_minutes}분`
        : '미확인'
    : formMode
      ? call >= 21
        ? '무제한'
        : call === 0
          ? '거의 없음'
          : `월 ${call * 10}분`
      : '미입력';
  const shownBudget = profile?.budget_max_won
    ? `${profile.budget_max_won.toLocaleString()}원 이하`
    : formMode
      ? `${budget.toLocaleString()}원 이하`
      : '미입력';
  const shownAge = profile
    ? profile.age_condition || (profile.user_age ? `만 ${profile.user_age}세` : '미확인')
    : formMode
      ? (age ? `만 ${age}세` : '미입력')
      : '미입력';

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
                조건을 분석해 후보와 추가 질문을 함께 안내합니다
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
                {QUICK.map(({ label, query }) => (
                  <button
                    key={label}
                    className="btn btn-sm"
                    disabled={loading}
                    onClick={() => submit(query)}
                  >
                    {label}
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
                <label className="k" htmlFor="user-age">만 나이 (선택)</label>
              </div>
              <input id="user-age" type="number" min={5} max={99} value={age} onChange={e => setAge(e.target.value)} placeholder="미입력 시 연령 전용 상품 제외" />
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
              disabled={loading || (!!age && (Number(age) < 5 || Number(age) > 99))}
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
          <span style={{ fontSize: 'var(--fs-11)', color: 'var(--accent)' }}>추천 요청 후 반영</span>
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
