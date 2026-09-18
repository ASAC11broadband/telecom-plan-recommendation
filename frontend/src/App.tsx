import { useEffect, useState } from 'react';
import { ChatMessage, HistoryRow, PlanItem, RecommendResponse, ScreenType } from './types';
import { recommend } from './api';
import { GNB } from './components/GNB';
import { Stepper } from './components/Stepper';
import { HomeScreen } from './components/HomeScreen';
import { InputScreen } from './components/InputScreen';
import { ResultScreen } from './components/ResultScreen';
import { ReportScreen } from './components/ReportScreen';
import { BrowseScreen } from './components/BrowseScreen';
import { CompareScreen } from './components/CompareScreen';

const now = () =>
  new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });

const SCREENS: ScreenType[] = ['s-home', 's-input', 's-result', 's-report', 's-browse', 's-compare'];

/** 주소창의 해시를 화면 상태로 쓴다. 라우터를 넣지 않고도 새로고침·뒤로가기·링크 공유가 된다. */
function screenFromHash(): ScreenType {
  const hash = window.location.hash.replace(/^#\/?/, '');
  const matched = SCREENS.find((s) => s === `s-${hash}`);
  return matched ?? 's-home';
}

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
  // 비교함: 탐색 화면에서 체크한 요금제. GNB 배지와 비교 모달이 같이 쓴다.
  const [compare, setCompare] = useState<PlanItem[]>(saved.compare ?? []);
  const [reportPlanId, setReportPlanId] = useState<string | null>(saved.reportPlanId ?? null);

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

  // 뒤로가기·주소 직접 입력도 같은 경로를 탄다.
  useEffect(() => {
    const onHashChange = () => setScreen(screenFromHash());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  /** 대화 전체를 매번 백엔드로 보낸다. 서버는 세션을 갖지 않는다. */
  const runRecommend = async (text: string) => {
    if (loading) return;
    const next: ChatMessage[] = [...messages, { role: 'user', content: text }];
    const before = result;
    setMessages(next);
    setPrevPlans(before?.plans ?? []);
    setLoading(true);
    setError(null);
    navigate('s-result');
    try {
      // 백엔드가 준 상위 3개 순위를 그대로 쓴다. 잘라내면 리포트 본문과 카드가 어긋난다.
      const data = await recommend(next);
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
    } catch (err) {
      setError(err instanceof Error ? err.message : '알 수 없는 오류');
    } finally {
      setLoading(false);
    }
  };

  const step = screen === 's-report' ? 3 : screen === 's-result' ? 2 : 1;

  const openReport = (planId: string) => {
    setReportPlanId(planId);
    navigate('s-report');
  };

  const resetConsultation = () => {
    setMessages([]); setResult(null); setPrevPlans([]); setHistory([]); setError(null);
    navigate('s-input');
  };

  return (
    <div className="screen">
      <GNB
        currentScreen={screen}
        onNavigate={navigate}
        compareCount={compare.length}
      />
      {messages.length > 0 && !loading && (screen === 's-input' || screen === 's-result') && <div className="session-reset"><button className="btn btn-sm" onClick={resetConsultation}>새 상담 시작 (이전 조건 초기화)</button></div>}
      {screen !== 's-home' && screen !== 's-browse' && screen !== 's-compare' && (
        <Stepper current={step} hasResult={!!result} onStepClick={navigate} />
      )}

      {screen === 's-home' && <HomeScreen onNavigate={navigate} />}
      {screen === 's-input' && (
        <InputScreen
          messages={messages}
          result={result}
          loading={loading}
          onSubmit={runRecommend}
          onNavigate={navigate}
        />
      )}
      {screen === 's-result' && (
        <ResultScreen
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
          compare={compare}
          onToggleCompare={toggleCompare}
          onOpenCompare={() => navigate('s-compare')}
        />
      )}

      {screen === 's-compare' && <CompareScreen plans={compare} onRemove={toggleCompare} onNavigate={navigate} />}
    </div>
  );
}
