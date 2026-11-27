import { memo, useCallback, useEffect, useMemo, useState } from 'react'

import { fetchFileIndex, fetchTasks } from '../api/client'
import { ContextMeter } from '../components/ContextMeter'
import { Modal } from '../components/Modal'
import { statusOf } from '../components/sessionStatus'
import { BUTTON } from '../components/styles'
import { ChatView } from '../projects/ChatView'
import { openProjectAt } from '../projects/nav'
import { Menu } from '../projects/ProjectBar'
import type { ScreenSession } from '../types'

const NO_FILES: string[] = []
const NO_OPEN = () => {}

/** 프로젝트의 .md 목록 (문서 속 링크 찾기 — 서버가 폴더 전체를 훑어 기억한 색인). 같은 프로젝트를 여러 칸이 보여도 1분에 한 번만 받는다. */
const mdCache = new Map<string, { at: number; files: Promise<string[]> }>()
function useMdFiles(projectKey: string | null): string[] {
  const [files, setFiles] = useState<{ key: string; list: string[] } | null>(null)
  useEffect(() => {
    if (!projectKey) return
    let hit = mdCache.get(projectKey)
    if (!hit || Date.now() - hit.at > 60_000) {
      const files = fetchFileIndex(projectKey)
        .then((r) => r.md)
        .catch(() => [] as string[])
      hit = { at: Date.now(), files }
      mdCache.set(projectKey, hit)
    }
    let alive = true
    hit.files.then((list) => alive && setFiles({ key: projectKey, list }))
    return () => {
      alive = false
    }
  }, [projectKey])
  return projectKey && files?.key === projectKey ? files.list : NO_FILES
}

/**
 * 화면분할의 칸 하나 = 세션 하나. 위에 한 줄 머리줄, 아래는 대화(따로 스크롤).
 * 대화는 프로젝트 탭의 ChatView 그대로다 (5초마다 새로 받고, 맨 아래였으면 따라 내려간다).
 * 파일 · 할 일 링크는 그 세션이 속한 프로젝트 탭에서 연다. 속한 프로젝트가 없으면 글자로만 보인다.
 */
export const SplitPane = memo(function SplitPane({
  index,
  sessionId,
  session,
  loaded,
  onPick,
  onClear,
}: {
  index: number
  /** 칸에 넣어 둔 세션의 목록 번호 */
  sessionId: string | null
  /** 그 세션의 지금 줄. 목록에 없으면(삭제 등) undefined */
  session: ScreenSession | undefined
  /** 세션 목록을 한 번이라도 받았나 — 받기 전에는 '없어진 세션' 으로 보이지 않게 */
  loaded: boolean
  onPick: (index: number) => void
  onClear: (index: number) => void
}) {
  const project = session?.project ?? null
  const mdFiles = useMdFiles(project)
  const [menu, setMenu] = useState(false)
  const closeMenu = useCallback(() => setMenu(false), [])
  const openFile = useCallback(
    (path: string) => project && openProjectAt(project, { view: 'files', selectedPath: path }),
    [project],
  )
  const openTask = useCallback(
    (number: number) => {
      if (!project) return
      fetchTasks(project)
        .then((list) => list.find((t) => t.number === number)?.file ?? null)
        .catch(() => null)
        .then((file) => openProjectAt(project, { view: 'tasks', chosenFile: file }))
    },
    [project],
  )

  if (!sessionId) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-gray-500">
        <span>빈 칸</span>
        <button type="button" className={BUTTON} onClick={() => onPick(index)}>
          세션 고르기
        </button>
      </div>
    )
  }
  if (!session) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-gray-500">
        {loaded ? (
          <>
            <span>
              없어진 세션 <span className="text-gray-600">({sessionId})</span>
            </span>
            <button type="button" className={BUTTON} onClick={() => onPick(index)}>
              다시 고르기
            </button>
          </>
        ) : (
          <span>불러오는 중…</span>
        )}
      </div>
    )
  }

  const status = statusOf(session)
  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 머리줄 한 줄: 이름 · 그룹 · 상태 · 게이지 · ⋮(바꾸기 · 비우기). 좁으면 이름 · 그룹이 먼저 줄어든다 */}
      <header className="relative flex h-10 shrink-0 items-center gap-2.5 border-b border-edge pr-1.5 pl-4">
        <span className="min-w-0 truncate text-sm font-semibold text-gray-100" title={session.name}>
          {session.name}
        </span>
        {session.group && (
          <span className="min-w-0 shrink-[2] truncate text-xs text-gray-500" title={`그룹: ${session.group}`}>
            {session.group}
          </span>
        )}
        <span className="flex-1" />
        <span className={`flex shrink-0 items-center gap-1.5 text-xs ${status.text}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${status.dot}`} />
          {status.label}
        </span>
        <ContextMeter tokens={session.tokens} compactions={session.compactions} />
        <button
          type="button"
          title="세션 바꾸기 · 칸 비우기"
          onClick={() => setMenu(!menu)}
          className="shrink-0 cursor-pointer rounded px-1.5 py-1 text-gray-500 hover:text-gray-100"
        >
          ⋮
        </button>
        {menu && (
          <Menu
            onClose={closeMenu}
            items={[
              { label: '세션 바꾸기', run: () => onPick(index) },
              { label: '칸 비우기', run: () => onClear(index) },
            ]}
          />
        )}
      </header>
      {/* data-scroll: ChatView 가 스크롤을 맞출 상자 (칸마다 하나) */}
      <div data-scroll className="min-h-0 flex-1 overflow-auto px-5 pt-1">
        <ChatView
          key={session.id}
          sessionId={session.id}
          projectKey={project ?? ''}
          working={session.activity === 'working'}
          mdFiles={project ? mdFiles : NO_FILES}
          onOpenFile={project ? openFile : NO_OPEN}
          onOpenTask={project ? openTask : NO_OPEN}
        />
      </div>
    </div>
  )
})

