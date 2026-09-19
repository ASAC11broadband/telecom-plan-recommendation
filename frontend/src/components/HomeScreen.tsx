import { useEffect, useState } from 'react';
import { ScreenType, Stats } from '../types';
import { fetchStats } from '../api';

const SCENARIOS = [
  {
    label: '가성비형',
    title: '월 3만원 이하, 데이터 20GB',
    description: '가격 부담은 줄이고 기본 데이터는 충분히 쓰고 싶은 경우',
    query: '월 3만원 이하, 데이터 20GB 이상으로 가성비 좋은 요금제 추천해줘.',
  },
  {
    label: '영상 시청형',
    title: '영상 시청이 많은 사용자',
    description: '무제한·소진 후 속도·프로모션 종료 뒤 비용까지 함께 비교',
    query: '영상 시청을 많이 해. 데이터 무제한이고 소진 후 속도가 유지되는 요금제로 추천해줘.',
  },
  {
    label: '5G 우선형',
    title: '5G를 유지하며 비용 절약',
    description: '5G 조건은 유지하고 예산 안에서 대안을 찾고 싶은 경우',
    query: '월 4만원 이하, 5G 요금제만 추천해줘.',
  },
];

export function HomeScreen({
  onNavigate,
  onRecommend,
}: {
  onNavigate: (s: ScreenType) => void;
  onRecommend: (query: string) => void;
}) {
  const [stats, setStats] = useState<Stats | null>(null);

  // 커버리지 숫자는 CSV 실측값. 실패해도 화면은 그대로 보여준다.
  useEffect(() => {
    fetchStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  const cells = [
    [stats?.total, '전체 요금제'],
    [stats?.mvno, '알뜰폰(MVNO)'],
    [stats?.mno, '통신 3사(MNO)'],
    [stats?.brands, '알뜰폰 브랜드'],
    [stats ? `${stats.compareMonths}개월` : undefined, '비용 비교 기준'],
  ] as const;

  return (
    <main className="body home">
      <section className="hero">
        <div className="kicker">모모플랜 · MoMo Plan</div>
        <h1>내 생활에 맞는 요금제,<br />추천의 근거까지 한눈에</h1>
        <p>
          통신 3사와 알뜰폰 요금제 {stats ? `${stats.total.toLocaleString()}건` : ''}을 같은 기준으로
          비교합니다. 예산과 데이터 사용량만 알려주면 바로 후보를 좁혀드려요.
        </p>
        <div className="hero-actions">
          <button className="btn btn-primary" onClick={() => onNavigate('s-input')}>30초 맞춤 추천 시작</button>
          <button className="btn" onClick={() => onNavigate('s-browse')}>전체 요금제 탐색</button>
        </div>
        <div className="hero-note">추천 뒤에는 가격·데이터·통신 세대 기준을 바꿔 직접 비교할 수 있습니다.</div>
      </section>

      <section className="home-scenarios" aria-labelledby="scenario-title">
        <div className="row-between">
          <div>
            <h2 id="scenario-title">자주 찾는 조건으로 바로 시작</h2>
            <p>가장 가까운 상황을 고르면 조건을 채워서 추천을 시작합니다.</p>
          </div>
          <button className="btn btn-sm" onClick={() => onNavigate('s-input')}>조건 직접 입력</button>
        </div>
        <div className="scenario-grid">
          {SCENARIOS.map((scenario) => (
            <article className="card scenario-card" key={scenario.label}>
              <span className="tag tag-muted">{scenario.label}</span>
              <h3>{scenario.title}</h3>
              <p>{scenario.description}</p>
              <button className="btn btn-sm" onClick={() => onRecommend(scenario.query)}>
                이 조건으로 추천받기
              </button>
            </article>
          ))}
        </div>
      </section>
      <section className="card home-coverage">
        <div
          className="row-between"
          style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)' }}
        >
          <strong style={{ fontSize: 'var(--fs-13)' }}>비교할 수 있는 요금제</strong>
          <span style={{ fontSize: 'var(--fs-11)', color: 'var(--t3)' }}>
            데이터 기준 {stats ? stats.dataAsOf : '—'}
          </span>
        </div>
        <div className="coverage-grid">
          {cells.map(([value, label]) => (
            <div className="coverage-cell" key={label}>
              <div className="n num">
                {value === undefined ? '—' : typeof value === 'number' ? value.toLocaleString() : value}
              </div>
              <div className="l">{label}</div>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
