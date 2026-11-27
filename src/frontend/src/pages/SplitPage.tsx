import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { addScreen, fetchScreenSessions, fetchScreens, patchScreen, setScreenCell } from '../api/client'
import { BUTTON, OFFLINE, WARNING, reasonOf } from '../components/styles'
import { goToSplit, replaceSplit } from '../hooks/useHashRoute'
import { useTabActive } from '../hooks/useTabActive'
import { rememberScreen } from '../projects/remember'
import { NameDialog, ScreenBar } from '../split/ScreenBar'
import { SessionPicker, SplitPane } from '../split/SplitPane'
import type { Screen, ScreenSession } from '../types'

/** 칸 머리줄(상태 · 게이지)을 새로 받는 간격. 대화는 칸마다 ChatView 가 따로 받는다. */
const SESSIONS_MS = 5000
/** 화면 목록 — 다른 브라우저 탭에서 고친 것을 따라온다 */
const SCREENS_MS = 15000

/**
 * 화면분할 탭: 세션 여러 개의 상태와 대화를 한 화면에서 나란히 본다 (보기 전용).
 *
 *   [업무 2분할 ⋮] [공부 4분할 ⋮] …              [2분할|4분할] [+ 화면]
 *   ┌ 이름 · 그룹 · 상태 · 85% · 압축 1 [바꾸기][비우기] ┬ … ┐
 *   │ 대화 (칸마다 따로 스크롤)                           │   │
 *
 * 화면마다 칸은 넷이고 2분할이면 앞의 둘만 보인다 — 분할을 바꿔도 칸 설정은 그대로다.
 */
