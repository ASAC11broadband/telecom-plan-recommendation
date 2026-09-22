import { ScreenType } from '../types';

export function GNB({
  currentScreen,
  onNavigate,
  compareCount,
}: {
  currentScreen: ScreenType;
  onNavigate: (s: ScreenType) => void;
  compareCount: number;
}) {
  return (
    <nav className="gnb">
      <div className="gnb-left">
        <span
          className="gnb-logo"
          style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 7 }}
          onClick={() => onNavigate('s-home')}
        >
          <span
            style={{
              width: 9,
              height: 9,
              borderRadius: '50%',
              background: 'var(--accent)',
              display: 'inline-block',
            }}
          />
          모모플랜
          <span style={{ fontSize: 'var(--fs-11)', fontWeight: 400, color: 'var(--t3)' }}>
            MoMo Plan
          </span>
        </span>
        <ul className="gnb-menu">
          <li>
            <button
              className={(currentScreen === 's-input' || currentScreen === 's-result' || currentScreen === 's-report') ? 'active' : ''}
              onClick={() => onNavigate('s-input')}
            >
              AI 추천
            </button>
          </li>
          <li>
            <button
              className={currentScreen === 's-browse' ? 'active' : ''}
              onClick={() => onNavigate('s-browse')}
            >
              전체 요금제
            </button>
          </li>
          <li>
            <button
              className={currentScreen === 's-compare' ? 'active' : ''}
              onClick={() => onNavigate('s-compare')}
            >
              내 비교함 ({compareCount})
            </button>
          </li>
        </ul>
      </div>
      <div className="gnb-right">
        <a href="https://github.com/ASAC11broadband" target="_blank" rel="noreferrer">
          프로젝트 정보
        </a>
      </div>
    </nav>
  );
}
