import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { fetchFile, fetchFileHistory, fetchRemote, imageUrl } from '../api/client'
import { BUTTON, WARNING, agoIso, reasonOf } from '../components/styles'
import { useNow } from '../hooks/useNow'
import type { FileContent, FileHistoryRow, ProjectRow, TaskRow } from '../types'
import { ChatView } from './ChatView'
import { CodeView } from './CodeView'
import { FileTree } from './FileTree'
import { langOfPath } from './lang'
import { MarkdownView } from './MarkdownView'
import { CommitView, GitList } from './GitView'
import { gitSeen, lastView, rememberGitSeen, rememberView, type ProjectViewName } from './remember'
import { SessionList, SessionPicker } from './SessionList'
import { TaskDoc, TaskList } from './TaskPanels'
import { defaultTask, useProjectData, useTaskDoc, type Slot } from './useProjectData'

/**
 * 한 프로젝트 화면. 다섯 칸을 한 화면에 펼치지 않고 [할 일] [파일] [git] [세션] 중 고른 하나만
 * 화면을 다 쓴다 (T-3). 작은 서브 모니터에서 칸마다 폭이 좁아 글자를 키울 수 없고 답답했다.
 *
 *   늘 보는 것   [할 일]  왼쪽 할 일 목록 · 오른쪽 넓은 정리 문서 (고르고 읽는 일이 짝이라 두 칸)
 *   찾아보는 것  [파일]   왼쪽 파일 구조 · 오른쪽 고른 파일을 바꾼 할 일들
 *               [git]    왼쪽 최근 커밋(동료 것 포함) · 오른쪽 바뀐 파일과 이전 · 이후 비교 (T-10)
 *               [세션]   이 프로젝트의 세션과 마지막 작업 (자르지 않고)
 *
 * 보기끼리 잇는다: 커밋의 [T-12] · 파일 이력의 할 일을 누르면 [할 일] 보기로 넘어가 그 문서가 열린다.
 */
