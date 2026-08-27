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

export function CompareModal({ plans, onClose }: { plans: PlanItem[]; onClose: () => void }) {
  const rows: [string, (p: PlanItem) => string][] = [
    ['사업자 / 망', (p) => p.carrier],
    ['월 요금', (p) => `${p.price}원`],
    ['정가', (p) => `${p.originalPrice.toLocaleString()}원`],
    ['프로모션 기간', (p) => (p.isPromo ? (p.promoMonths ? `${p.promoMonths}개월` : '약정 유지') : '없음')],
    ['데이터', (p) => p.data],
    ['소진 후 속도', (p) => p.qos],
    ['음성통화', (p) => p.call],
    ['문자', (p) => p.sms],
    ['테더링', (p) => (p.tetheringGb ? `${p.tetheringGb}GB` : '미제공')],
    ['부가 혜택', (p) => p.benefit],
    [`${plans[0]?.compareMonths ?? 6}개월 총비용`, (p) => p.total],
  ];

  return (
    <Modal
      title="요금제 비교"
      subtitle={`선택한 ${plans.length}건의 제공량과 ${plans[0]?.compareMonths ?? 6}개월 총비용을 비교합니다.`}
      onClose={onClose}
      width={860}
    >
      <div style={{ overflowX: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th>항목</th>
              {plans.map((p) => (
                <th key={p.id}>{p.name}</th>
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
    </Modal>
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
