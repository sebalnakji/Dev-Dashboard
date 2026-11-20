import { useCallback, useEffect, useRef, useState } from 'react'

import { fetchProjects } from '../api/client'
import { BUTTON, OFFLINE, WARNING } from '../components/styles'
import { goToProject, replaceProject } from '../hooks/useHashRoute'
import { AddProjectDialog } from '../projects/AddProjectDialog'
import { ProjectBar } from '../projects/ProjectBar'
import { ProjectView } from '../projects/ProjectView'
import { rememberProject } from '../projects/remember'
import type { ProjectRow } from '../types'

/** 프로젝트 목록(이름 · 진행 중 수)은 칸 내용보다 덜 자주 바뀐다. */
const LIST_REFRESH_MS = 10000

/**
 * 프로젝트 탭.
 *
 *   [프로젝트 이름들 ⋮ …]                                    [+ 프로젝트 추가]
 *   [할 일] [파일] [git] [세션]        ← 고른 보기 하나가 아래 화면을 다 쓴다
 *   ┌ 할 일 목록 ──┬ 정리 문서 ───────────────────────────────┐
 *   │ 진행 중 …    │ 목표 · 한 일 · 남은 일 · 바뀐 파일          │
 */
export function ProjectsPage({ selectedKey }: { selectedKey: string | null }) {
  const [projects, setProjects] = useState<ProjectRow[] | null>(null)
  const [offline, setOffline] = useState(false)
  const [adding, setAdding] = useState(false)
  // 이름 상자를 끄는 동안에는 주기 갱신이 순서를 되돌리지 않게 건너뛴다
  const dragging = useRef(false)

  const loadProjects = useCallback(() => {
    if (dragging.current) return
    fetchProjects()
      .then((list) => {
        setProjects(list)
        setOffline(false)
      })
      .catch(() => setOffline(true))
  }, [])

  useEffect(() => {
    const first = window.setTimeout(loadProjects, 0)
    const timer = window.setInterval(() => !document.hidden && loadProjects(), LIST_REFRESH_MS)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(timer)
    }
  }, [loadProjects])

  // 주소에 프로젝트가 없거나 사라진 프로젝트면 첫째를 연다. 화면이 스스로 정한 이동이라
  // 이력을 덮어쓴다 — 쌓으면 뒤로가기를 눌러도 이 화면으로 되돌아왔다
  const current = projects?.find((p) => p.key === selectedKey) ?? null
  useEffect(() => {
    if (projects && projects.length > 0 && !current) replaceProject(projects[0].key)
  }, [projects, current])
  useEffect(() => {
    if (current) rememberProject(current.key)
  }, [current])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 넘치면 다음 줄로 접는다 — 가로 스크롤 상자면 ⋮ 메뉴가 그 안에 잘렸다 */}
      <div className="relative z-10 flex shrink-0 flex-wrap items-center gap-1 border-b border-edge px-4 py-2 min-[900px]:px-[30px]">
        {projects && (
          <ProjectBar
            projects={projects}
            currentKey={current?.key ?? null}
            onChange={(next) => (next ? setProjects(next) : loadProjects())}
            onDragging={(d) => (dragging.current = d)}
          />
        )}
        <span className="flex-1" />
        <button type="button" className={`${BUTTON} shrink-0`} onClick={() => setAdding(true)}>
          + 프로젝트 추가
        </button>
      </div>

      {offline && <p className={`mx-4 mt-3 ${WARNING}`}>{OFFLINE}</p>}
      {projects?.length === 0 && (
        <p className="m-6 text-sm text-gray-400">
          등록된 프로젝트가 없습니다. [+ 프로젝트 추가] 로 넣거나, 그 프로젝트의 Claude 세션에서 /devdash:init 을
          입력합니다.
        </p>
      )}
      {current && <ProjectView key={current.key} project={current} offline={offline} />}

      {adding && (
        <AddProjectDialog
          onClose={() => setAdding(false)}
          onAdded={(p) => {
            setAdding(false)
            // 목록을 다시 읽기 전에 이동하면 아직 없는 프로젝트라 첫째가 열렸다 — 먼저 끼워 넣는다
            setProjects((list) => [...(list ?? []).filter((x) => x.key !== p.key), p])
            goToProject(p.key)
            loadProjects()
          }}
        />
      )}
    </div>
  )
}
