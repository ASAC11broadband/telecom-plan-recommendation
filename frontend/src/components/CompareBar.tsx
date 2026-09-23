import { PlanItem } from '../types';
export function CompareBar({plans,onRemove,onClear,onOpen}:{plans:PlanItem[];onRemove:(p:PlanItem)=>void;onClear:()=>void;onOpen:()=>void}) {
 return <aside className="compare-dock" aria-label="선택한 요금제"><div className="dock-heading"><strong>선택한 요금제 {plans.length}개</strong><button className="text-btn" onClick={onClear}>전체 비우기</button></div><div className="dock-items">{plans.map(p=><span key={p.id}>{p.name}<button aria-label={p.name+' 비교함에서 삭제'} onClick={()=>onRemove(p)}>×</button></span>)}</div><button className="btn btn-primary" onClick={onOpen}>비교하기 →</button></aside>;
}
