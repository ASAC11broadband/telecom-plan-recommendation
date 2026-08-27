import { ScreenType } from '../types';

export function GNB({
  currentScreen,
  onNavigate,
  hasReport,
  compareCount,
  onOpenCompare,
}: {
  currentScreen: ScreenType;
  onNavigate: (s: ScreenType) => void;
  hasReport: boolean;
  compareCount: number;
  onOpenCompare: () => void;
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
              className={currentScreen === 's-input' ? 'active' : ''}
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
              disabled={compareCount < 2}
              title={compareCount < 2 ? '전체 요금제에서 2건 이상 선택하세요' : ''}
              onClick={onOpenCompare}
            >
              비교함
              {compareCount > 0 && <span className="gnb-badge num">{compareCount}</span>}
            </button>
          </li>
          <li>
            <button
              className={currentScreen === 's-report' ? 'active' : ''}
              disabled={!hasReport}
              onClick={() => onNavigate('s-report')}
            >
              내 리포트
            </button>
          </li>
        </ul>
      </div>
      <div className="gnb-right">
        <span>문서</span>
        <div className="gnb-avatar">K</div>
      </div>
    </nav>
  );
}
