import { agoText, bytes, DISK_WARN_AT, LEVEL_TEXT, levelOf, usedOfTotal } from '../format'
import type { ServerSummary } from '../types'
import { Gauge } from './Gauge'
import { Sparkline } from './Sparkline'
import { StatusBadge } from './StatusBadge'

/**
 * 그리드에 늘어놓는 서버 카드.
 *
 * 이상 여부를 판단하는 데 필요한 것만 담는다. 로드·네트워크·가동 시간은
 * 이상 여부 판단에 직접 쓰이지 않아 상세 뷰로 보냈다.
 */
export function ServerCard({ server, now }: { server: ServerSummary; now: number }) {
  const cpuLevel = levelOf(server.cpu_percent)

  return (
    <a
      href={`#/${encodeURIComponent(server.id)}`}
      className="block rounded-xl border border-[var(--color-edge)] bg-[var(--color-panel)] p-4 transition-colors hover:border-gray-600"
    >
      <header className="mb-1 flex items-baseline justify-between gap-2">
        <h2 className="truncate font-medium text-gray-100">{server.label}</h2>
        <StatusBadge status={server.status} title={server.error ?? undefined} />
      </header>

      <p className="mb-3 truncate text-xs text-gray-500" title={specText(server)}>
        {specText(server) || ' '}
      </p>

      {server.error && (
        <p className="mb-3 truncate rounded bg-rose-500/10 px-2 py-1 text-xs text-rose-300" title={server.error}>
          {server.error}
        </p>
      )}

      <div className="mb-3 flex items-center gap-3">
        <span className="w-24 shrink-0 text-sm text-gray-400">CPU</span>
        <Sparkline values={server.cpu_spark ?? []} className={`flex-1 ${LEVEL_TEXT[cpuLevel]}`} />
        <span className={`w-16 shrink-0 text-right text-sm tabular-nums ${LEVEL_TEXT[cpuLevel]}`}>
          {server.cpu_percent == null ? '측정 중' : `${server.cpu_percent.toFixed(1)} %`}
        </span>
        {/* Gauge 의 실제 수치 칸과 열을 맞춘다 */}
        <span className="w-28 shrink-0" />
      </div>

      <div className="space-y-2">
        <Gauge
          label="메모리"
          value={server.mem_percent}
          amount={usedOfTotal(server.mem_used_bytes, server.mem_total_bytes)}
        />
        <Gauge
          label={server.disk ? `디스크 ${server.disk.mount}` : '디스크'}
          value={server.disk?.percent ?? null}
          amount={usedOfTotal(server.disk?.used_bytes, server.disk?.total_bytes)}
          warnAt={DISK_WARN_AT}
        />
        {server.gpus.map((gpu) => (
          <div key={gpu.index} className="space-y-2">
            <Gauge label={server.gpus.length > 1 ? `GPU ${gpu.index}` : 'GPU'} value={gpu.utilization_percent} />
            <Gauge
              label="VRAM"
              value={gpu.memory_percent}
              amount={usedOfTotal(gpu.memory_used_bytes, gpu.memory_total_bytes)}
            />
          </div>
        ))}
      </div>

      <footer className="mt-4 flex items-center justify-between border-t border-[var(--color-edge)] pt-3 text-xs">
        {server.containers ? (
          <span className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-gray-300">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              {server.containers.running} 실행
            </span>
            <span className="flex items-center gap-1.5 text-gray-500">
              <span className="h-1.5 w-1.5 rounded-full border border-gray-600" />
              {server.containers.stopped} 중지
            </span>
          </span>
        ) : (
          <span className="text-gray-500" title={server.docker_error ?? undefined}>
            {server.docker_error ? 'docker 를 읽지 못함' : '컨테이너 확인 중'}
          </span>
        )}
        <span className="text-gray-600">{agoText(server.ts, now)}</span>
      </footer>
    </a>
  )
}

/** "i5-14500 20코어 · RTX 4070 · 62 GiB" 처럼 서버 규모를 한 줄로. */
function specText(server: ServerSummary): string {
  const parts: string[] = []

  if (server.cpu_model) {
    // "Intel(R) Core(TM) i5-14500" 에서 장식을 걷어낸다
    const model = server.cpu_model
      .replace(/\((R|TM)\)/g, '')
      .replace(/\s+/g, ' ')
      .trim()
    parts.push(server.cpu_cores ? `${model} ${server.cpu_cores}코어` : model)
  }

  const gpu = server.gpus[0]?.name
  if (gpu) parts.push(gpu.replace(/^NVIDIA\s+/, ''))

  if (server.mem_total_bytes) parts.push(bytes(server.mem_total_bytes))

  return parts.join(' · ')
}
