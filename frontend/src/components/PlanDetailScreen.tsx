import { useEffect, useState } from 'react';
import { BrandLogo } from './BrandLogo';
import { PriceTerms } from './PriceTerms';
import { ChatMessage, PlanItem } from '../types';
import { ask } from '../api';

export const DETAIL_CHAT_KEY = 'momo-plan-detail-chat';

function readDetailChat(planId: string): ChatMessage[] {
 try {
  const saved = JSON.parse(sessionStorage.getItem(DETAIL_CHAT_KEY) || 'null');
  if (saved?.planId !== planId || !Array.isArray(saved.messages)) return [];
  return saved.messages.filter((message: unknown): message is ChatMessage => {
   if (!message || typeof message !== 'object') return false;
   const row = message as Partial<ChatMessage>;
   return (row.role === 'user' || row.role === 'assistant') && typeof row.content === 'string';
  });
 } catch { return []; }
}

export function PlanDetailScreen({plan,onBack,onCompare,selected}:{plan:PlanItem;onBack:()=>void;onCompare:()=>void;selected:boolean}) {
 const [question,setQuestion]=useState('');
 const [messages,setMessages]=useState<ChatMessage[]>(()=>readDetailChat(plan.id));
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);

 useEffect(()=>{
  try { sessionStorage.setItem(DETAIL_CHAT_KEY,JSON.stringify({planId:plan.id,messages})); }
  catch { /* 저장 공간·브라우저 정책 문제는 질문 기능을 막지 않는다. */ }
 },[plan.id,messages]);

 async function submit() {
  const text=question.trim();
  if(!text||busy)return;
  const history=messages.slice(-10);
  setMessages(current=>[...current,{role:'user',content:text}]);
  setQuestion('');setError('');setBusy(true);
  try {
   const response=await ask(plan.id,text,history);
   setMessages(current=>[...current,{role:'assistant',content:response.answer}]);
  } catch(e) {
   setError(e instanceof Error?e.message:'응답 실패');
  } finally {setBusy(false);}
 }

 return <main className="body detail-page"><button className="btn" onClick={onBack}>← 요금제 목록</button><section className="card detail-card"><BrandLogo carrier={plan.carrier}/><h2>{plan.name}</h2><div className="plan-price">월 {plan.price}원</div><p>{plan.data} · 소진 후 {plan.qos} · 통화 {plan.call}</p><PriceTerms plan={plan}/><p>{plan.compareMonths}개월 총비용 <strong>{plan.total}</strong></p><p>{plan.benefit}</p>{plan.ageCondition&&<p>가입 조건: {plan.ageCondition}</p>}{/^https?:\/\//.test(plan.sourceUrl)&&<a href={plan.sourceUrl} target="_blank" rel="noreferrer">공식 상품 페이지 ↗</a>}<button className="btn btn-primary" onClick={onCompare}>{selected ? "비교함에서 삭제" : "+ 비교 담기"}</button><hr/><div className="row-between"><h3>이 요금제에 대해 AI에게 물어보기</h3>{messages.length>0&&<button className="text-btn" type="button" onClick={()=>{setMessages([]);setError('');try{sessionStorage.removeItem(DETAIL_CHAT_KEY);}catch{/* 저장소 접근 실패는 화면 초기화를 막지 않는다. */}}}>대화 초기화</button>}</div>{messages.length>0&&<div className="detail-chat-history" aria-live="polite">{messages.map((message,index)=><div className={`detail-chat-message ${message.role}`} key={`${message.role}-${index}`}><strong>{message.role==='user'?'나':'AI'}</strong><p>{message.content}</p></div>)}</div>}<form onSubmit={e=>{e.preventDefault();void submit();}}><input aria-label="요금제 질문" value={question} onChange={e=>setQuestion(e.target.value)} placeholder="할인 기간이 끝나면 얼마인가요?"/><button className="btn btn-primary" disabled={busy||!question.trim()}>{busy?'답변 생성 중…':'질문하기'}</button></form>{error&&<p role="alert">{error}</p>}</section></main>;
}