export function ProjectView({ project, offline }: { project: ProjectRow; offline: boolean }) {
  // 뒤로 가기로 이 프로젝트에 돌아왔으면 그때 보던 화면에서 시작한다 (T-28)
  const [initial] = useState(() => savedNav(project.key))
  const [view, setView] = useState<ProjectViewName>(initial?.view ?? lastView)
  const [chosenFile, setChosenFile] = useState<string | null>(initial?.chosenFile ?? null)
  const [selectedPath, setSelectedPath] = useState<string | null>(initial?.selectedPath ?? null)
  const [selectedSession, setSelectedSession] = useState<string | null>(initial?.selectedSession ?? null)
  const [selectedCommit, setSelectedCommit] = useState<string | null>(initial?.selectedCommit ?? null)
  // 뒤로 가기로 돌아온 화면의 스크롤 위치 — 그 화면이 그려진 뒤 맞춘다. 사람이 다시 옮기면 비운다
  const [restore, setRestore] = useState<{ view: ProjectViewName; scroll: number } | null>(
    initial ? { view: initial.view, scroll: initial.scroll } : null,
  )
  const [picking, setPicking] = useState(false)

  // 지금 화면 — go() 가 고정된 함수라서(아래 openFile 등이 바뀌지 않게) 늘 최신 값을 여기서 읽는다.
  // 그린 직후에 적어 둔다 (go 는 클릭 때만 불리니 그때는 늘 최신이다)
  const here = useRef<Omit<Nav, 'scroll'>>({ key: project.key, view, chosenFile, selectedPath, selectedSession, selectedCommit })
  useLayoutEffect(() => {
    here.current = { key: project.key, view, chosenFile, selectedPath, selectedSession, selectedCommit }
  })

  const apply = useCallback((n: Nav) => {
    setView(n.view)
    rememberView(n.view)
    setChosenFile(n.chosenFile)
    setSelectedPath(n.selectedPath)
    setSelectedSession(n.selectedSession)
    setSelectedCommit(n.selectedCommit)
  }, [])

  /**
   * 사람이 누른 이동 (T-28): 보기 전환 · 항목 고르기 · 다른 보기로 넘어가기. 브라우저 방문 기록에 한 칸 남겨
   * 뒤로 가기를 누르면 바로 전 화면(보기 · 고른 것 · 스크롤 위치)으로 돌아온다. 같은 화면이면 남기지 않는다.
   * 주소(해시)는 프로젝트까지만 쓰고, 화면 상태는 방문 기록의 state 에 둔다.
   * 처음 열 때 기본 항목을 스스로 고르는 것은 이동이 아니라서 남기지 않는다.
   */
  const go = useCallback(
    (patch: Partial<Omit<Nav, 'key' | 'scroll'>>) => {
      const from: Nav = { ...here.current, scroll: rightScroll() }
      const to: Nav = { ...from, ...patch, scroll: 0 }
      if (sameScreen(from, to)) return
      const state = (window.history.state ?? {}) as Record<string, unknown>
      window.history.replaceState({ ...state, [NAV]: from }, '') // 떠나는 화면에 스크롤 위치를 적어 둔다
      window.history.pushState({ [NAV]: to }, '')
      setRestore(null)
      apply(to)
    },
    [apply],
  )

  // 뒤로 · 앞으로 가기: 방문 기록에 적어 둔 화면으로. 다른 프로젝트의 기록이면 주소(해시)가 바뀌어 화면이 새로 열린다
  useEffect(() => {
    const onPop = () => {
      const n = savedNav(project.key)
      if (!n) return
      apply(n)
      setRestore({ view: n.view, scroll: n.scroll })
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [project.key, apply])

  // 돌아온 화면의 스크롤 위치 맞추기. 대화 보기는 대화가 그려진 뒤 ChatView 가 스스로 맞춘다.
  // 다른 보기는 내용이 늦게 올 수 있어 몇 번 다시 맞춘다
  useEffect(() => {
    if (!restore || restore.view !== view || view === 'sessions') return
    const set = () => {
      const el = document.querySelector('[data-scroll]') as HTMLElement | null
      if (el) el.scrollTop = restore.scroll
    }
    const timers = [0, 200, 600].map((ms) => window.setTimeout(set, ms))
    return () => timers.forEach((t) => window.clearTimeout(t))
  }, [restore, view])
  const { files, sessions, tasks, git, reload } = useProjectData(project.key)

  // [세션] 보기: 고르지 않았거나 고른 세션이 빠졌으면 (/clear 로 번호가 바뀐 경우 포함) 첫째를 연다
  // 목록이 비었으면 고른 것도 비운다 — 다 뺀 세션의 대화가 오른쪽에 남아 있었다 (8단계 Fable 검토)
  const sessionList = sessions.data
  if (sessionList && !sessionList.some((s) => s.id === selectedSession)) {
    const first = sessionList[0]?.id ?? null
    if (first !== selectedSession) setSelectedSession(first)
  }

  // [git] 보기: 고르지 않았거나 고른 커밋이 목록에서 빠졌으면 가장 최근 커밋을 연다
  const commitList = git.data?.commits
  if (commitList && !commitList.some((c) => c.hash === selectedCommit)) {
    const first = commitList[0]?.hash ?? null
    if (first !== selectedCommit) setSelectedCommit(first)
  }

  // 아직 고르지 않았거나 고른 할 일이 사라졌으면 기본(진행 중 첫째 …)을 연다
  const list = tasks.data
  if (list && (chosenFile == null || !list.some((t) => t.file === chosenFile))) {
    const next = defaultTask(list)
    if (next !== chosenFile) setChosenFile(next)
  }
  const chosen = list?.find((t) => t.file === chosenFile) ?? null
  const doc = useTaskDoc(project.key, chosen)
  const shownDoc = doc.data?.file === chosenFile ? doc.data : null

  const highlight = useMemo(() => new Set(shownDoc?.files.map((f) => f.path) ?? []), [shownDoc])
  const known = useMemo(() => new Set(list?.map((t) => t.number) ?? []), [list])
  // 문서 속 [[위키링크]] 를 찾을 곳 (T-4)
  const mdFiles = useMemo(
    () => files.data?.files.map((f) => f.path).filter((p) => /\.md$/i.test(p)) ?? [],
    [files.data],
  )

  // 다른 보기에서 할 일 번호를 누르면 [할 일] 보기로 넘어가 연다 (같은 번호가 둘이면 목록 순 첫째)
  const openTask = useCallback(
    (ref: number | string) => {
      const n = typeof ref === 'number' ? ref : Number(ref.replace(/^T-/, ''))
      const hit = list?.find((t) => t.number === n)
      if (!hit) return
      go({ view: 'tasks', chosenFile: hit.file })
    },
    [list, go],
  )
  // 정리 문서의 "이 작업에서 바뀐 파일"을 누르면 [파일] 보기로 넘어가 그 파일을 연다 (T-5).
  // 트리는 고른 파일을 품은 폴더를 펼치고 그 자리까지 스크롤한다 (FileTree)
  // 고정해 둔다: 대화의 마크다운(MarkdownView)이 이 함수를 받는데, 매번 새 함수면 매번 다시 그린다
  const openFile = useCallback((path: string) => go({ view: 'files', selectedPath: path }), [go])
  const selectTask = useCallback((file: string) => go({ chosenFile: file }), [go])
  const selectPath = useCallback((path: string) => go({ selectedPath: path }), [go])
  const selectCommit = useCallback((hash: string) => go({ selectedCommit: hash }), [go])
  const selectSession = useCallback((id: string) => go({ selectedSession: id }), [go])

  // [갱신]: 원격의 새 커밋을 받아 오고 목록을 다시 읽는다. 실패 이유는 목록의 fetch.error 로 보인다
  const refreshRemote = useCallback(async () => {
    await fetchRemote(project.key).catch(() => {})
    reload()
  }, [project.key, reload])

  // [git] 옆 숫자: [git] 보기를 마지막으로 본 뒤 생긴 커밋 수 (T-10). 보고 있는 동안은 세지 않는다
  const commits = git.data?.commits
  const seenCommits = view === 'git' ? null : gitSeen(project.key)
  const newCommits = commits && seenCommits ? commits.filter((c) => !seenCommits.has(c.hash)).length : 0
  // 본 커밋 기억: 보고 있으면 지금 목록 전부를, 처음 여는 프로젝트면 지금부터 센다
  useEffect(() => {
    if (!commits) return
    if (view === 'git' || gitSeen(project.key) == null) {
      rememberGitSeen(
        project.key,
        commits.map((c) => c.hash),
      )
    }
  }, [view, commits, project.key])

  const doing = list?.filter((t) => t.status === 'doing').length
  // 순서와 이름은 사용자 결정 (T-15): 세션 · 작업 · 파일 · 깃
  const tabs: { name: ProjectViewName; label: string; count?: number; fresh?: boolean }[] = [
    { name: 'sessions', label: '세션', count: sessions.data?.length },
    { name: 'tasks', label: '작업', count: doing },
    { name: 'files', label: '파일', count: files.data?.files.length },
    { name: 'git', label: '깃', count: newCommits, fresh: true },
  ]
  const countTitle: Record<ProjectViewName, string> = {
    tasks: '진행 중',
    files: '파일 수',
    git: '마지막으로 본 뒤 새 커밋',
    sessions: '고른 세션',
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <nav className="flex shrink-0 items-center gap-1 border-b border-edge px-4 py-2 min-[900px]:px-[30px]">
        {tabs.map((t) => (
          <button
            key={t.name}
            type="button"
            onClick={() => go({ view: t.name })}
            className={`flex cursor-pointer items-center gap-1.5 rounded-md px-3.5 py-1.5 text-[15px] ${
              view === t.name ? 'bg-btn font-semibold text-gray-100' : 'text-gray-400 hover:bg-hover hover:text-gray-200'
            }`}
          >
            {t.label}
            {t.count != null && t.count > 0 && (
              <span
                title={countTitle[t.name]}
                className={`text-xs tabular-nums ${t.fresh ? 'font-semibold text-[#f06368]' : 'font-normal text-gray-500'}`}
              >
                {t.fresh ? `+${t.count}` : t.count}
              </span>
            )}
          </button>
        ))}
      </nav>

      {view === 'tasks' && (
        <Split
          left={
            <SlotView slot={tasks} offline={offline}>
              {(t) => <TaskList tasks={t} selected={chosenFile} onSelect={selectTask} />}
            </SlotView>
          }
          right={
            // 고른 것이 없으면 비워 둔다 — 사용법 안내 문장은 두지 않는다 (사용자 결정)
            chosenFile == null ? null : (
              <>
                {doc.error && !offline && <p className={`mb-3 ${WARNING}`}>{doc.error}</p>}
                {shownDoc ? (
                  // 작업을 바꾸면 열어 둔 비교도 닫는다
                  <TaskDoc key={shownDoc.file} projectKey={project.key} doc={shownDoc} onOpenFile={openFile} />
                ) : (
                  !doc.error && <Loading />
                )}
              </>
            )
          }
        />
      )}

      {view === 'files' && (
        <Split
          note={files.data?.truncated ? '파일이 많아 앞부분만 보입니다' : undefined}
          wide={selectedPath != null && !/\.md$/i.test(selectedPath)}
          left={
            <SlotView slot={files} offline={offline}>
              {(f) => (
                <FileTree
                  projectKey={project.key}
                  files={f.files}
                  highlight={highlight}
                  selected={selectedPath}
                  onSelect={selectPath}
                />
              )}
            </SlotView>
          }
          right={
            selectedPath == null ? null : (
              <FilePane
                projectKey={project.key}
                path={selectedPath}
                mdFiles={mdFiles}
                tasks={list}
                onSelect={selectPath}
                onOpenTask={openTask}
              />
            )
          }
        />
      )}

      {view === 'git' && (
        <Split
          wide
          left={
            <SlotView slot={git} offline={offline}>
              {(g) => (
                <GitList
                  projectKey={project.key}
                  git={g}
                  selected={selectedCommit}
                  onSelect={selectCommit}
                  onRefresh={refreshRemote}
                />
              )}
            </SlotView>
          }
          right={
            selectedCommit == null ? null : (
              <CommitView
                key={selectedCommit}
                projectKey={project.key}
                hash={selectedCommit}
                known={known}
                onSelectTask={openTask}
              />
            )
          }
        />
      )}

      {view === 'sessions' && (
        <Split
          left={
            <>
              <div className="mb-3 flex items-center justify-between px-1.5">
                <span className="text-xs text-gray-400">이 프로젝트의 세션</span>
                <button type="button" className={BUTTON} onClick={() => setPicking(true)}>
                  세션 고르기
                </button>
              </div>
              <SlotView slot={sessions} offline={offline}>
                {(s) => (
                  <SessionList sessions={s} selected={selectedSession} onSelect={selectSession} onChanged={reload} />
                )}
              </SlotView>
            </>
          }
          right={
            selectedSession == null ? null : (
              // 세션을 바꾸면 처음(최근 10개)부터 다시
              <ChatView
                key={selectedSession}
                sessionId={selectedSession}
                projectKey={project.key}
                working={sessionList?.find((s) => s.id === selectedSession)?.activity === 'working'}
                mdFiles={mdFiles}
                onOpenFile={openFile}
                onOpenTask={openTask}
                initialTop={restore?.view === 'sessions' ? restore.scroll : null}
              />
            )
          }
        />
      )}

      {picking && <SessionPicker projectKey={project.key} onClose={() => setPicking(false)} onSaved={reload} />}
    </div>
  )
}

/** 뒤로 가기용 화면 하나 (T-28). 브라우저 방문 기록의 state 에 넣는다 — 주소(해시)는 프로젝트까지만 쓴다. */
interface Nav {
  key: string
  view: ProjectViewName
  chosenFile: string | null
  selectedPath: string | null
  selectedSession: string | null
  selectedCommit: string | null
  /** 오른쪽 칸의 스크롤 위치 */
  scroll: number
}

const NAV = 'devdashNav'

/** 지금 방문 기록 칸에 적힌 이 프로젝트의 화면 (없거나 다른 프로젝트 것이면 null) */
function savedNav(key: string): Nav | null {
  const nav = (window.history.state as Record<string, unknown> | null)?.[NAV] as Nav | undefined
  return nav && nav.key === key ? nav : null
}

/** 오른쪽 칸(보기마다 하나)의 스크롤 위치 */
function rightScroll(): number {
  return (document.querySelector('[data-scroll]') as HTMLElement | null)?.scrollTop ?? 0
}

function sameScreen(a: Nav, b: Nav): boolean {
  return (
    a.view === b.view &&
    a.chosenFile === b.chosenFile &&
    a.selectedPath === b.selectedPath &&
    a.selectedSession === b.selectedSession &&
    a.selectedCommit === b.selectedCommit
  )
}

/**
 * 두 칸 보기: 왼쪽 목록(좁게) · 오른쪽 내용. 좁은 화면에서는 위아래로 쌓는다.
 * 오른쪽 내용은 가운데에 둬서 좌우 여백을 같게 한다 (왼쪽에 붙이니 오른쪽만 비어 보였다, T-7).
 * 글(정리 문서 · md)은 읽기 좋은 줄 길이로 좁게, 코드(wide)는 화면을 넓게 쓴다.
 */
function Split({
  left,
  right,
  note,
  wide = false,
}: {
  left: ReactNode
  right: ReactNode
  note?: string
  wide?: boolean
}) {
  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 overflow-auto min-[900px]:grid-cols-[minmax(260px,360px)_minmax(0,1fr)] min-[900px]:overflow-hidden">
      <div className="min-w-0 border-divider px-3 py-4 max-[899px]:border-b min-[900px]:overflow-auto min-[900px]:border-r min-[900px]:px-5">
        {note && <p className="mb-2 px-1.5 text-xs text-gray-500">{note}</p>}
        {left}
      </div>
      {/* data-scroll: 대화 보기가 스크롤 위치를 맞출 때 이 상자를 찾는다 */}
      <div data-scroll className="min-w-0 px-5 py-5 min-[900px]:overflow-auto min-[900px]:px-10">
        <div className={`mx-auto w-full ${wide ? 'max-w-[1400px]' : 'max-w-[860px]'}`}>{right}</div>
      </div>
    </div>
  )
}

/** [파일] 보기 오른쪽: 고른 파일을 바꾼 할 일들. 누르면 [할 일] 보기에서 그 문서가 열린다. */
function FileHistory({
  projectKey,
  path,
  tasks,
  onOpenTask,
}: {
  projectKey: string
  path: string
  tasks: TaskRow[] | null
  onOpenTask: (ref: string) => void
}) {
  const now = useNow()
  const [rows, setRows] = useState<{ path: string; data: FileHistoryRow[] } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetchFileHistory(projectKey, path)
      .then((data) => {
        if (!alive) return
        setRows({ path, data })
        setError(null)
      })
      .catch((e) => alive && setError(reasonOf(e)))
    return () => {
      alive = false
    }
  }, [projectKey, path])

  const titleOf = (ref: string | null) => {
    if (!ref) return null
    const n = Number(ref.replace(/^T-/, ''))
    return tasks?.find((t) => t.number === n)?.title ?? null
  }
  const shown = rows?.path === path ? rows.data : null

  return (
    <section>
      <h4 className="mb-2 text-[13px] font-semibold text-gray-300">이 파일을 바꾼 작업</h4>
      {error && <p className={WARNING}>{error}</p>}
      {!shown && !error && <Loading />}
      {shown?.length === 0 && (
        <p className="text-[15px] text-gray-500">기록 없음</p>
      )}
      <div className="space-y-1">
        {shown?.map((h) => {
          const title = titleOf(h.task)
          return (
            <button
              key={h.task ?? 'none'}
              type="button"
              disabled={!title}
              onClick={() => h.task && onOpenTask(h.task)}
              className="flex w-full cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-left text-[15px] hover:bg-hover disabled:cursor-default disabled:hover:bg-transparent"
            >
              <span className="w-11 shrink-0 tabular-nums text-gray-500">{h.task ?? '—'}</span>
              <span className={`min-w-0 flex-1 truncate ${title ? 'text-gray-100' : 'text-gray-500'}`}>
                {title ?? (h.task ? '지금은 없는 작업' : '작업 없이 고침')}
              </span>
              {h.created && <span className="shrink-0 text-xs text-on">만듦</span>}
              <span className="shrink-0 text-xs tabular-nums text-gray-500">
                {h.changes}번 · {agoIso(h.last, now)}
              </span>
            </button>
          )
        })}
      </div>
    </section>
  )
}

