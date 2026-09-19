import { Profile } from './types';

/** 조건을 사람이 읽는 문장으로. 추천 결과 화면과 추천 과정 설명이 같은 규칙을 쓴다.
 *  JSON 을 그대로 띄우면 "내 조건을 제대로 읽었나" 를 확인할 수 없다. */

/** 사용자가 직접 말한 조건인지, 시스템이 추정·보완한 값인지. 둘을 같은 말로 쓰면 안 된다. */
export type ConditionKind = '요청' | '추정' | '기본' | '';

export interface Condition {
  k: string;
  value: string;
  kind: ConditionKind;
}

const won = (value: number) => `${value.toLocaleString()}원`;

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

/** 순위 계산에 실제로 쓴 조건 전부. 값이 없는 항목은 아예 내보내지 않는다. */
export function describeProfile(p: Profile | null | undefined): Condition[] {
  if (!p) return [];
  const rows: Condition[] = [dataCondition(p)];

  if (p.budget_max_won || p.budget_min_won) {
    const range = p.budget_min_won
      ? `${won(p.budget_min_won)} ~ ${p.budget_max_won ? won(p.budget_max_won) : '제한 없음'}`
      : `${won(p.budget_max_won!)} 이하`;
    rows.push({ k: '월 예산', value: range, kind: '요청' });
  }
  if (p.voice_unlimited) rows.push({ k: '통화', value: '무제한', kind: '요청' });
  else if (p.min_voice_minutes)
    rows.push({ k: '통화', value: `${p.min_voice_minutes}분 이상`, kind: '요청' });
  if (p.sms_unlimited) rows.push({ k: '문자', value: '무제한', kind: '요청' });
  if (p.min_qos_mbps)
    rows.push({ k: '소진 후 속도', value: `${p.min_qos_mbps}Mbps 이상`, kind: '요청' });
  else if (p.requires_qos)
    rows.push({ k: '소진 후 속도', value: '소진 후에도 사용 가능', kind: '요청' });
  if (p.min_tethering_gb)
    rows.push({ k: '테더링', value: `${p.min_tethering_gb}GB 이상`, kind: '요청' });

  const carrier = [p.carrier_type === 'MNO' ? '통신 3사' : p.carrier_type === 'MVNO' ? '알뜰폰' : '', p.host_mno ? `${p.host_mno}망` : '', p.mvno_brand ?? '', p.network_gen ?? '']
    .filter(Boolean)
    .join(' · ');
  if (carrier) rows.push({ k: '사업자', value: carrier, kind: '요청' });
  if (p.network_preference)
    rows.push({ k: '통신 세대 우선', value: `${p.network_preference} 우선 · 다른 세대도 포함`, kind: '요청' });

  const benefits = [...(p.wanted_benefits ?? []), ...(p.wanted_benefit_categories ?? [])];
  if (benefits.length > 0) {
    const joiner = p.benefit_match_mode === 'any' ? ' 또는 ' : ' 그리고 ';
    rows.push({ k: '혜택', value: benefits.join(joiner), kind: '요청' });
  }
  if (p.min_discount_period_months)
    rows.push({ k: '할인 기간', value: `${p.min_discount_period_months}개월 이상`, kind: '요청' });

  rows.push(
    p.age_condition
      ? { k: '가입 자격', value: p.age_condition, kind: '요청' }
      : p.user_age
        ? { k: '가입 자격', value: `만 ${p.user_age}세 기준으로 확인`, kind: '요청' }
        : { k: '가입 자격', value: '연령 전용 요금제는 제외', kind: '기본' }
  );

  if (p.priorities?.length) {
    const labels: Record<string, string> = {
      price: '가격',
      data: '데이터',
      qos: '소진 후 속도',
      benefit: '혜택',
      voice: '통화',
      tethering: '테더링',
    };
    rows.push({
      k: '우선순위',
      value: p.priorities.map((key) => labels[key] ?? key).join(' > '),
      kind: '요청',
    });
  }
  return rows;
}