export function SplitPage({ selectedId }: { selectedId: string | null }) {
  const [screens, setScreens] = useState<Screen[] | null>(null)
  const [sessions, setSessions] = useState<ScreenSession[] | null>(null)
  const [offline, setOffline] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [picking, setPicking] = useState<number | null>(null)
  const dragging = useRef(false)

  const loadScreens = useCallback(() => {
    if (dragging.current) return
    fetchScreens()
      .then((r) => {
        setScreens(r.screens)
        setOffline(false)
      })
      .catch(() => setOffline(true))
  }, [])
  const loadSessions = useCallback(() => {
    fetchScreenSessions()
      .then(setSessions)
      .catch(() => setOffline(true))
  }, [])

  // 숨겨진 탭이면 묻지 않는다. 다시 보이면 곧바로 한 번
  const active = useTabActive()
  useEffect(() => {
    if (!active) return
    const first = window.setTimeout(() => {
      loadScreens()
      loadSessions()
    }, 0)
    const a = window.setInterval(() => !document.hidden && loadSessions(), SESSIONS_MS)
    const b = window.setInterval(() => !document.hidden && loadScreens(), SCREENS_MS)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(a)
      window.clearInterval(b)
    }
  }, [active, loadScreens, loadSessions])

  // 주소에 화면이 없거나 지운 화면이면 첫째를 연다 (화면이 스스로 정한 이동이라 이력을 덮어쓴다)
  const current = screens?.find((s) => s.id === selectedId) ?? null
  useEffect(() => {
    if (active && screens && screens.length > 0 && !current) replaceSplit(screens[0].id)
  }, [screens, current, active])
  useEffect(() => {
    if (current) rememberScreen(current.id)
  }, [current])

  const byId = useMemo(() => new Map((sessions ?? []).map((s) => [s.id, s])), [sessions])

  /** 서버가 돌려준 목록으로 바꾸고, 실패면 이유를 띄운다 */
  const apply = useCallback(
    async (request: Promise<{ screens: Screen[]; error?: string }>) => {
      const r = await request.catch((e) => ({ error: reasonOf(e), screens: null }))
      if (r.error || !r.screens) setError(r.error ?? '바꾸지 못했습니다')
      else {
        setError(null)
        setScreens(r.screens)
      }
    },
    [],
  )
  const pick = useCallback((index: number) => setPicking(index), [])
  const clear = useCallback(
    (index: number) => current && apply(setScreenCell(current.id, index, null)),
    [current, apply],
  )

  const shown = current ? current.cells.slice(0, current.layout) : []
  const taken = useMemo(() => {
    const out = new Map<string, number>()
    current?.cells.forEach((c, i) => c && i !== picking && out.set(c, i))
    return out
  }, [current, picking])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="relative z-10 flex shrink-0 flex-wrap items-center gap-1 border-b border-edge px-4 py-2 min-[900px]:px-[30px]">
        {screens && (
          <ScreenBar
            screens={screens}
            currentId={current?.id ?? null}
            onChange={setScreens}
            onDragging={(d) => (dragging.current = d)}
          />
        )}
        <span className="flex-1" />
        {current && (
          <span className="mr-2 flex shrink-0 overflow-hidden rounded-[5px] border border-edge" role="group">
            {([2, 4] as const).map((n) => (
              <button
                key={n}
                type="button"
                aria-pressed={current.layout === n}
                onClick={() => current.layout !== n && apply(patchScreen(current.id, { layout: n }))}
                className={`cursor-pointer px-2.5 py-[3px] text-xs ${
                  current.layout === n ? 'bg-btn-hover text-gray-100' : 'bg-btn text-gray-400 hover:text-gray-100'
                }`}
              >
                {n}분할
              </button>
            ))}
          </span>
        )}
        <button type="button" className={`${BUTTON} shrink-0`} onClick={() => setAdding(true)}>
          + 화면
        </button>
      </div>

      {offline && <p className={`mx-4 mt-3 ${WARNING}`}>{OFFLINE}</p>}
      {error && <p className={`mx-4 mt-3 ${WARNING}`}>{error}</p>}
      {screens?.length === 0 && (
        <p className="m-6 text-sm text-gray-400">
          화면이 없습니다. [+ 화면] 으로 만들고, 칸마다 볼 세션을 고르세요.
        </p>
      )}

      {current && (
        // 넓은 창: 화면 높이에 꽉 맞춰 2칸(좌우) · 4칸(2×2), 칸마다 따로 스크롤.
        // 좁은 창: 위아래로 쌓고 페이지가 스크롤한다 (칸 높이는 창 높이의 대부분)
        <div
          className={`grid min-h-0 flex-1 grid-cols-1 overflow-auto min-[900px]:grid-cols-2 min-[900px]:overflow-hidden ${
            current.layout === 4 ? 'min-[900px]:grid-rows-2' : 'min-[900px]:grid-rows-1'
          }`}
        >
          {shown.map((cell, i) => (
            <div
              key={i}
              className={`min-h-0 min-w-0 border-divider max-[899px]:h-[78vh] ${i > 0 ? 'max-[899px]:border-t' : ''} ${
                i % 2 === 1 ? 'min-[900px]:border-l' : ''
              } ${i >= 2 ? 'min-[900px]:border-t' : ''}`}
            >
              <SplitPane
                index={i}
                sessionId={cell}
                session={cell ? byId.get(cell) : undefined}
                loaded={sessions != null}
                onPick={pick}
                onClear={clear}
              />
            </div>
          ))}
        </div>
      )}

      {adding && (
        <NameDialog
          title="화면 추가"
          action="추가"
          onClose={() => setAdding(false)}
          onSubmit={(name) => addScreen(name)}
          onDone={(r) => {
            setAdding(false)
            setScreens(r.screens)
            if (r.made) goToSplit(r.made.id)
          }}
        />
      )}
      {picking != null && current && (
        <SessionPicker
          sessions={sessions ?? []}
          taken={taken}
          current={current.cells[picking]}
          onClose={() => setPicking(null)}
          onPick={(id) => {
            const index = picking
            setPicking(null)
            apply(setScreenCell(current.id, index, id))
          }}
        />
      )}
    </div>
  )
}
