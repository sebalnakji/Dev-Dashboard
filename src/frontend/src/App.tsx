import { goToGrid, goToProject, useHashRoute } from './hooks/useHashRoute'
import { useMonitor } from './hooks/useMonitor'
import { useNow } from './hooks/useNow'
import { ProjectsPage } from './pages/ProjectsPage'
import { ServerDetail } from './pages/ServerDetail'
import { MemoryPanel } from './panels/MemoryPanel'
import { ServerPanel } from './panels/ServerPanel'
import { SessionPanel } from './panels/SessionPanel'
import { lastProject } from './projects/remember'

/**
 * 두 탭.
 *   모니터링: 원격 서버 | Claude 세션(메인) | 이 PC 메모리
 *   프로젝트: 프로젝트마다 파일 · 세션 | 정리 문서 · 할 일 | git 이력
 * 서버 카드를 누르면 그 서버의 상세 화면이 전체 화면으로 열린다 (#/서버id).
 */
export default function App() {
  const { servers, containers, loadError, live } = useMonitor()
  const route = useHashRoute()
  const now = useNow()

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

  return (
    // 프로젝트 탭은 칸마다 따로 스크롤하므로 화면 높이에 맞추고, 모니터링은 내용만큼 늘어난다
    <div className={`flex flex-col ${route.view === 'projects' ? 'h-full' : 'min-h-full'}`}>
      <nav className="flex h-10 shrink-0 items-end gap-1 border-b border-divider px-4 min-[900px]:px-[30px]">
        <Tab active={route.view === 'monitor'} onClick={goToGrid}>
          모니터링
        </Tab>
        <Tab active={route.view === 'projects'} onClick={() => goToProject(lastProject())}>
          프로젝트
        </Tab>
      </nav>

      {route.view === 'projects' ? (
        <ProjectsPage selectedKey={route.key} />
      ) : (
        // 삼등분. 칸 사이 세로줄은 화면 아래까지 닿게 하고, 좁은 창에서는 한 줄로 쌓는다
        <div className="grid flex-1 grid-cols-1 min-[900px]:grid-cols-3">
          <ServerPanel servers={servers} loadError={loadError} live={live} now={now} />
          <div className="border-divider max-[899px]:border-t min-[900px]:border-l">
            <SessionPanel />
          </div>
          <div className="border-divider max-[899px]:border-t min-[900px]:border-l">
            <MemoryPanel />
          </div>
        </div>
      )}
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
