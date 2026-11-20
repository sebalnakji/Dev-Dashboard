import { useCallback, useEffect, useRef, useState } from 'react'

import { fetchGit, fetchProjectFiles, fetchProjectSessions, fetchTask, fetchTasks } from '../api/client'
import { reasonOf } from '../components/styles'
import type { FilesResponse, GitHistory, ProjectSession, TaskDetail, TaskRow } from '../types'

/** 칸 내용은 세션이 일하는 대로 바뀌므로 자주 묻는다. 요청마다 수십 ms 다. */
const REFRESH_MS = 5000

/** 칸 하나의 상태: 아직 모름(null) · 내용 · 거절 이유. */
export type Slot<T> = { data: T | null; error: string | null }

const EMPTY: Slot<never> = { data: null, error: null }

/** 처음 열 때 고를 할 일: 진행 중 첫째 → 할 일 첫째 → 가장 최근에 끝낸 것. 파일 이름으로 가린다. */
export function defaultTask(tasks: TaskRow[]): string | null {
  return (
    tasks.find((t) => t.status === 'doing')?.file ??
    tasks.find((t) => t.status === 'todo')?.file ??
    tasks.filter((t) => t.status === 'done').at(-1)?.file ??
    null
  )
}

/**
 * 받은 내용을 칸에 넣는다. 내용이 지난번과 같으면 상태를 바꾸지 않는다 — 매번 새 배열이면
 * 파일 트리 같은 큰 칸이 5초마다 다시 계산된다.
 * 실패하면 마지막 내용은 그대로 두고 이유만 띄운다 (한 번 끊겼다고 칸이 비지 않게).
 */
function useSlot<T>() {
  const [slot, setSlot] = useState<Slot<T>>(EMPTY)
  const last = useRef('')
  const put = useCallback((request: Promise<T>, stillCurrent: () => boolean) => {
    request.then(
      (data) => {
        if (!stillCurrent()) return
        const text = JSON.stringify(data)
        if (text === last.current) {
          setSlot((p) => (p.error ? { ...p, error: null } : p))
          return
        }
        last.current = text
        setSlot({ data, error: null })
      },
      (e: unknown) => {
        if (stillCurrent()) setSlot((p) => ({ data: p.data, error: reasonOf(e) }))
      },
    )
  }, [])
  return [slot, put] as const
}

/** 몇 초마다 부른다. 브라우저 탭이 가려져 있으면 건너뛴다 (보는 사람이 없다). */
function usePolling(load: () => void) {
  useEffect(() => {
    const tick = () => {
      if (!document.hidden) load()
    }
    const first = window.setTimeout(load, 0)
    const timer = window.setInterval(tick, REFRESH_MS)
    document.addEventListener('visibilitychange', tick)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [load])
}

/**
 * 한 프로젝트의 네 칸(파일 · 세션 · 할 일 · git)을 몇 초마다 읽는다.
 * 프로젝트가 바뀌면 부르는 쪽이 key 로 다시 마운트하므로 이전 내용이 섞이지 않는다.
 * 칸마다 받아 오는 대로 그린다 — 다 기다리면 가장 느린 칸(세션: claude 에 켜짐/꺼짐을 묻는다,
 * 첫 호출은 몇 초)이 나머지를 붙잡았다.
 */
export function useProjectData(key: string) {
  const [files, putFiles] = useSlot<FilesResponse>()
  const [sessions, putSessions] = useSlot<ProjectSession[]>()
  const [tasks, putTasks] = useSlot<TaskRow[]>()
  const [git, putGit] = useSlot<GitHistory>()

  const load = useCallback(() => {
    const always = () => true // key 가 바뀌면 다시 마운트되므로 낡은 응답이 남지 않는다
    putFiles(fetchProjectFiles(key), always)
    putSessions(fetchProjectSessions(key), always)
    putTasks(fetchTasks(key), always)
    putGit(fetchGit(key), always)
  }, [key, putFiles, putSessions, putTasks, putGit])
  usePolling(load)

  return { files, sessions, tasks, git, reload: load }
}

/**
 * 고른 할 일의 정리 문서. 할 일을 바꿔 눌러도 네 칸은 다시 묻지 않는다.
 * 늦게 온 앞 할 일의 문서가 지금 고른 것을 덮지 않게 지금 고른 것과 맞춰 본다.
 */
export function useTaskDoc(key: string, task: TaskRow | null) {
  const [doc, putDoc] = useSlot<TaskDetail>()
  const chosen = useRef<string | null>(null)
  const file = task?.file ?? null
  const number = task?.number ?? null
  useEffect(() => {
    chosen.current = file
  }, [file])

  const load = useCallback(() => {
    if (file == null || number == null) return
    putDoc(fetchTask(key, number, file), () => chosen.current === file)
  }, [key, file, number, putDoc])
  usePolling(load)

  return file == null ? EMPTY : doc
}
