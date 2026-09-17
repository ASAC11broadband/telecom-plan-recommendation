import { useEffect, useState } from 'react';
import { PlanItem } from '../types';
import { ask } from '../api';

export function Modal({
  title,
  subtitle,
  onClose,
  width = 640,
  children,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  width?: number;
  children: React.ReactNode;
}) {
  // Esc 로 닫기. 모달이 열려 있는 동안만 듣는다.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal card" style={{ maxWidth: width }} onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <div>
            <strong style={{ fontSize: 'var(--fs-13)' }}>{title}</strong>
            {subtitle && (
              <p style={{ fontSize: 'var(--fs-11)', color: 'var(--t3)', marginTop: 2 }}>{subtitle}</p>
            )}
          </div>
          <button className="btn btn-sm" onClick={onClose}>
            닫기
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function AiQueryModal({ plan, onClose }: { plan: PlanItem; onClose: () => void }) {
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = (text: string) => {
    if (!text.trim() || loading) return;
    setLoading(true);
    setAnswer(null);
    setError(null);
    ask(plan.id, text.trim())
      .then((res) => setAnswer(res.answer))
      .catch((err) => setError(err instanceof Error ? err.message : '응답 실패'))
      .finally(() => setLoading(false));
  };

  const samples = ['테더링 되나요?', '데이터 다 쓰면 어떻게 되나요?', '가입 조건이 있나요?'];

  return (
    <Modal title="AI 질의" subtitle={`${plan.name} · ${plan.carrier}`} onClose={onClose}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {samples.map((s) => (
            <button className="btn btn-sm" key={s} disabled={loading} onClick={() => (setQuestion(s), submit(s))}>
              {s}
            </button>
          ))}
        </div>
        <form
          style={{ display: 'flex', gap: 8 }}
          onSubmit={(e) => {
            e.preventDefault();
            submit(question);
          }}
        >
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="이 요금제에 대해 궁금한 점을 물어보세요"
            autoFocus
          />
          <button className="btn btn-primary" type="submit" disabled={!question.trim() || loading}>
            {loading ? '확인 중' : '질문'}
          </button>
        </form>
        {error && <div className="msg system"><div className="bubble">{error}</div></div>}
        {answer && (
          <div className="msg">
            <span className="role">ASSISTANT</span>
            <div className="bubble">{answer}</div>
          </div>
        )}
        <p style={{ fontSize: 'var(--fs-10)', color: 'var(--t3)' }}>
          수집된 요금제 데이터만 근거로 답합니다. 자료에 없는 내용은 답하지 않습니다.
        </p>
      </div>
    </Modal>
  );
}
