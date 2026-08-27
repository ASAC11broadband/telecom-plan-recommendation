import { ScreenType } from '../types';

const STEPS: { n: 1 | 2 | 3; label: string; screen: ScreenType }[] = [
  { n: 1, label: '1. 이용 패턴 입력', screen: 's-input' },
  { n: 2, label: '2. 추천 결과', screen: 's-result' },
  { n: 3, label: '3. 상세 리포트', screen: 's-report' },
];

export function Stepper({
  current,
  hasResult,
  onStepClick,
}: {
  current: 1 | 2 | 3;
  hasResult: boolean;
  onStepClick: (s: ScreenType) => void;
}) {
  return (
    <div className="stepper">
      {STEPS.map((step, i) => {
        // 결과가 없으면 2·3단계는 볼 게 없다.
        const locked = step.n > 1 && !hasResult;
        const state = step.n === current ? 'active' : step.n < current ? 'done' : '';
        return (
          <div key={step.n} style={{ display: 'flex', alignItems: 'center' }}>
            {i > 0 && <span className="step-line" />}
            <button
              className={`step ${state}`}
              disabled={locked}
              onClick={() => onStepClick(step.screen)}
              style={{
                background: 'none',
                border: 'none',
                cursor: locked ? 'not-allowed' : 'pointer',
                opacity: locked ? 0.5 : 1,
              }}
            >
              <span className="box" />
              {step.label}
            </button>
          </div>
        );
      })}
    </div>
  );
}
