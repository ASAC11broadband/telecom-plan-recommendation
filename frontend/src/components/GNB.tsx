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
        <button
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
        </button>
        <ul className="gnb-menu">
          <li>
            <button
              className={['s-browse', 's-result', 's-report'].includes(currentScreen) ? 'active' : ''}
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
              비교함
              {compareCount > 0 && <span className="gnb-badge num">{compareCount}</span>}
            </button>
          </li>
        </ul>
      </div>
    </nav>
  );
}
