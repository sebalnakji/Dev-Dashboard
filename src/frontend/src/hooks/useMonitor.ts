import { useEffect, useState } from 'react'

import { fetchServers } from '../api/client'
import type { ContainerRow, ContainersResponse, ServerSummary } from '../types'

const SPARK_LIMIT = 60

/**
 * 서버 요약과 컨테이너 목록을 들고 있으면서 SSE 로 최신 상태를 받는다.
 *
 * 그리드와 상세 뷰가 같은 데이터를 보므로 연결은 하나만 연다.
 * 스파크라인은 초기 로드에만 서버가 보내준다. 이후에는 브라우저가 들고 있다가
 * 새 값을 이어붙인다. 5초마다 60개를 다시 받을 이유가 없기 때문이다.
 */
export function useMonitor() {
  const [servers, setServers] = useState<ServerSummary[] | null>(null)
  const [containers, setContainers] = useState<Record<string, ContainerRow[]>>({})
  const [loadError, setLoadError] = useState<string | null>(null)
  const [live, setLive] = useState(false)

  useEffect(() => {
    let cancelled = false

    fetchServers()
      .then((rows) => {
        if (!cancelled) setServers(rows)
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : String(error))
      })

    const source = new EventSource('/api/stream')

    source.addEventListener('open', () => setLive(true))
    source.addEventListener('error', () => setLive(false))

    source.addEventListener('update', (event) => {
      const next = JSON.parse((event as MessageEvent<string>).data) as ServerSummary[]
      setServers((prev) => mergeSpark(prev, next))
    })

    source.addEventListener('containers', (event) => {
      const body = JSON.parse((event as MessageEvent<string>).data) as ContainersResponse
      setContainers((prev) => ({ ...prev, [body.server_id]: body.containers }))
    })

    return () => {
      cancelled = true
      source.close()
    }
  }, [])

  return { servers, containers, loadError, live }
}

/**
 * 새 요약에 기존 스파크라인을 이어붙인다.
 *
 * 갱신은 값이 바뀔 때만 오는데, 연결 상태만 바뀌었을 수도 있다.
 * 그때 CPU 점을 또 찍으면 같은 값이 두 번 들어가므로, 수집 시각이 실제로
 * 넘어갔을 때만 이어붙인다.
 */
function mergeSpark(prev: ServerSummary[] | null, next: ServerSummary[]): ServerSummary[] {
  const before = new Map((prev ?? []).map((s) => [s.id, s]))
  return next.map((server) => {
    const old = before.get(server.id)
    const spark = old?.cpu_spark ?? []
    const advanced = server.ts != null && old?.ts !== server.ts
    return {
      ...server,
      cpu_spark: advanced ? [...spark, server.cpu_percent].slice(-SPARK_LIMIT) : spark,
    }
  })
}
