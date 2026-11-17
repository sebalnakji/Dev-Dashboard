import { useCallback, useEffect, useRef, useState } from 'react'

import { fetchSessions, postSessionAction, postStartAll, type SessionAction } from '../api/client'
import { Column, ColumnTitle } from '../components/Column'
import { ContextMeter } from '../components/ContextMeter'
import { BUTTON } from '../components/styles'
import type { SessionRow, SessionState } from '../types'

const REFRESH_MS = 3000
const NOTICE_MS = 4000
/** 연결 실패는 이유가 길고 늦게 알게 되어 더 오래 보여준다. */
const FAIL_NOTICE_MS = 10000
const CLOSED_KEY = 'closedGroups'

const byName = (a: string, b: string) => a.localeCompare(b, 'ko', { sensitivity: 'base' })

function loadClosed(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(CLOSED_KEY) ?? '[]') as string[])
  } catch {
    return new Set()
  }
}

/**
 * 가운데 칸(메인): 관리할 Claude 세션. 하나씩 연결/해제해 쓸 것만 켜 둔다.
 *
 * 버튼을 누르면 응답을 기다리는 동안 그 줄을 먼저 '연결 중/해제 중'으로 바꿔 둔다.
 * 그 사이 주기 갱신이 옛 상태로 덮어쓰지 않도록 요청 중에는 갱신을 건너뛴다.
 */
export function SessionPanel() {
  const [rows, setRows] = useState<SessionRow[] | null>(null)
  const [offline, setOffline] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [pending, setPending] = useState<Record<string, SessionState>>({})
  const [closed, setClosed] = useState(loadClosed)
  const [startingAll, setStartingAll] = useState(false)
  const busy = useRef(false)
  const shownFailures = useRef(new Set<string>())
  const noticeTimer = useRef<number | undefined>(undefined)

  const showNotice = useCallback((message: string, ms = NOTICE_MS) => {
    setNotice(message)
    window.clearTimeout(noticeTimer.current)
    noticeTimer.current = window.setTimeout(() => setNotice(null), ms)
  }, [])

  const refresh = useCallback(async () => {
    if (busy.current) return
    try {
      const next = await fetchSessions()
      setRows(next)
      setPending({})
      setOffline(false)
      // 연결 실패는 몇 초 뒤에야 알 수 있어 목록 갱신에 실려 온다.
      // 서버가 같은 실패를 1분 동안 계속 실어 보내므로 (번호, 시각)으로 한 번만 띄운다
      const fresh = next.filter((r) => r.error && !shownFailures.current.has(`${r.id}${r.errorAt}`))
      for (const r of fresh) shownFailures.current.add(`${r.id}${r.errorAt}`)
      if (fresh.length) showNotice(fresh.map((r) => r.error).join('\n'), FAIL_NOTICE_MS)
    } catch {
      setOffline(true)
    }
  }, [showNotice])

  useEffect(() => {
    // 첫 읽기도 타이머로 미룬다 — 효과 안에서 곧바로 상태를 바꾸지 않게 (react 검사 규칙)
    const first = window.setTimeout(refresh, 0)
    const timer = window.setInterval(refresh, REFRESH_MS)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(timer)
    }
  }, [refresh])

  async function act(row: SessionRow, action: SessionAction) {
    busy.current = true
    if (action !== 'foreground') {
      setPending((p) => ({ ...p, [row.id]: action === 'start' ? 'starting' : 'stopping' }))
    }
    try {
      let result = await postSessionAction(row.id, action)
      // 백그라운드 작업(서버 등)이 함께 꺼지는 해제는 한 번 물어본다
      if (result.confirm && window.confirm(result.confirm)) {
        result = await postSessionAction(row.id, action, true)
      }
      if (result.error) showNotice(result.error)
    } catch {
      showNotice('요청이 실패했습니다.')
    }
    busy.current = false
    await refresh()
  }

  async function startAll() {
    setStartingAll(true)
    busy.current = true
    try {
      const result = await postStartAll()
      if (result.error) showNotice(result.error)
    } catch {
      showNotice('요청이 실패했습니다.')
    }
    busy.current = false
    setStartingAll(false)
    await refresh()
  }

  function toggleGroup(group: string) {
    setClosed((prev) => {
      const next = new Set(prev)
      if (next.has(group)) next.delete(group)
      else next.add(group)
      try {
        localStorage.setItem(CLOSED_KEY, JSON.stringify([...next]))
      } catch {
        // 저장이 막힌 브라우저면 이번 화면에서만 기억한다
      }
      return next
    })
  }

  const stateOf = (row: SessionRow) => pending[row.id] ?? row.state
  const groups = new Map<string, SessionRow[]>()
  for (const row of rows ?? []) groups.set(row.group, [...(groups.get(row.group) ?? []), row])
  const connected = (rows ?? []).filter((r) => ['on', 'terminal'].includes(stateOf(r))).length

  return (
    <Column
      title={
        <>
          <ColumnTitle>Claude 세션</ColumnTitle>
          {rows && (
            <span className="text-xs text-gray-400">
              연결 {connected} / 전체 {rows.length}
            </span>
          )}
        </>
      }
      side={
        // 꺼진 세션이 없을 때도 누를 수 있다 — 누르면 "연결할 꺼진 세션이 없습니다" 가 뜬다.
        // 비활성으로 두니 고장처럼 보였다
        <button type="button" className={BUTTON} disabled={startingAll} onClick={startAll}>
          모두 연결
        </button>
      }
    >
      {offline && (
        <p className="mb-3.5 rounded-md bg-[#3a2621] px-2.5 py-2 text-xs text-[#f0b8a4]">
          대시보드 서버에 연결할 수 없습니다. start.bat 으로 다시 띄워 주세요.
        </p>
      )}
      {notice && (
        <p className="mb-3.5 whitespace-pre-line rounded-md bg-btn px-2.5 py-2 text-xs text-gray-100">
          {notice}
        </p>
      )}
      {rows == null && !offline && <p className="px-1.5 text-sm text-gray-500">불러오는 중…</p>}
      {rows?.length === 0 && (
        <p className="px-1.5 text-sm text-gray-500">
          config/sessions.json 에 세션이 없습니다. python -m devdash claude add 로 추가합니다.
        </p>
      )}

      {[...groups.keys()].sort(byName).map((group) => (
        <section key={group} className="mb-[18px]">
          <button
            type="button"
            onClick={() => toggleGroup(group)}
            className="flex items-center gap-1.5 px-1.5 py-1 text-xs tracking-wide text-gray-400"
          >
            {group}
            <span className={`text-[9px] transition-transform ${closed.has(group) ? '-rotate-90' : ''}`}>
              ▼
            </span>
          </button>
          {!closed.has(group) &&
            groups
              .get(group)!
              .sort((a, b) => byName(a.name, b.name))
              .map((row) => (
                <SessionLine key={row.id} row={row} state={stateOf(row)} onAct={act} />
              ))}
        </section>
      ))}
    </Column>
  )
}

