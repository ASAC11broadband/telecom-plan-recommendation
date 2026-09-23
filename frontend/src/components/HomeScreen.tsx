import { useEffect, useState } from 'react';
import { ScreenType, Stats, PlanPage, PlanItem } from '../types';
import { fetchStats, listPlans } from '../api';
import { categories, categoryFilters } from '../categories';
import { PlanTile } from './PlanTile';
export function HomeScreen({ onNavigate, onBrowse, compare, onToggleCompare, onAskPlan }: { onNavigate: (s: ScreenType) => void; onBrowse: (category: string) => void; compare: PlanItem[]; onToggleCompare: (p: PlanItem) => void; onAskPlan: (p: PlanItem) => void }) {
 const [stats, setStats] = useState<Stats | null>(null);
 const [category, setCategory] = useState('all');
 const [plans, setPlans] = useState<PlanPage | null>(null);
 const [error, setError] = useState('');
 const [loading, setLoading] = useState(true);
 useEffect(() => { fetchStats().then(setStats).catch(() => setStats(null)); }, []);
 useEffect(() => { let live = true; setLoading(true); setError(''); listPlans({filters:categoryFilters(category),sort:'total_asc',page:1,pageSize:3}).then(p => {if(live) setPlans(p);}).catch(() => {if(live) setError('요금제를 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.');}).finally(() => {if(live) setLoading(false);}); return () => {live=false;}; }, [category]);
 const active = categories.find(c => c.id === category)!;
 return <main className="body home">
 <section className="home-hero"><div><div className="eyebrow">내 생활에 맞는 통신비, 모모플랜</div><h1>요금제는 많으니까,<br/>비교는 <em>모모플랜.</em></h1><p>영상부터 통화까지, 내가 쓰는 방식대로.<br/>통신 3사와 알뜰폰을 한곳에서 비교해 보세요.</p><button className="btn btn-primary" onClick={() => onBrowse('all')}>내게 맞는 요금제 찾기 <span>→</span></button></div><div className="hero-art" aria-hidden="true"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="floating-label label-one">▶ 영상도 마음껏</div><div className="phone"><div className="phone-notch"/><span>나의 다음 요금제</span><div className="phone-symbol">m<span>☺</span></div><strong>딱 맞게 쓰고,<br/>가볍게 내고.</strong><div className="phone-pill">모모플랜에서 발견</div></div><div className="floating-label label-two">✓ 통신비 비교 완료</div></div></section>
 <section className="coverage-strip" aria-label="모모플랜 요금제 커버리지"><div className="coverage-intro"><strong>모모플랜이 비교하는<br/>요금제의 범위</strong><span>보유 데이터 기준</span></div>{[[stats?.total,'전체 요금제'],[stats?.mvno,'알뜰폰 요금제'],[stats?.mno,'통신 3사 요금제'],[stats?.brands,'알뜰폰 브랜드']].map(([n,l]) => <div className="coverage-cell" key={l}><strong>{typeof n === 'number' ? n.toLocaleString() : '—'}<small>{l === '알뜰폰 브랜드' ? '개' : '개'}</small></strong><span>{l}</span></div>)}</section>
 <section className="discovery"><div className="section-heading"><div><div className="eyebrow">FIND YOUR PLAN</div><h2>어떤 일상에 함께할 요금제인가요?</h2><p>사용 패턴을 선택하면, 해당 조건의 요금제를 바로 보여드려요.</p></div><button className="text-btn" onClick={() => onBrowse(category)}>전체 둘러보기 ↗</button></div>
 <div className="category-grid">{categories.map(c => <button key={c.id} className={'category-card '+(category===c.id?'active':'')} aria-pressed={category===c.id} onClick={() => setCategory(c.id)}><span className={'category-icon '+c.id}>{c.icon}</span><strong>{c.title}</strong><small>{c.description}</small></button>)}</div>
 <div className="section-heading results-heading"><h3>{active.title} <span className="accent-text">{!loading && !error && plans ? plans.total.toLocaleString()+'개' : ''}</span></h3><span className="muted">{stats ? `${stats.compareMonths}개월 ` : ''}총비용 낮은순</span></div>
 {loading ? <div className="empty-state" role="status">요금제를 불러오는 중…</div> : error ? <div className="empty-state" role="alert">{error}</div> : plans?.plans.length ? <div className="plan-grid">{plans.plans.map(plan => <PlanTile key={plan.id} plan={plan} selected={compare.some(p => p.id===plan.id)} onCompare={() => onToggleCompare(plan)} onAsk={() => onAskPlan(plan)} />)}</div> : <div className="empty-state">조건에 맞는 요금제가 없습니다.</div>}
 <p className="data-disclaimer">보유 데이터 내 조건별 분류이며, 카테고리는 서로 중복될 수 있습니다. 할인 기간과 가입 조건을 함께 확인해 주세요.</p>
 <button className="btn browse-more" onClick={() => onBrowse(category)}>{active.title} 더 보기 →</button></section>
 <section className="ai-banner"><div><span className="eyebrow">선택이 어렵다면</span><h2>평소처럼 말하면, AI가 찾아드려요.</h2><p>“출퇴근길 유튜브를 많이 보고, 한 달 2만 원 정도 쓰고 싶어요.”</p></div><button className="btn btn-primary" onClick={() => onNavigate('s-input')}>AI 맞춤 추천 시작 ↗</button></section>
 <footer>모모플랜 · MoMo Plan <span>통신 3사 · 알뜰폰 요금제 비교</span></footer></main>;
}
