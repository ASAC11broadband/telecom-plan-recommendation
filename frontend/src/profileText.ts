import { Profile } from './types';

/** 조건을 사람이 읽는 문장으로. 추천 결과 화면의 조건 칩이 이 규칙을 쓴다.
 *  JSON 을 그대로 띄우면 "내 조건을 제대로 읽었나" 를 확인할 수 없다. */

/** 사용자가 직접 말한 조건인지, 시스템이 추정·보완한 값인지. 둘을 같은 말로 쓰면 안 된다. */
export type ConditionKind = '요청' | '추정' | '기본' | '';

export interface Condition {
  k: string;
  value: string;
  kind: ConditionKind;
}

export function dataCondition(p: Profile): Condition {
  if (p.data_unlimited)
    return { k: '데이터', value: p.require_full_unlimited ? '완전 무제한' : '무제한', kind: '요청' };
  if (p.min_data_gb)
    return {
      k: '데이터',
      value: `${p.min_data_gb}GB 이상${p.max_data_gb ? ` · ${p.max_data_gb}GB 이하` : ''}`,
      kind: '요청',
    };
  if (p.max_data_gb) return { k: '데이터', value: `${p.max_data_gb}GB 이하`, kind: '요청' };
  if (p.target_data_gb) return { k: '데이터', value: `${p.target_data_gb}GB 내외`, kind: '요청' };
  if (p.estimated_monthly_data_gb)
    return { k: '데이터', value: `월 ${p.estimated_monthly_data_gb}GB 예상`, kind: '추정' };
  return { k: '데이터', value: '미지정', kind: '' };
}