/**
 * [파일] 보기 오른쪽 전체: 파일 이름 → 내용 → 이 파일을 바꾼 할 일.
 * 내용은 .md 면 읽기 좋게 그린 문서(T-4), 그 밖의 글 파일은 색과 줄 번호를 붙인 코드(T-6).
 * 문서 속 링크로 다른 .md 를 열면 onSelect 로 고른 파일이 바뀌어 트리도 따라간다.
 */
function FilePane({
  projectKey,
  path,
  mdFiles,
  tasks,
  onSelect,
  onOpenTask,
}: {
  projectKey: string
  path: string
  mdFiles: string[]
  tasks: TaskRow[] | null
  onSelect: (path: string) => void
  onOpenTask: (ref: string) => void
}) {
  return (
    <article>
      <h3 className="text-lg font-semibold break-all text-gray-100">{path.split('/').pop()}</h3>
      <p className="mt-1 mb-6 text-xs break-all text-gray-500">{path}</p>
      <div className="mb-8 border-b border-edge pb-6">
        <FileContentView projectKey={projectKey} path={path} mdFiles={mdFiles} onOpen={onSelect} />
      </div>
      <FileHistory projectKey={projectKey} path={path} tasks={tasks} onOpenTask={onOpenTask} />
    </article>
  )
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|ico)$/i

