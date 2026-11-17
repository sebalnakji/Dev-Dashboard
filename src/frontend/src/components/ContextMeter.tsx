/**
 * 세션의 컨텍스트 사용률과 압축 횟수 (T-21): "85% · 압축 4".
 * 모니터링 › Claude 세션 칸과 프로젝트 › 세션 목록이 같이 쓴다.
 *
 * - %는 무조건 100만 토큰 기준 (사용자 결정 — 기록에 창 크기가 없고, 쓰는 세션이 모두 100만 창이다)
 * - %: 80% 이상 노랑 · 90% 이상 빨강 (곧 자동 압축된다)
 * - 압축: 0 · 1번 초록 · 2번 노랑 · 3번 이상 빨강 (사용자 결정)
 */

const WINDOW = 1_000_000

const YELLOW = 'text-[#e3b75f]'
const RED = 'text-[#f06368]'
const GREEN = 'text-on'
const DIM = 'text-gray-600'

function percentColor(p: number) {
  if (p >= 90) return RED
  if (p >= 80) return YELLOW
  return 'text-gray-500'
}

function compactColor(n: number) {
  if (n >= 3) return RED
  if (n === 2) return YELLOW
  return GREEN
}

export function ContextMeter({ tokens, compactions }: { tokens?: number | null; compactions?: number }) {
  if (tokens == null && !compactions) return null
  const percent = tokens == null ? null : Math.min(100, Math.round((tokens / WINDOW) * 100))
  return (
    <span className="shrink-0 text-xs tabular-nums">
      {percent != null && (
        <span title={`컨텍스트 ${tokens!.toLocaleString()} / ${WINDOW.toLocaleString()} 토큰`} className={percentColor(percent)}>
          {percent}%
        </span>
      )}
      {percent != null && <span className={DIM}> · </span>}
      <span title={`대화를 ${compactions ?? 0}번 압축했습니다`} className={compactColor(compactions ?? 0)}>
        압축 {compactions ?? 0}
      </span>
    </span>
  )
}
