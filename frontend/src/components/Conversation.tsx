import { useState } from 'react';
import { ChatMessage } from '../types';

export function Conversation({ messages, loading, error, onSubmit }: {
  messages: ChatMessage[]; loading: boolean; error: string | null; onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState('');
  return <section className="card conversation">
    <div className="panel-head"><strong>AI 상담</strong><span className="tag tag-muted">이 탭에서 대화 유지</span></div>
    <div className="chat-msgs" aria-live="polite">
      {messages.length === 0 && <p>사용량과 예산을 알려주시면 요금제를 찾아드릴게요.</p>}
      {messages.map((message, index) => <div className={`msg ${message.role === 'user' ? 'user' : ''}`} key={index}>
        <span className="role">{message.role === 'user' ? '나' : '모모플랜'}</span><div className="bubble">{message.content}</div>
      </div>)}
      {loading && <div className="inline-loading" role="status"><span className="spinner" />조건에 맞는 요금제와 추천 이유를 확인하고 있어요. 잠시만 기다려 주세요.</div>}
      {error && <div className="notice" role="alert">{error} 입력한 내용은 유지됩니다. 잠시 후 다시 요청해 주세요.</div>}
    </div>
    <form className="chat-input" onSubmit={event => {
      event.preventDefault(); if (loading || !text.trim()) return; onSubmit(text.trim()); setText('');
    }}>
      <input aria-label="상담 메시지" value={text} onChange={event => setText(event.target.value)} placeholder="예: 예산을 2만원으로 낮춰줘" />
      <button className="btn btn-primary" disabled={loading || !text.trim()}>{loading ? '확인 중' : '전송'}</button>
    </form>
  </section>;
}
