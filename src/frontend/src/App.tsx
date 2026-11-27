import { useState } from 'react'

import { goToGrid, goToProject, goToSplit, useHashRoute } from './hooks/useHashRoute'
import { useMonitor } from './hooks/useMonitor'
import { useNow } from './hooks/useNow'
import { TabActive } from './hooks/useTabActive'
import { ProjectsPage } from './pages/ProjectsPage'
import { ServerDetail } from './pages/ServerDetail'
import { SplitPage } from './pages/SplitPage'
import { MemoryPanel } from './panels/MemoryPanel'
import { ServerPanel } from './panels/ServerPanel'
import { SessionPanel } from './panels/SessionPanel'
import { lastProject, lastScreen } from './projects/remember'

/**
 * 세 탭.
 *   모니터링: 원격 서버 | Claude 세션(메인) | 이 PC 메모리
 *   프로젝트: 프로젝트마다 파일 · 세션 | 정리 문서 · 할 일 | git 이력
 *   화면분할: 세션 여러 개의 상태와 대화를 나란히 (#/split/<화면 id>)
 * 서버 카드를 누르면 그 서버의 상세 화면이 전체 화면으로 열린다 (#/서버id).
 */
export default function App() {
  const { servers, containers, loadError, live } = useMonitor()
  const route = useHashRoute()
  const now = useNow()
  // 탭을 바꿀 때 지우지 않고 숨기기만 한다 — 돌아올 때마다 모든 칸이 '불러오는 중' 부터 다시
  // 받았다. 프로젝트 탭은 처음 열 때 만들고, 그 뒤로는 마지막에 본 프로젝트를 들고 숨어 있는다
  const [projectKey, setProjectKey] = useState<string | null | undefined>(
    route.view === 'projects' ? route.key : undefined,
  )
  if (route.view === 'projects' && route.key !== projectKey) setProjectKey(route.key)
  // 화면분할 탭도 같다 — 처음 열 때 만들고, 숨어 있는 동안에는 대화를 받지 않는다 (TabActive)
  const [screenId, setScreenId] = useState<string | null | undefined>(
    route.view === 'split' ? route.id : undefined,
  )
  if (route.view === 'split' && route.id !== screenId) setScreenId(route.id)

  if (route.view === 'server') {
    const selected = servers?.find((s) => s.id === route.id)
    if (selected) {
      return <ServerDetail server={selected} containers={containers[selected.id] ?? null} />
    }
    return (
      <p className="m-6 rounded-lg border border-edge p-4 text-sm text-gray-400">
        {servers == null ? '불러오는 중…' : `'${route.id}' 서버를 찾을 수 없습니다. `}
        <a href="#" className="text-accent">
          대시보드로
        </a>
      </p>
    )
  }

  const onProjects = route.view === 'projects'
  const onSplit = route.view === 'split'
  const onMonitor = !onProjects && !onSplit
  return (
    // 프로젝트 · 화면분할 탭은 칸마다 따로 스크롤하므로 화면 높이에 맞추고, 모니터링은 내용만큼 늘어난다
    <div className={`flex flex-col ${onMonitor ? 'min-h-full' : 'h-full'}`}>
      <nav className="flex h-10 shrink-0 items-end gap-1 border-b border-divider px-4 min-[900px]:px-[30px]">
        <Tab active={route.view === 'monitor'} onClick={goToGrid}>
          모니터링
        </Tab>
        <Tab active={route.view === 'projects'} onClick={() => goToProject(lastProject())}>
          프로젝트
        </Tab>
        <Tab active={onSplit} onClick={() => goToSplit(lastScreen())}>
          화면분할
        </Tab>
      </nav>

      {projectKey !== undefined && (
        <TabActive.Provider value={onProjects}>
          <div className={onProjects ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}>
            <ProjectsPage selectedKey={projectKey} />
          </div>
        </TabActive.Provider>
      )}
      {/* 프로젝트 탭 뒤에 둔다 — 프로젝트 화면은 문서의 첫 [data-scroll] 을 제 오른쪽 칸으로 안다 */}
      {screenId !== undefined && (
        <TabActive.Provider value={onSplit}>
          <div className={onSplit ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}>
            <SplitPage selectedId={screenId} />
          </div>
        </TabActive.Provider>
      )}
      <TabActive.Provider value={onMonitor}>
        {/* 삼등분. 칸 사이 세로줄은 화면 아래까지 닿게 하고, 좁은 창에서는 한 줄로 쌓는다 */}
        <div className={onMonitor ? 'grid flex-1 grid-cols-1 min-[900px]:grid-cols-3' : 'hidden'}>
          <ServerPanel servers={servers} loadError={loadError} live={live} now={now} />
          <div className="border-divider max-[899px]:border-t min-[900px]:border-l">
            <SessionPanel />
          </div>
          <div className="border-divider max-[899px]:border-t min-[900px]:border-l">
            <MemoryPanel />
          </div>
        </div>
      </TabActive.Provider>
    </div>
  )
}

function Tab({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`-mb-px cursor-pointer border-b-2 px-3 pb-2 text-sm ${
        active ? 'border-accent text-gray-100' : 'border-transparent text-gray-400 hover:text-gray-200'
      }`}
    >
      {children}
    </button>
  )
}
