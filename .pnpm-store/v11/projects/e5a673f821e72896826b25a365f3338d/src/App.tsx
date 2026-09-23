import { RecommendationChat } from './components/RecommendationChat';
import { useEffect, useState, useRef } from 'react';
import { ChatMessage, HistoryRow, PlanItem, RecommendResponse, ScreenType } from './types';
import { recommend, getPlan } from './api';
import { GNB } from './components/GNB';
import { Stepper } from './components/Stepper';
import { HomeScreen } from './components/HomeScreen';
import { InputScreen } from './components/InputScreen';
import { ResultScreen } from './components/ResultScreen';
import { ReportScreen } from './components/ReportScreen';
import { BrowseScreen } from './components/BrowseScreen';
import { CompareScreen } from './components/CompareScreen';
import { PlanDetailScreen } from './components/PlanDetailScreen';
import { CompareBar } from './components/CompareBar';

/** 화면에 노출할 추천 건수. 백엔드는 5건까지 주지만 3칼럼 그리드에 맞춰 3건만 쓴다. */
const TOP_N = 3;

const now = () =>
  new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });

const routes: Record<string, ScreenType> = {'/':'s-home','/browse':'s-browse','/compare':'s-compare','/input':'s-input','/result':'s-result','/report':'s-report','/detail':'s-detail'};
const currentRoute = () => routes[window.location.hash.slice(1).split('?')[0]] ?? 's-home';
export default function App() {
  const [browseCategory, setBrowseCategory] = useState('all');
  const toggleCompare = (plan: PlanItem) => setCompare(prev => prev.some(p => p.id === plan.id) ? prev.filter(p => p.id !== plan.id) : [...prev, plan]);
  const [screen, setScreen] = useState<ScreenType>(currentRoute);
  const requestVersion = useRef(0);
  const [draft, setDraft] = useState('');
  const [inputVersion, setInputVersion] = useState(0);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [result, setResult] = useState<RecommendResponse | null>(null);
  // 재계산 시 순위·가격 변동을 보여주려고 직전 결과만 하나 들고 있는다.
  const [prevPlans, setPrevPlans] = useState<PlanItem[]>([]);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 비교함: 탐색 화면에서 체크한 요금제. GNB 배지와 비교 페이지이 같이 쓴다.
  const [compare, setCompare] = useState<PlanItem[]>(() => {
    try { const saved = JSON.parse(sessionStorage.getItem('momoplan.compare') || '[]'); return Array.isArray(saved) ? saved.filter((p: PlanItem) => p && typeof p.id === 'string' && typeof p.carrier === 'string' && typeof p.priceNum === 'number' && typeof p.totalNum === 'number') : []; } catch { return []; }
  });
  useEffect(() => { try { sessionStorage.setItem('momoplan.compare', JSON.stringify(compare)); } catch { /* Storage can be disabled. */ } }, [compare]);
  useEffect(() => { const onHashChange = () => { const hash = window.location.hash; setScreen(currentRoute()); window.scrollTo(0,0); }; window.addEventListener('popstate',onHashChange); return () => window.removeEventListener('popstate',onHashChange); }, []);
  const [askPlan, setAskPlan] = useState<PlanItem | null>(null);
  const [reportPlanId, setReportPlanId] = useState<string | null>(null);

  const navigate = (next: ScreenType) => {
    const path = '#' + (Object.keys(routes).find(key => routes[key] === next) || '/');
    if (window.location.hash !== path) window.history.pushState(null, '', path);
    setScreen(next);
    window.scrollTo(0, 0);
  };

  const openDetail = (plan: PlanItem) => { setAskPlan(plan); window.history.pushState(null, '', '#/detail?id='+encodeURIComponent(plan.id)); setScreen('s-detail'); window.scrollTo(0,0); };
  const [detailError, setDetailError] = useState('');
  useEffect(() => {
    if(screen !== 's-detail') return;
    const id = new URLSearchParams(window.location.hash.split('?')[1]).get('id');
    if(!id) {setDetailError('요금제 목록에서 상품을 선택해 주세요.'); return;}
    let live=true;setDetailError('');setAskPlan(null);
    getPlan(id).then(p=>{if(live)setAskPlan(p);}).catch(()=>{if(live)setDetailError('요금제 정보를 불러올 수 없습니다.');});
    return ()=>{live=false;};
  }, [screen]);
  /** 대화 전체를 매번 백엔드로 보낸다. 서버는 세션을 갖지 않는다. */
  const runRecommend = async (text: string, stayOnPage = false) => {
    if (loading) return;
    const version = ++requestVersion.current;
    const next: ChatMessage[] = [...messages, { role: 'user', content: text }];
    const before = result;
    setMessages(next);
    setPrevPlans(before?.plans ?? []);
    setLoading(true);
    setError(null);
    if (!stayOnPage) navigate('s-result');
    try {
      const data = await recommend(next);
      if(version !== requestVersion.current) return;
      data.plans = data.plans.slice(0, TOP_N);
      setResult(data);
      setHistory((rows) => [
        {
          time: now(),
          change: text,
          before: before ? `후보 ${before.candidateCount}건` : '—',
          after: `후보 ${data.candidateCount}건`,
          result: data.needsMoreInput
            ? '추가 정보 필요'
            : !before || before.plans.length === 0
              ? `상위 ${data.plans.length}건 선정`
              : before.plans[0]?.id === data.plans[0]?.id
                ? '1순위 유지'
                : '1순위 교체',
        },
        ...rows,
      ]);
      if (data.followupQuestion) {
        setMessages([...next, { role: 'assistant', content: data.followupQuestion }]);
      }
    } catch (err) {
      if(version !== requestVersion.current) return;
      setError(err instanceof Error ? err.message : '알 수 없는 오류');
    } finally {
      if(version === requestVersion.current) setLoading(false);
    }
  };

  const resetRecommendation = () => { requestVersion.current++; setMessages([]); setResult(null); setPrevPlans([]); setHistory([]); setError(null); setLoading(false); setReportPlanId(null); setDraft(''); setInputVersion(v=>v+1); };
  const chat = <RecommendationChat messages={messages} result={result} loading={loading} error={error} draft={draft} onDraft={setDraft} onSubmit={text=>runRecommend(text,screen==='s-browse')} onReset={resetRecommendation} onResult={()=>navigate('s-result')}/>;

  const step = screen === 's-report' ? 3 : screen === 's-result' ? 2 : 1;

  const openReport = (planId: string) => {
    setReportPlanId(planId);
    navigate('s-report');
  };

  return (
    <div className={compare.length ? "screen has-compare-dock" : "screen"}>
      <GNB
        currentScreen={screen}
        onNavigate={navigate}
        hasReport={!!result}
        compareCount={compare.length}
        onOpenCompare={() => navigate('s-compare')}
      />
      {screen !== 's-home' && screen !== 's-browse' && screen !== 's-compare' && screen !== 's-detail' && (
        <Stepper current={step} hasResult={!!result} onStepClick={navigate} />
      )}

      {screen === 's-home' && <HomeScreen onNavigate={navigate} onBrowse={(category) => { setBrowseCategory(category); navigate('s-browse'); }} compare={compare} onToggleCompare={toggleCompare} onAskPlan={openDetail} />}
      {screen === 's-input' && (
        <InputScreen key={inputVersion} chat={chat} loading={loading} onSubmit={runRecommend}/>

      )}
      {screen === 's-result' && (
        <ResultScreen
          chat={chat}
          result={result}
          prevPlans={prevPlans}
          messages={messages}
          history={history}
          loading={loading}
          error={error}
          onFollowup={runRecommend}
          onReport={openReport}
          onNavigate={navigate}
        />
      )}
      {screen === 's-report' && (
        <ReportScreen result={result} selectedPlanId={reportPlanId} onNavigate={navigate} />
      )}
      {screen === 's-browse' && (
        <BrowseScreen
          chat={chat}
          initialCategory={browseCategory}
          compare={compare}
          onToggleCompare={(plan) =>
            setCompare((prev) =>
              prev.some((p) => p.id === plan.id)
                ? prev.filter((p) => p.id !== plan.id)
                : [...prev, plan]
            )
          }
          onOpenCompare={() => navigate('s-compare')}
          onAskPlan={openDetail}
        />
      )}

      {screen === 's-compare' && <CompareScreen plans={compare} onRemove={toggleCompare} onClear={() => setCompare([])} onBrowse={() => navigate('s-browse')} />}
      {screen === 's-detail' && !askPlan && <div className="body"><p role="status">{detailError || '요금제 정보를 불러오는 중…'}</p><button className="btn" onClick={()=>navigate('s-browse')}>목록으로</button></div>}
      {screen === 's-detail' && askPlan && <PlanDetailScreen key={askPlan.id} plan={askPlan} onBack={() => navigate('s-browse')} selected={compare.some(p=>p.id===askPlan.id)} onCompare={()=>toggleCompare(askPlan)}/>}
      {compare.length>0 && ['s-home','s-browse','s-detail'].includes(screen) && <CompareBar plans={compare} onRemove={toggleCompare} onClear={()=>setCompare([])} onOpen={()=>navigate('s-compare')}/>}
    </div>
  );
}