const ACTION: Partial<Record<SessionState, { label: string; action?: SessionAction }>> = {
  on: { label: '해제', action: 'stop' },
  off: { label: '연결', action: 'start' },
  starting: { label: '연결 중…' },
  stopping: { label: '해제 중…' },
}

function SessionLine({
  row,
  state,
  onAct,
}: {
  row: SessionRow
  state: SessionState
  onAct: (row: SessionRow, action: SessionAction) => void
}) {
  const action = ACTION[state]
  // 포그라운드: 꺼졌으면 터미널 창에서 이어 열고, 켜졌으면 붙는다
  const fgTitle =
    state === 'on'
      ? '터미널 창에서 이 세션에 붙습니다 (창을 닫아도 세션은 백그라운드에서 계속 돕니다)'
      : '터미널 창에서 이 세션을 엽니다 (창을 닫으면 세션도 꺼집니다)'

  return (
    <div className="flex items-center gap-2.5 rounded-md px-1.5 py-[7px] hover:bg-hover">
      <span className={`min-w-0 flex-1 truncate ${state === 'off' ? 'text-gray-400' : 'text-gray-100'}`}>
        {row.name}
      </span>
      <ContextMeter tokens={row.tokens} compactions={row.compactions} />
      {/* 다른 상태엔 자리만 비워 둬서 아이콘·버튼 열이 흐트러지지 않게 한다 */}
      <span className="w-[30px] shrink-0 text-right">
        {(state === 'on' || state === 'off') && (
          <button
            type="button"
            title={fgTitle}
            onClick={() => onAct(row, 'foreground')}
            className="w-7 cursor-pointer rounded-[5px] border border-edge bg-btn py-0.5 font-mono text-[11px] font-semibold text-gray-400 hover:border-accent hover:text-accent"
          >
            &gt;_
          </button>
        )}
      </span>
      <MonitorIcon state={state} />
      <span className="w-[78px] shrink-0 text-right">
        {state === 'terminal' ? (
          // 누를 수 없는 표시라 버튼과 다르게 — 배경 없음 · 점선 · 파란 글자
          <span
            title="터미널에서 연 세션입니다. 그 터미널을 닫아야 꺼집니다."
            className="inline-block min-w-16 select-none rounded-[5px] border border-dashed border-accent px-2.5 py-[3px] text-center text-xs text-accent"
          >
            터미널
          </span>
        ) : (
          action && (
            <button
              type="button"
              disabled={!action.action}
              onClick={() => action.action && onAct(row, action.action)}
              className={`${BUTTON} ${state === 'on' ? 'hover:border-danger' : ''}`}
            >
              {action.label}
            </button>
          )
        )}
      </span>
    </div>
  )
}

/** 연결됨: 밝은 모니터 + 초록 점 · 꺼짐: 흐린 회색 · 바뀌는 중: 깜빡임 · 터미널: 화면에 >_ */
function MonitorIcon({ state }: { state: SessionState }) {
  const color = {
    on: 'text-gray-100',
    terminal: 'text-gray-100',
    off: 'text-gray-600',
    starting: 'animate-pulse text-gray-400',
    stopping: 'animate-pulse text-gray-400',
  }[state]
  return (
    <span className={`relative h-[18px] w-[22px] shrink-0 ${color}`}>
      <svg viewBox="0 0 22 18" fill="none" stroke="currentColor" strokeWidth="1.5" className="block h-[18px] w-[22px]">
        <rect x="1.5" y="1.5" width="19" height="11.5" rx="1.5" />
        <path d="M8 16.5h6M11 13v3.5" />
        {state === 'terminal' && <path d="M6 5.5l2 1.75L6 9M10 9h3" strokeWidth="1.3" />}
      </svg>
      {state === 'on' && (
        <span className="absolute right-[-2px] bottom-px h-[7px] w-[7px] rounded-full border-[1.5px] border-ground bg-on" />
      )}
    </span>
  )
}