/**
 * 그림 파일. 받아서 보인다 — <img> 에 주소를 바로 주면 거절 이유(너무 큼 등)를 알 수 없어서.
 * svg 도 <img> 로만 그려 그 안의 스크립트가 돌지 않는다 (화면에 끼워 넣지 않는다).
 */
function ImageView({ projectKey, path }: { projectKey: string; path: string }) {
  const [state, setState] = useState<{ path: string; url?: string; error?: string } | null>(null)

  useEffect(() => {
    let alive = true
    let url: string | undefined
    fetch(imageUrl(projectKey, path))
      .then(async (r) => {
        if (!r.ok) throw new Error(await r.text())
        url = URL.createObjectURL(await r.blob())
        if (alive) setState({ path, url })
      })
      .catch((e) => alive && setState({ path, error: reasonOf(e) }))
    return () => {
      alive = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [projectKey, path])

  if (state?.path !== path) return <Loading />
  if (state.error) return <p className={WARNING}>{state.error}</p>
  return <img src={state.url} alt={path} className="max-w-full rounded border border-edge" />
}

/** 파일 내용을 읽어 그림은 ImageView, .md 는 MarkdownView, 그 밖은 CodeView 로. 글이 아닌 파일은 이유를 보인다. */
function FileContentView({
  projectKey,
  path,
  mdFiles,
  onOpen,
}: {
  projectKey: string
  path: string
  mdFiles: string[]
  onOpen: (path: string) => void
}) {
  const [content, setContent] = useState<FileContent | null>(null)
  const [error, setError] = useState<{ path: string; message: string } | null>(null)
  const isImage = IMAGE_EXT.test(path)
  // 문서 속 상대 경로 그림의 주소. 고정해서 넘긴다 — 바뀌면 마크다운을 통째로 다시 그린다
  const docImage = useCallback((p: string) => imageUrl(projectKey, p), [projectKey])

  useEffect(() => {
    if (isImage) return
    let alive = true
    fetchFile(projectKey, path)
      .then((c) => alive && setContent(c))
      .catch((e) => alive && setError({ path, message: reasonOf(e) }))
    return () => {
      alive = false
    }
  }, [projectKey, path, isImage])

  if (isImage) return <ImageView projectKey={projectKey} path={path} />
  if (error?.path === path) return <p className={WARNING}>{error.message}</p>
  if (content?.path !== path) return <Loading />
  if (/\.md$/i.test(path)) {
    return <MarkdownView text={content.text} path={path} mdFiles={mdFiles} onOpen={onOpen} imageUrl={docImage} />
  }
  return <CodeView code={content.text} lang={langOfPath(path)} />
}

const Loading = () => <p className="text-[15px] text-gray-500">불러오는 중…</p>

/**
 * 칸의 세 상태: 불러오는 중 · 거절 이유 · 내용 (이유가 있어도 마지막 내용은 함께 보인다).
 * 서버에 닿지 않을 때는 위에 한 줄만 띄우고 칸마다 같은 말을 되풀이하지 않는다.
 */
function SlotView<T>({
  slot,
  offline,
  children,
}: {
  slot: Slot<T>
  offline: boolean
  children: (data: T) => ReactNode
}) {
  return (
    <>
      {slot.error && !offline && <p className={`mb-3 ${WARNING}`}>{slot.error}</p>}
      {slot.data != null ? children(slot.data) : !slot.error && <Loading />}
    </>
  )
}