/**
 * [세션 고르기] 창: 그룹별 세션 목록. 이 화면의 다른 칸에 이미 넣은 세션은 고를 수 없다
 * (2분할이라 숨어 있는 3 · 4번 칸 것도). 터미널에서 연 세션은 목록 번호가 없어 처음부터 없다.
 */
export function SessionPicker({
  sessions,
  taken,
  current,
  onPick,
  onClose,
}: {
  sessions: ScreenSession[]
  /** 다른 칸에 넣은 세션 → 몇 번 칸 */
  taken: Map<string, number>
  /** 이 칸에 지금 있는 세션 */
  current: string | null
  onPick: (id: string) => void
  onClose: () => void
}) {
  const groups = useMemo(() => {
    const out = new Map<string, ScreenSession[]>()
    for (const s of sessions) {
      const g = s.group ?? '분류 안 됨'
      out.set(g, [...(out.get(g) ?? []), s])
    }
    return [...out.entries()]
  }, [sessions])

  return (
    <Modal title="세션 고르기" onClose={onClose}>
      {groups.length === 0 && <p className="text-sm text-gray-500">백그라운드 세션이 없습니다.</p>}
      {groups.map(([group, list]) => (
        <section key={group} className="mb-4 last:mb-0">
          <h4 className="mb-1 px-1 text-xs tracking-wide text-gray-500">{group}</h4>
          {list.map((s) => {
            const other = taken.get(s.id)
            const status = statusOf(s)
            return (
              <button
                key={s.id}
                type="button"
                disabled={other != null}
                onClick={() => onPick(s.id)}
                className={`flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm ${
                  other != null
                    ? 'cursor-default text-gray-600'
                    : `cursor-pointer hover:bg-hover ${s.id === current ? 'bg-btn text-gray-100' : 'text-gray-200'}`
                }`}
              >
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${other != null ? 'bg-gray-700' : status.dot}`} />
                <span className="min-w-0 flex-1 truncate">{s.name}</span>
                <span className="shrink-0 text-xs text-gray-500">
                  {other != null ? `${other + 1}번 칸에 있음` : s.id === current ? '지금 칸' : status.label}
                </span>
              </button>
            )
          })}
        </section>
      ))}
    </Modal>
  )
}
