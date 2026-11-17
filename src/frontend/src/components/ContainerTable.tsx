import { bytes } from '../format'
import type { ContainerRow } from '../types'

/** 실행 중인 것을 먼저, 그 안에서는 CPU 를 많이 쓰는 순으로. */
function sortKey(container: ContainerRow): [number, number, string] {
  const running = container.state === 'running'
  return [running ? 0 : 1, -(container.cpu_percent ?? -1), container.name]
}

export function ContainerTable({
  containers,
  selectedId,
  onSelect,
}: {
  containers: ContainerRow[] | null
  selectedId: string | null
  onSelect: (container: ContainerRow) => void
}) {
  if (containers == null) {
    return <p className="text-sm text-gray-600">컨테이너를 불러오는 중…</p>
  }
  if (containers.length === 0) {
    return <p className="text-sm text-gray-600">컨테이너가 없습니다.</p>
  }

  const rows = [...containers].sort((a, b) => {
    const [ar, ac, an] = sortKey(a)
    const [br, bc, bn] = sortKey(b)
    return ar - br || ac - bc || an.localeCompare(bn)
  })

  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--color-edge)] bg-[var(--color-panel)]">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-gray-500">
          <tr className="border-b border-[var(--color-edge)]">
            <th className="px-4 py-2 font-medium">이름</th>
            <th className="px-4 py-2 font-medium">상태</th>
            <th className="px-4 py-2 text-right font-medium">CPU</th>
            <th className="px-4 py-2 text-right font-medium">메모리</th>
            <th className="px-4 py-2 font-medium">이미지</th>
            <th className="px-4 py-2 font-medium">포트</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((container) => {
            const running = container.state === 'running'
            return (
              <tr
                key={container.id}
                className={`border-b border-[var(--color-edge)] last:border-0 ${
                  selectedId === container.id ? 'bg-white/5' : ''
                }`}
              >
                <td className="px-4 py-2">
                  <span className="flex items-center gap-2">
                    <span
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                        running ? 'bg-emerald-400' : 'border border-gray-600'
                      }`}
                    />
                    <span className={running ? 'text-gray-200' : 'text-gray-500'}>
                      {container.name}
                    </span>
                  </span>
                </td>
                <td className={`px-4 py-2 ${running ? 'text-gray-400' : 'text-gray-600'}`}>
                  {container.status}
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-gray-300">
                  {container.cpu_percent == null ? '-' : `${container.cpu_percent.toFixed(1)} %`}
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-gray-300">
                  {bytes(container.mem_used_bytes)}
                </td>
                <td className="max-w-[16rem] truncate px-4 py-2 text-gray-500" title={container.image}>
                  {container.image}
                </td>
                <td className="max-w-[16rem] truncate px-4 py-2 text-gray-500" title={container.ports}>
                  {container.ports || '-'}
                </td>
                <td className="px-4 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => onSelect(container)}
                    className="rounded-lg border border-[var(--color-edge)] px-2 py-0.5 text-xs text-gray-400 hover:text-gray-200"
                  >
                    로그
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
