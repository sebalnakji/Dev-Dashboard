import type { SessionState } from '../types'

/**
 * 세션이 지금 무엇을 하나: 점 색 · 글 · 글 색.
 * 프로젝트 탭 세션 목록과 화면분할 칸 머리줄이 같이 쓴다 (같은 세션이 탭마다 다른 말로 보이지 않게).
 */
export function statusOf(s: {
  state: SessionState
  activity?: 'working' | 'blocked' | 'done' | null
}): { dot: string; label: string; text: string } {
  if (s.state === 'starting') return { dot: 'bg-gray-600 animate-pulse', label: '연결 중', text: 'text-gray-500' }
  if (s.state === 'stopping') return { dot: 'bg-gray-600 animate-pulse', label: '해제 중', text: 'text-gray-500' }
  if (s.state === 'deleting') return { dot: 'bg-gray-600 animate-pulse', label: '삭제 중', text: 'text-gray-500' }
  if (s.state === 'off') return { dot: 'bg-gray-700', label: '꺼짐', text: 'text-gray-600' }
  if (s.activity === 'working') return { dot: 'bg-accent animate-pulse', label: '답변 중', text: 'text-accent' }
  // 확인이 필요할 때의 색: 위험(주황)과 구분되는 노랑
  if (s.activity === 'blocked') return { dot: 'bg-[#e3b75f]', label: '확인 필요', text: 'text-[#e3b75f]' }
  return { dot: 'bg-on', label: '쉬는 중', text: 'text-gray-500' }
}
