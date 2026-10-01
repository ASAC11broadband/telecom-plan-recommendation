import { useEffect, useRef, useState } from 'react';
import { BrowseState, ChatMessage, HistoryRow, PlanItem, RecommendResponse, ScreenType } from './types';
import { recommend, fetchPlan } from './api';
import { GNB } from './components/GNB';
import { Stepper } from './components/Stepper';
import { HomeScreen } from './components/HomeScreen';
import { ResultScreen } from './components/ResultScreen';
import { ReportScreen } from './components/ReportScreen';
import { BrowseScreen } from './components/BrowseScreen';
import { CompareScreen } from './components/CompareScreen';
import { RecommendationChat } from './components/RecommendationChat';
import { DETAIL_CHAT_KEY, PlanDetailScreen } from './components/PlanDetailScreen';
import { CompareBar } from './components/CompareBar';
import { categoryFilters } from './categories';

const now = () =>
  new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });

const SCREENS: ScreenType[] = ['s-home', 's-result', 's-report', 's-browse', 's-compare', 's-detail'];

/** 주소창의 해시를 화면 상태로 쓴다. 라우터를 넣지 않고도 새로고침·뒤로가기·링크 공유가 된다.
 *  상세 화면만 `#/detail?id=...` 처럼 쿼리를 붙인다. */
