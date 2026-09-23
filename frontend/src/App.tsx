import { useEffect, useRef, useState } from 'react';
import { ChatMessage, HistoryRow, PlanItem, RecommendResponse, ScreenType } from './types';
import { recommend, fetchPlan } from './api';
import { GNB } from './components/GNB';
import { Stepper } from './components/Stepper';
import { HomeScreen } from './components/HomeScreen';
import { InputScreen } from './components/InputScreen';
import { ResultScreen } from './components/ResultScreen';
import { ReportScreen } from './components/ReportScreen';
import { BrowseScreen } from './components/BrowseScreen';
import { CompareScreen } from './components/CompareScreen';
import { RecommendationChat } from './components/RecommendationChat';
import { PlanDetailScreen } from './components/PlanDetailScreen';
import { CompareBar } from './components/CompareBar';

const now = () =>
  new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });

const SCREENS: ScreenType[] = ['s-home', 's-input', 's-result', 's-report', 's-browse', 's-compare', 's-detail'];

/** 주소창의 해시를 화면 상태로 쓴다. 라우터를 넣지 않고도 새로고침·뒤로가기·링크 공유가 된다.
 *  상세 화면만 `#/detail?id=...` 처럼 쿼리를 붙인다. */
function screenFromHash(): ScreenType {
  const hash = window.location.hash.replace(/^#\/?/, '').split('?')[0];
  const matched = SCREENS.find((s) => s === `s-${hash}`);
  return matched ?? 's-home';
}

const detailIdFromHash = () => new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('id');

const SESSION_KEY = 'momoplan-session-v1';
function readSession(): { pending?: boolean; messages?: ChatMessage[]; result?: RecommendResponse | null; compare?: PlanItem[]; history?: HistoryRow[]; prevPlans?: PlanItem[]; reportPlanId?: string | null } {
  try {
    const saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null');
    if (!saved || !Array.isArray(saved.messages) || !Array.isArray(saved.compare) || !Array.isArray(saved.history)) return {};
    if (saved.result && (!Array.isArray(saved.result.plans) || !Array.isArray(saved.result.assumptions))) return {};
    return saved;
  } catch { return {}; }
}

export default function App() {
  const [saved] = useState(readSession);
  const [screen, setScreen] = useState<ScreenType>(screenFromHash);
  const [messages, setMessages] = useState<ChatMessage[]>(saved.messages ?? []);
  const [result, setResult] = useState<RecommendResponse | null>(saved.result ?? null);
  // 재계산 시 순위·가격 변동을 보여주려고 직전 결과만 하나 들고 있는다.
  const [prevPlans, setPrevPlans] = useState<PlanItem[]>(saved.prevPlans ?? []);
  const [history, setHistory] = useState<HistoryRow[]>(saved.history ?? []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(saved.pending ? '새로고침 전에 진행하던 요청은 자동 재개되지 않습니다.' : null);
  // 비교함: 탐색 화면에서 체크한 요금제. GNB 배지와 비교함 화면이 같이 쓴다.
  const [compare, setCompare] = useState<PlanItem[]>(saved.compare ?? []);
  const [reportPlanId, setReportPlanId] = useState<string | null>(saved.reportPlanId ?? null);
  // 채팅 입력창은 화면을 옮겨도 같은 초안을 쓴다.
  const [draft, setDraft] = useState('');
  const [inputVersion, setInputVersion] = useState(0);
  const [browseCategory, setBrowseCategory] = useState('all');
  const [askPlan, setAskPlan] = useState<PlanItem | null>(null);
  const [detailError, setDetailError] = useState('');
  // 처음부터를 누른 뒤 늦게 도착한 이전 응답이 화면을 덮지 않게 한다.
  const requestVersion = useRef(0);

  useEffect(() => {
    try { sessionStorage.setItem(SESSION_KEY, JSON.stringify({ messages, result, compare, history, prevPlans, reportPlanId, pending: loading })); }
    catch { /* 저장이 차단되거나 용량이 부족해도 현재 상담은 계속한다. */ }
  }, [messages, result, compare, history, prevPlans, reportPlanId, loading]);

  const toggleCompare = (plan: PlanItem) => setCompare(previous => previous.some(item => item.id === plan.id)
    ? previous.filter(item => item.id !== plan.id) : [...previous, plan]);

  const navigate = (next: ScreenType) => {
    window.location.hash = `#/${next.replace(/^s-/, '')}`;
    setScreen(next);
    window.scrollTo(0, 0);
  };

  const openDetail = (plan: PlanItem) => {
    setAskPlan(plan);
    window.location.hash = `#/detail?id=${encodeURIComponent(plan.id)}`;
    setScreen('s-detail');
    window.scrollTo(0, 0);
  };

  // 뒤로가기·주소 직접 입력도 같은 경로를 탄다.
  useEffect(() => {
    const onHashChange = () => setScreen(screenFromHash());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  // 상세 화면은 주소의 id 로 다시 불러온다. 새로고침·링크 공유에도 같은 상품이 뜬다.
  useEffect(() => {
    if (screen !== 's-detail') return;
    const id = detailIdFromHash();
    if (!id) { setDetailError('요금제 목록에서 상품을 선택해 주세요.'); return; }
    if (askPlan?.id === id) return;
    let live = true;
    setDetailError(''); setAskPlan(null);
    fetchPlan(id)
      .then(p => { if (live) setAskPlan(p); })
      .catch(() => { if (live) setDetailError('요금제 정보를 불러올 수 없습니다.'); });
    return () => { live = false; };
  }, [screen]);

  /** 대화 전체를 매번 백엔드로 보낸다. 서버는 세션을 갖지 않는다.
   *  stayOnPage: 목록을 보면서 채팅으로 물으면 목록에 머문다. 결과는 채팅의 버튼으로 연다. */
  const runRecommend = async (text: string, stayOnPage = false) => {
    if (loading) return;
    const version = ++requestVersion.current;
    const next: ChatMessage[] = [...messages, { role: 'user', content: text }];
    const before = result;
    setMessages(next);
    setPrevPlans(before?.plans ?? []);
    setLoading(true);
    setError(null);
    try {
      // 백엔드가 준 상위 3개 순위를 그대로 쓴다. 잘라내면 리포트 본문과 카드가 어긋난다.
      const data = await recommend(next);
      if (version !== requestVersion.current) return;
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
      setMessages([...next, { role: 'assistant', content: data.followupQuestion || (data.plans.length ? `${data.plans.length}개의 요금제를 찾았어요. 요금과 제공량을 비교하고 관심 요금제를 비교함에 담아보세요.` : '조건에 맞는 요금제를 찾지 못했어요. 예산이나 사용량 조건을 조정해 주세요.') }]);
      // 필수 정보가 부족하면 결과 0건 화면이 아니라, 방금 입력하던 곳에서 답을 이어 받는다.
      if (!stayOnPage) navigate(data.needsMoreInput ? 's-input' : 's-result');
    } catch (err) {
      if (version !== requestVersion.current) return;
      setError(err instanceof Error ? err.message : '알 수 없는 오류');
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  };

  const resetRecommendation = () => {
    requestVersion.current++;
    setMessages([]); setResult(null); setPrevPlans([]); setHistory([]); setError(null);
    setLoading(false); setReportPlanId(null); setDraft(''); setInputVersion(v => v + 1);
  };

  const chat = <RecommendationChat messages={messages} result={result} loading={loading} error={error} draft={draft} onDraft={setDraft}
    onSubmit={text => runRecommend(text, screen === 's-browse')} onReset={resetRecommendation} onResult={() => navigate('s-result')} />;

  const step = screen === 's-report' ? 3 : screen === 's-result' ? 2 : 1;

  const openReport = (planId: string) => {
    setReportPlanId(planId);
    navigate('s-report');
  };

  return (
    <div className={compare.length ? 'screen has-compare-dock' : 'screen'}>
      <GNB
        currentScreen={screen}
        onNavigate={navigate}
        compareCount={compare.length}
      />
      {screen !== 's-home' && screen !== 's-browse' && screen !== 's-compare' && screen !== 's-detail' && (
        <Stepper current={step} hasResult={!!result && !result.needsMoreInput} onStepClick={navigate} />
      )}

      {screen === 's-home' && <HomeScreen onNavigate={navigate} onBrowse={(category) => { setBrowseCategory(category); navigate('s-browse'); }}
        compare={compare} onToggleCompare={toggleCompare} onAskPlan={openDetail} />}
      {screen === 's-input' && <InputScreen key={inputVersion} chat={chat} loading={loading} onSubmit={runRecommend} />}
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
          compare={compare}
          onToggleCompare={toggleCompare}
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
          onToggleCompare={toggleCompare}
          onOpenCompare={() => navigate('s-compare')}
          onAskPlan={openDetail}
        />
      )}

      {screen === 's-compare' && <CompareScreen plans={compare} onRemove={toggleCompare} onClear={() => setCompare([])} onBrowse={() => navigate('s-browse')} />}
      {screen === 's-detail' && !askPlan && <div className="body"><p role="status">{detailError || '요금제 정보를 불러오는 중…'}</p><button className="btn" onClick={() => navigate('s-browse')}>목록으로</button></div>}
      {screen === 's-detail' && askPlan && <PlanDetailScreen key={askPlan.id} plan={askPlan} onBack={() => navigate('s-browse')} selected={compare.some(p => p.id === askPlan.id)} onCompare={() => toggleCompare(askPlan)} />}
      {compare.length > 0 && ['s-home', 's-browse', 's-detail'].includes(screen) && <CompareBar plans={compare} onRemove={toggleCompare} onClear={() => setCompare([])} onOpen={() => navigate('s-compare')} />}
    </div>
  );
}
