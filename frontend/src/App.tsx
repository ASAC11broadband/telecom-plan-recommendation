import { useState } from 'react';
import { ChatMessage, HistoryRow, PlanItem, RecommendResponse, ScreenType } from './types';
import { recommend } from './api';
import { GNB } from './components/GNB';
import { Stepper } from './components/Stepper';
import { HomeScreen } from './components/HomeScreen';
import { InputScreen } from './components/InputScreen';
import { ResultScreen } from './components/ResultScreen';
import { ReportScreen } from './components/ReportScreen';
import { BrowseScreen } from './components/BrowseScreen';
import { AiQueryModal, CompareModal } from './components/Modal';

/** 화면에 노출할 추천 건수. 백엔드는 5건까지 주지만 3칼럼 그리드에 맞춰 3건만 쓴다. */
const TOP_N = 3;

const now = () =>
  new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });

export default function App() {
  const [screen, setScreen] = useState<ScreenType>('s-home');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [result, setResult] = useState<RecommendResponse | null>(null);
  // 재계산 시 순위·가격 변동을 보여주려고 직전 결과만 하나 들고 있는다.
  const [prevPlans, setPrevPlans] = useState<PlanItem[]>([]);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 비교함: 탐색 화면에서 체크한 요금제. GNB 배지와 비교 모달이 같이 쓴다.
  const [compare, setCompare] = useState<PlanItem[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [askPlan, setAskPlan] = useState<PlanItem | null>(null);

  const navigate = (next: ScreenType) => {
    setScreen(next);
    window.scrollTo(0, 0);
  };

  /** 대화 전체를 매번 백엔드로 보낸다. 서버는 세션을 갖지 않는다. */
  const runRecommend = async (text: string) => {
    const next: ChatMessage[] = [...messages, { role: 'user', content: text }];
    const before = result;
    setMessages(next);
    setPrevPlans(before?.plans ?? []);
    setLoading(true);
    setError(null);
    navigate('s-result');
    try {
      const data = await recommend(next);
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
      setError(err instanceof Error ? err.message : '알 수 없는 오류');
    } finally {
      setLoading(false);
    }
  };

  const step = screen === 's-report' ? 3 : screen === 's-result' ? 2 : 1;

  return (
    <div className="screen">
      <GNB
        currentScreen={screen}
        onNavigate={navigate}
        hasReport={!!result}
        compareCount={compare.length}
        onOpenCompare={() => setCompareOpen(true)}
      />
      {screen !== 's-home' && screen !== 's-browse' && (
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
          onNavigate={navigate}
        />
      )}
      {screen === 's-report' && <ReportScreen result={result} onNavigate={navigate} />}
      {screen === 's-browse' && (
        <BrowseScreen
          compare={compare}
          onToggleCompare={(plan) =>
            setCompare((prev) =>
              prev.some((p) => p.id === plan.id)
                ? prev.filter((p) => p.id !== plan.id)
                : [...prev, plan]
            )
          }
          onOpenCompare={() => setCompareOpen(true)}
          onAskPlan={setAskPlan}
        />
      )}

      {compareOpen && compare.length > 0 && (
        <CompareModal plans={compare} onClose={() => setCompareOpen(false)} />
      )}
      {askPlan && <AiQueryModal plan={askPlan} onClose={() => setAskPlan(null)} />}
    </div>
  );
}
