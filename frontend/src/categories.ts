import { BrowseFilters, EMPTY_FILTERS } from './types';
export const emptyFilters = (): BrowseFilters => ({ ...EMPTY_FILTERS });
export const categories = [
  { id: 'all', icon: '✦', title: '전체 요금제', description: '모든 선택지를 한눈에', group: 'data', keys: [] },
  { id: 'video', icon: '▶', title: '영상 시청형', description: '30GB 이상 · 무제한', group: 'data', keys: ['30to50', '50to100', 'gte100', 'unlimited'] },
  { id: 'daily', icon: '◉', title: '일상 사용형', description: 'SNS · 지도, 10~30GB', group: 'data', keys: ['10to30'] },
  { id: 'light', icon: '☁', title: '가벼운 사용형', description: '와이파이 중심, 10GB 미만', group: 'data', keys: ['lt10'] },
  { id: 'call', icon: '☎', title: '통화 중심형', description: '음성통화 무제한', group: 'voice', keys: ['unlimited'] },
  { id: 'ott', icon: '▣', title: 'OTT 혜택형', description: 'OTT 결합 옵션 포함', group: 'flags', keys: ['addon'] },
] as const;
export function categoryFilters(id: string): BrowseFilters {
  const c = categories.find(c => c.id === id) ?? categories[0];
  return { ...emptyFilters(), [c.group]: [...c.keys] };
}
