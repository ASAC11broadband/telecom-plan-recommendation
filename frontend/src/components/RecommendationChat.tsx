import { ChatMessage, RecommendResponse } from '../types';

const SUGGESTIONS = [
  '영상 자주 봐요, 월 3만원 이하',
  '데이터 30GB 정도면 돼요',
  '통화는 거의 안 하고 저렴한 요금제',
  '소진 후 속도 3Mbps 이상이면 좋겠어요',
];

export function RecommendationChat({ messages, result, loading, error, draft, onDraft, onSubmit, onReset, onResult }: {
  messages: ChatMessage[];
  result: RecommendResponse | null;
  loading: boolean;
  error: string | null;
  draft: string;
  onDraft: (s: string) => void;
  onSubmit: (s: string) => void;
  onReset: () => void;
  onResult: () => void;
}) {
  const latestAssistant = [...messages].reverse().find(message => message.role === 'assistant')?.content;
  return (
    <aside className="card recommendation-chat" aria-label="요금제 추천 대화">
      <div className="panel-head chat-guide-head">
        <div className="chat-guide-title">
          <img src="/images/ai-plan-helper.png" alt="" />
          <strong>AI와 대화로 찾기</strong>
        </div>
        <button className="text-btn" onClick={onReset}>대화 초기화</button>
      </div>
      <div className="conversation-messages" role="log" aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-welcome">
            <span className="chat-step">STEP 02 · 편하게 말하기</span>
            <strong>어떤 요금제가 필요하세요?</strong>
            <p>사용습관이나 원하는 조건을 적어주세요.</p>
            <span className="chat-example-label">예시를 눌러 시작해 보세요</span>
            <div className="chat-suggestions" aria-label="입력 예시">
              {SUGGESTIONS.map((suggestion) => (
                <button type="button" key={suggestion} onClick={() => onDraft(suggestion)}>
                  <span>{suggestion}</span><b aria-hidden="true">＋</b>
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((message, index) => (
          <div className={`msg ${message.role}`} key={index}>
            <span className="role">{message.role === 'user' ? '나' : '모모 Helper'}</span>
            <div className="bubble">{message.content}</div>
          </div>
        ))}
        {loading && <p role="status" className="chat-status">답변을 준비하고 있어요.</p>}
        {error && <p role="alert" className="chat-error">{error}</p>}
        {result?.needsMoreInput && latestAssistant === result.followupQuestion && !loading && !error && <p className="chat-next-hint">위 질문에 답해 주시면 추천을 이어갈게요.</p>}
        {result && !result.needsMoreInput && result.plans.length > 0 && !loading && !error && (
          <button className="btn btn-block" onClick={onResult}>추천 요금제 {result.plans.length}개 확인하기 →</button>
        )}
      </div>
      <form className="conversation-compose" onSubmit={(event) => {
        event.preventDefault();
        if (loading || !draft.trim()) return;
        onSubmit(draft.trim());
        onDraft('');
      }}>
        <label htmlFor="shared-ai-message">자유롭게 입력</label>
        <textarea id="shared-ai-message" rows={3} value={draft} onChange={(event) => onDraft(event.target.value)} placeholder="예: 데이터 30GB 이상, 월 3만원 이하" />
        <small>{loading ? '조건을 확인하고 있어요. 잠시만 기다려 주세요.' : '선택한 예시에 조건을 더 적어도 좋아요.'}</small>
        <button className="btn btn-primary" disabled={loading || !draft.trim()}>{loading ? '답변을 준비 중…' : '모모에게 보내기'}</button>
      </form>
    </aside>
  );
}