function screenFromHash(): ScreenType {
  const hash = window.location.hash.replace(/^#\/?/, '').split('?')[0];
  // 이전 조건 입력 주소로 들어와도 대화창이 있는 전체 요금제로 자연스럽게 연결한다.
  if (hash === 'input') return 's-browse';
  const matched = SCREENS.find((s) => s === `s-${hash}`);
  return matched ?? 's-home';
}

const detailIdFromHash = () => new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('id');

const SESSION_KEY = 'momoplan-session-v1';
const MAX_COMPARE_PLANS = 5;
const BENEFIT_RELAX_RE = /혜택\s*(?:유형|종류|카테고리)?\s*(?:조건)?\s*(?:은|을|는|이)?\s*(?:빼|제외|풀|없애|해제)/;
const BUDGET_RELAX_RE = /(?:예산|가격|월\s*요금)\s*(상한|하한)?\s*(?:조건)?\s*(?:은|는|을|를)?\s*(?:빼|제외|삭제|해제|없애|풀)/;
const DAILY_DATA_RELAX_RE = /(?:매일|하루|일일)\s*(?:제공\s*)?(?:데이터|용량)\s*(?:조건)?\s*(?:은|는|을|를)?\s*(?:빼|제외|삭제|해제|없애|풀)/;
const MONTHLY_BASE_DATA_RELAX_RE = /월\s*기본\s*(?:데이터|용량)\s*(?:조건)?\s*(?:은|는|을|를)?\s*(?:빼|제외|삭제|해제|없애|풀)/;
const BUDGET_SET_RE = /\d[\d,]*(?:\.\d+)?\s*(?:만|천)?\s*원\s*(?:이하|이내|미만|이상|부터|까지)/;
const SPECIFIC_BENEFIT_RE = /스마트기기|스마트워치|태블릿|OTT|넷플릭스|유튜브\s*프리미엄|음악|오디오|도서|밀리의서재|멤버십|페이백|추가\s*데이터/i;
function conditionRelaxFields(text: string): string[] {
  const fields: string[] = [];
  if (BENEFIT_RELAX_RE.test(text)) {
    fields.push(...(/혜택\s*(?:유형|종류|카테고리)/.test(text)
      ? ['wanted_benefit_categories']
      : ['wanted_benefits', 'wanted_benefit_categories']));
  }
  const budget = BUDGET_RELAX_RE.exec(text);
  if (budget?.[1] === '상한') fields.push('budget_max_won');
  else if (budget?.[1] === '하한') fields.push('budget_min_won');
  else if (budget) fields.push('budget_min_won', 'budget_max_won');
  if (DAILY_DATA_RELAX_RE.test(text)) fields.push('min_daily_data_gb');
  if (MONTHLY_BASE_DATA_RELAX_RE.test(text)) fields.push('min_monthly_base_data_gb');
  return Array.from(new Set(fields));
}
const DEFAULT_BROWSE_STATE: BrowseState = {
  category: 'all',
  view: 'cards',
  filters: categoryFilters('all'),
  sort: 'fee_asc',
  q: '',
  page: 1,
};
function readSession(): { pending?: boolean; messages?: ChatMessage[]; result?: RecommendResponse | null; compare?: PlanItem[]; history?: HistoryRow[]; prevPlans?: PlanItem[]; reportPlanId?: string | null; browse?: BrowseState; relaxedFields?: string[] } {
  try {
    const saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null');
    if (!saved || !Array.isArray(saved.messages) || !Array.isArray(saved.compare) || !Array.isArray(saved.history)) return {};
    if (saved.result && (!Array.isArray(saved.result.plans) || !Array.isArray(saved.result.assumptions))) return {};
    return {
      ...saved,
      relaxedFields: Array.isArray(saved.relaxedFields)
        ? saved.relaxedFields.filter((field: unknown) => typeof field === 'string')
        : [],
    };
  } catch { return {}; }
}

export default function App() {
  const [saved] = useState(readSession);
  const [screen, setScreen] = useState<ScreenType>(screenFromHash);
  const [messages, setMessages] = useState<ChatMessage[]>(saved.messages ?? []);
  const [result, setResult] = useState<RecommendResponse | null>(saved.result ?? null);
  // 0건 화면에서 해제한 조건은 다음 조건 해제 때도 유지한다. 일반 채팅을 새로 보내면 초기화된다.
  const [relaxedFields, setRelaxedFields] = useState<string[]>(saved.relaxedFields ?? []);
  // 재계산 시 순위·가격 변동을 보여주려고 직전 결과만 하나 들고 있는다.
  const [prevPlans, setPrevPlans] = useState<PlanItem[]>(saved.prevPlans ?? []);
  const [history, setHistory] = useState<HistoryRow[]>(saved.history ?? []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(saved.pending ? '새로고침 전에 진행하던 요청은 자동 재개되지 않습니다.' : null);
  // 비교함: 탐색 화면에서 체크한 요금제. GNB 배지와 비교함 화면이 같이 쓴다.
  const [compare, setCompare] = useState<PlanItem[]>((saved.compare ?? []).slice(0, MAX_COMPARE_PLANS));
  const [reportPlanId, setReportPlanId] = useState<string | null>(saved.reportPlanId ?? null);
  // 채팅 입력창은 화면을 옮겨도 같은 초안을 쓴다.
  const [draft, setDraft] = useState('');
  const [browseState, setBrowseState] = useState<BrowseState>(() => {
    const previous = saved.browse;
    if (!previous?.filters) return DEFAULT_BROWSE_STATE;
    const allowedSorts = ['fee_asc', 'data_desc', 'qos_desc'];
    return { ...previous, sort: allowedSorts.includes(previous.sort) ? previous.sort : 'fee_asc' };
  });
  const [askPlan, setAskPlan] = useState<PlanItem | null>(null);
  const [detailError, setDetailError] = useState('');
  // 대화 초기화를 누른 뒤 늦게 도착한 이전 응답이 화면을 덮지 않게 한다.
  const requestVersion = useRef(0);

  useEffect(() => {
    try { sessionStorage.setItem(SESSION_KEY, JSON.stringify({ messages, result, compare, history, prevPlans, reportPlanId, browse: browseState, relaxedFields, pending: loading })); }
    catch { /* 저장이 차단되거나 용량이 부족해도 현재 상담은 계속한다. */ }
  }, [messages, result, compare, history, prevPlans, reportPlanId, browseState, relaxedFields, loading]);

  const toggleCompare = (plan: PlanItem) => {
    if (compare.some(item => item.id === plan.id)) {
      setCompare(compare.filter(item => item.id !== plan.id));
      return;
    }
    if (compare.length >= MAX_COMPARE_PLANS) {
      window.alert(`비교함에는 요금제를 최대 ${MAX_COMPARE_PLANS}개까지 담을 수 있습니다.`);
      return;
    }
    setCompare([...compare, plan]);
  };

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
  const runRecommend = async (
    text: string,
    stayOnPage = false,
    relaxField?: string,
    preserveRelaxations = false,
  ) => {
    if (loading) return;
    const version = ++requestVersion.current;
    const next: ChatMessage[] = [...messages, { role: 'user', content: text }];
    const inferredRelaxations = conditionRelaxFields(text);
    const newSpecificBenefit = !inferredRelaxations.some(field => field.startsWith('wanted_benefit'))
      && SPECIFIC_BENEFIT_RE.test(text);
    const newBudgetConstraint = BUDGET_SET_RE.test(text);
    const retainedRelaxations = relaxedFields.filter(field => {
      if (field === 'wanted_benefits' || field === 'wanted_benefit_categories') return !newSpecificBenefit;
      if (field === 'budget_min_won' || field === 'budget_max_won') return !newBudgetConstraint;
      return false;
    });
    // 조건 풀기 버튼끼리는 누적하고, 사용자가 새 문장을 직접 보내면 새 의도를 우선해 초기화한다.
    const nextRelaxedFields = relaxField
      ? Array.from(new Set([...relaxedFields, relaxField]))
      : preserveRelaxations
        ? relaxedFields
        : Array.from(new Set([...retainedRelaxations, ...inferredRelaxations]));
    const before = result;
    setMessages(next);
    setRelaxedFields(nextRelaxedFields);
    setLoading(true);
    setError(null);
    try {
      // 백엔드가 준 상위 3개 순위를 그대로 쓴다. 잘라내면 리포트 본문과 카드가 어긋난다.
      const data = await recommend(next, nextRelaxedFields);
      if (version !== requestVersion.current) return;
      if (data.conversationOnly) {
        setRelaxedFields(relaxedFields);
        setMessages([...next, { role: 'assistant', content: data.assistantMessage || '' }]);
        if (!stayOnPage) navigate('s-browse');
        return;
      }
      setPrevPlans(before?.plans ?? []);
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
      // 목록에서 상담을 시작했더라도 실제 0건이면 조건 완화 버튼이 있는 결과 화면을 연다.
      // 추가 정보 질문만 대화창에 남겨 답을 이어 받는다.
      if (!stayOnPage || (!data.needsMoreInput && data.plans.length === 0)) {
        navigate(data.needsMoreInput ? 's-browse' : 's-result');
      }
    } catch (err) {
      if (version !== requestVersion.current) return;
      setError(err instanceof Error ? err.message : '알 수 없는 오류');
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  };

  const resetRecommendation = () => {
    requestVersion.current++;
    setMessages([]); setResult(null); setPrevPlans([]); setHistory([]); setRelaxedFields([]); setError(null);
    setLoading(false); setReportPlanId(null); setDraft('');
    try { sessionStorage.removeItem(DETAIL_CHAT_KEY); } catch { /* 화면 상태 초기화는 계속한다. */ }
  };

  const chat = <RecommendationChat messages={messages} result={result} loading={loading} error={error} draft={draft} onDraft={setDraft}
    onSubmit={text => runRecommend(text, screen === 's-browse')} onReset={resetRecommendation} onResult={() => navigate('s-result')} />;

  const step = screen === 's-report' ? 2 : 1;

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
      {(screen === 's-result' || screen === 's-report') && (
        <Stepper current={step} hasResult={!!result && !result.needsMoreInput} onStepClick={navigate} />
      )}

      {screen === 's-home' && <HomeScreen onBrowse={(category) => {
        setBrowseState(previous => ({ ...previous, category, filters: { ...categoryFilters(category), price: previous.filters.price }, page: 1 }));
        navigate('s-browse');
      }} compare={compare} onToggleCompare={toggleCompare} />}
      {screen === 's-result' && (
        <ResultScreen
          chat={chat}
          result={result}
          prevPlans={prevPlans}
          loading={loading}
          error={error}
          onFollowup={runRecommend}
          onRankingFollowup={(text) => runRecommend(text, false, undefined, true)}
          onRelaxCondition={(text, field) => runRecommend(text, false, field)}
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
          state={browseState}
          onStateChange={setBrowseState}
          compare={compare}
          onToggleCompare={toggleCompare}
          onOpenCompare={() => navigate('s-compare')}
        />
      )}

      {screen === 's-compare' && <CompareScreen plans={compare} onRemove={toggleCompare} onClear={() => setCompare([])} onBrowse={() => navigate('s-browse')} />}
      {screen === 's-detail' && !askPlan && <div className="body"><p role="status">{detailError || '요금제 정보를 불러오는 중…'}</p><button className="btn" onClick={() => navigate('s-browse')}>목록으로</button></div>}
      {screen === 's-detail' && askPlan && <PlanDetailScreen key={askPlan.id} plan={askPlan} onBack={() => navigate('s-browse')} selected={compare.some(p => p.id === askPlan.id)} onCompare={() => toggleCompare(askPlan)} />}
      {compare.length > 0 && ['s-home', 's-browse', 's-detail'].includes(screen) && <CompareBar plans={compare} onRemove={toggleCompare} onClear={() => setCompare([])} onOpen={() => navigate('s-compare')} />}
    </div>
  );
}
