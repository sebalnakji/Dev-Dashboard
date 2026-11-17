import { useEffect, useMemo, useState } from 'react'

import { fetchSamples } from '../api/client'
import { ContainerTable } from '../components/ContainerTable'
import { LogPanel } from '../components/LogPanel'
import type { ChartRow, Series } from '../components/MetricChart'
import { MetricChart } from '../components/MetricChart'
import { StatusBadge } from '../components/StatusBadge'
import { bytes, bytesPerSecond } from '../format'
import { goToGrid } from '../hooks/useHashRoute'
import type { ContainerRow, SamplePoint, ServerSummary } from '../types'

const RANGES = [
  { label: '30분', seconds: 30 * 60 },
  { label: '1시간', seconds: 60 * 60 },
  { label: '3시간', seconds: 3 * 60 * 60 },
]

/** 파티션이나 GPU 가 여러 개일 때 선을 구분하는 색. */
const PALETTE = ['#f59e0b', '#22d3ee', '#a78bfa', '#34d399', '#f472b6']

const percentFormat = (value: number) => `${value.toFixed(0)}%`

export function ServerDetail({
  server,
  containers,
}: {
  server: ServerSummary
  containers: ContainerRow[] | null
}) {
  const [samples, setSamples] = useState<SamplePoint[] | null>(null)
  const [memTotal, setMemTotal] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [rangeSeconds, setRangeSeconds] = useState(RANGES[1].seconds)
  const [logTarget, setLogTarget] = useState<ContainerRow | null>(null)

  // 요약의 수집 시각이 넘어갈 때마다 다시 받는다. SSE 는 요약만 보내주므로
  // 차트에 필요한 값(파티션별 사용률, 네트워크 속도)은 여기서 가져와야 한다.
  useEffect(() => {
    let cancelled = false
    fetchSamples(server.id)
      .then((body) => {
        if (cancelled) return
        setSamples(body.samples)
        setMemTotal(body.mem_total_bytes)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [server.id, server.ts])

  const { rows, diskSeries, gpuSeries, vramSeries } = useMemo(
    () => buildRows(samples ?? [], rangeSeconds),
    [samples, rangeSeconds],
  )

  return (
    <div>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-edge)] px-6 py-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={goToGrid}
            className="rounded-lg border border-[var(--color-edge)] px-2.5 py-1 text-sm text-gray-400 hover:text-gray-200"
          >
            ← 대시보드
          </button>
          <h1 className="text-lg font-semibold text-gray-100">{server.label}</h1>
          <StatusBadge status={server.status} title={server.error ?? undefined} />
        </div>

        <div className="flex gap-1 rounded-lg border border-[var(--color-edge)] p-0.5">
          {RANGES.map((range) => (
            <button
              key={range.seconds}
              type="button"
              onClick={() => setRangeSeconds(range.seconds)}
              className={`rounded-md px-3 py-1 text-xs ${
                rangeSeconds === range.seconds
                  ? 'bg-white/10 text-gray-100'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {range.label}
            </button>
          ))}
        </div>
      </header>

      <main className="space-y-4 p-6">
        {error && (
          <p className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-300">
            지표를 불러오지 못했습니다: {error}
          </p>
        )}

        <div className="grid gap-4 xl:grid-cols-2">
          <MetricChart
            title="CPU"
            rows={rows}
            series={[{ key: 'cpu', name: 'CPU', color: '#38bdf8' }]}
            domain={[0, 100]}
            format={percentFormat}
          />
          <MetricChart
            title="메모리"
            rows={rows}
            series={[{ key: 'mem', name: '사용률', color: '#a78bfa' }]}
            domain={[0, 100]}
            format={percentFormat}
            tooltipFormat={(value, row) =>
              `${value.toFixed(1)}%  (${bytes(row.memBytes)}${memTotal ? ` / ${bytes(memTotal)}` : ''})`
            }
          />
          <MetricChart
            title="디스크"
            rows={rows}
            series={diskSeries}
            domain={[0, 100]}
            format={percentFormat}
          />
          <MetricChart
            title="네트워크"
            rows={rows}
            series={[
              { key: 'netRx', name: '수신', color: '#34d399' },
              { key: 'netTx', name: '송신', color: '#fb7185' },
            ]}
            format={(value) => bytesPerSecond(value)}
          />
          {gpuSeries.length > 0 && (
            <MetricChart
              title="GPU"
              rows={rows}
              series={gpuSeries}
              domain={[0, 100]}
              format={percentFormat}
            />
          )}
          {vramSeries.length > 0 && (
            <MetricChart
              title="VRAM"
              rows={rows}
              series={vramSeries}
              domain={[0, 100]}
              format={percentFormat}
            />
          )}
        </div>

        <section>
          <h2 className="mb-3 text-sm font-medium text-gray-300">
            컨테이너
            {server.containers && (
              <span className="ml-2 text-xs text-gray-500">
                실행 {server.containers.running} · 중지 {server.containers.stopped}
              </span>
            )}
          </h2>
          {server.docker_error ? (
            <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-300">
              docker 를 읽지 못했습니다: {server.docker_error}
            </p>
          ) : (
            <ContainerTable
              containers={containers}
              selectedId={logTarget?.id ?? null}
              onSelect={setLogTarget}
            />
          )}

          {logTarget && (
            <LogPanel
              serverId={server.id}
              container={logTarget}
              onClose={() => setLogTarget(null)}
            />
          )}
        </section>
      </main>
    </div>
  )
}

/**
 * 차트가 먹는 평평한 행으로 바꾼다.
 *
 * 파티션과 GPU 는 개수가 서버마다 달라, 실제로 들어온 것만 보고 선을 만든다.
 */
function buildRows(samples: SamplePoint[], rangeSeconds: number) {
  const since = Date.now() / 1000 - rangeSeconds
  const inRange = samples.filter((s) => s.ts >= since)

  const mounts: string[] = []
  const gpuIndexes: number[] = []
  for (const sample of inRange) {
    for (const disk of sample.disks) if (!mounts.includes(disk.mount)) mounts.push(disk.mount)
    for (const gpu of sample.gpus) if (!gpuIndexes.includes(gpu.index)) gpuIndexes.push(gpu.index)
  }
  mounts.sort()
  gpuIndexes.sort((a, b) => a - b)

  const rows: ChartRow[] = inRange.map((sample) => {
    const row: ChartRow = {
      ts: sample.ts,
      cpu: sample.cpu_percent,
      mem: sample.mem_percent,
      memBytes: sample.mem_used_bytes,
      netRx: sample.net_rx_bps,
      netTx: sample.net_tx_bps,
    }
    for (const disk of sample.disks) row[`disk:${disk.mount}`] = disk.percent
    for (const gpu of sample.gpus) {
      row[`gpu:${gpu.index}`] = gpu.utilization_percent
      row[`vram:${gpu.index}`] = gpu.memory_percent
    }
    return row
  })

  const diskSeries: Series[] = mounts.map((mount, i) => ({
    key: `disk:${mount}`,
    name: mount,
    color: PALETTE[i % PALETTE.length],
  }))
  const gpuSeries: Series[] = gpuIndexes.map((index, i) => ({
    key: `gpu:${index}`,
    name: gpuIndexes.length > 1 ? `GPU ${index}` : '사용률',
    color: PALETTE[i % PALETTE.length],
  }))
  const vramSeries: Series[] = gpuIndexes.map((index, i) => ({
    key: `vram:${index}`,
    name: gpuIndexes.length > 1 ? `GPU ${index}` : '사용률',
    color: PALETTE[i % PALETTE.length],
  }))

  return { rows, diskSeries, gpuSeries, vramSeries }
}
