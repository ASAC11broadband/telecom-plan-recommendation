import { useEffect, useState } from 'react';
import { ScreenType, Stats } from '../types';
import { fetchStats } from '../api';

export function HomeScreen({ onNavigate }: { onNavigate: (s: ScreenType) => void }) {
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
    <div className="body">
      <div className="hero">
        <div className="kicker">모모플랜 · MoMo Plan</div>
        <h1>내 생활에 맞는 요금제,<br />추천의 근거까지 한눈에</h1>
        <p>
          통신 3사와 알뜰폰 요금제 {stats ? `${stats.total.toLocaleString()}건` : ''}을 같은 기준으로
          비교하고, 내 사용량에 맞는 추천을 받아보세요. 관심 상품은 비교함에 모아 확인할 수 있습니다.
        </p>
      </div>

      <div className="entry-grid">
        <div className="card accent entry-card">
          <span className="tag tag-accent">AI 추천</span>
          <h3>AI에게 추천받기</h3>
          <p>대화로 이용 패턴을 입력하면 조건에 맞는 후보와 선택 이유를 함께 보여드립니다.</p>
          <div className="hr" />
          <ul className="entry-list">
            <li>채팅 또는 직접 선택 입력</li>
            <li>추천 사유 자연어 설명</li>
            <li>{stats?.compareMonths ?? 12}개월 기준 총비용 비교</li>
          </ul>
          <button className="btn btn-primary btn-block" onClick={() => onNavigate('s-input')}>
            AI 추천 시작
          </button>
        </div>
        <div className="card entry-card">
          <span className="tag tag-muted">직접 탐색</span>
          <h3>전체 요금제 둘러보기</h3>
          <p>수집된 전체 요금제를 조건별로 필터링해 직접 비교하고 확인합니다.</p>
          <div className="hr" />
          <ul className="entry-list">
            <li>통신사·요금·데이터 필터</li>
            <li>테이블 정렬 및 비교</li>
            <li>선택 요금제 AI 질의 연동</li>
          </ul>
          <button className="btn btn-block" onClick={() => onNavigate('s-browse')}>
            요금제 탐색
          </button>
        </div>
      </div>

      <div className="service-steps" aria-label="서비스 이용 순서">
        <div><span>01</span><strong>탐색</strong><p>요금과 데이터 분포부터 확인</p></div>
        <div><span>02</span><strong>추천</strong><p>내 사용량과 예산에 맞게 좁히기</p></div>
        <div><span>03</span><strong>비교</strong><p>직접 찾은 상품과 추천 상품 함께 보기</p></div>
        <div><span>04</span><strong>확인</strong><p>추천 이유와 가입 전 주의사항 확인</p></div>
      </div>
      <div className="card">
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
      </div>
    </div>
  );
}
