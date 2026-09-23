import { BrandLogo } from './BrandLogo';
import { useEffect, useRef, useState, ReactNode } from 'react';
import { PlanItem } from '../types';
import { ask } from '../api';
function Dialog({onClose,children}:{onClose:()=>void;children:ReactNode}) {
 const ref=useRef<HTMLDialogElement>(null);
 useEffect(()=>{ref.current?.showModal();},[]);
 return <dialog ref={ref} className="modal" onCancel={onClose}><button className="btn close-modal" onClick={onClose} aria-label="닫기">닫기 ×</button>{children}</dialog>;
}
export function AiQueryModal({plan,onClose}:{plan:PlanItem;onClose:()=>void}) {
 const [question,setQuestion]=useState('');const [answer,setAnswer]=useState('');const [busy,setBusy]=useState(false);
 return <Dialog onClose={onClose}><BrandLogo carrier={plan.carrier}/><h2>{plan.name}</h2><div className="plan-price">월 {plan.price}원</div><p>{plan.data} · 소진 후 {plan.qos} · 통화 {plan.call}</p><p>{plan.priceNote}</p><p>6개월 총비용 <strong>{plan.total}</strong></p><p>{plan.benefit}</p>{plan.ageCondition&&<p>가입 조건: {plan.ageCondition}</p>}{/^https?:\/\//.test(plan.sourceUrl)&&<a href={plan.sourceUrl} target="_blank" rel="noreferrer">공식 상품 페이지 ↗</a>}<hr/><h3>이 요금제에 대해 AI에게 물어보기</h3><form onSubmit={async e=>{e.preventDefault();if(!question.trim()||busy)return;setBusy(true);try{setAnswer((await ask(plan.id,question)).answer);}catch(e){setAnswer(e instanceof Error?e.message:'응답 실패');}finally{setBusy(false);}}}><input aria-label="요금제 질문" value={question} onChange={e=>setQuestion(e.target.value)} placeholder="할인 기간이 끝나면 얼마인가요?"/><button className="btn btn-primary" disabled={busy||!question.trim()}>{busy?'답변 생성 중…':'질문하기'}</button></form><p role="status">{answer}</p></Dialog>;
}
