import { Column, ColumnTitle } from '../components/Column'
import { ServerCard } from '../components/ServerCard'
import type { ServerSummary } from '../types'

/**
 * 왼쪽 칸: 원격 서버. 예전 그리드의 카드를 그대로 세로로 쌓는다.
 * 카드를 누르면 그 서버의 상세 화면으로 간다.
 */
export function ServerPanel({
  servers,
  loadError,
  live,
  now,
}: {
  servers: ServerSummary[] | null
  loadError: string | null
  live: boolean
  now: number
}) {
  return (
    <Column
      title={<ColumnTitle>서버</ColumnTitle>}
      side={
        // 정상일 때는 조용하게, 끊겼을 때는 눈에 띄게
        <span className="flex items-center gap-2 text-xs">
          <span className={`h-2 w-2 rounded-full ${live ? 'bg-on' : 'bg-amber-400'}`} />
          <span className={live ? 'text-gray-400' : 'text-amber-300'}>
            {live ? '실시간' : '연결 끊김 · 다시 붙는 중'}
          </span>
        </span>
      }
    >
      {loadError && (
        <p className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-300">
          서버 목록을 불러오지 못했습니다: {loadError}
        </p>
      )}
      {servers == null && !loadError && <p className="px-1.5 text-sm text-gray-500">불러오는 중…</p>}
      {servers?.length === 0 && (
        <p className="px-1.5 text-sm text-gray-500">config/servers.yaml 에 서버가 없습니다.</p>
      )}
      <div className="space-y-4">
        {servers?.map((server) => <ServerCard key={server.id} server={server} now={now} />)}
      </div>
    </Column>
  )
}
