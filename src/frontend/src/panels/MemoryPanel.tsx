import { useEffect, useState } from 'react'

import { fetchPcMemory } from '../api/client'
import { Column, ColumnTitle } from '../components/Column'
import type { PcMemory } from '../types'

/** 앱 목록(tasklist)을 읽는 데 ~0.5초 걸려, 세션 칸(3초)보다 느리게 갱신한다. */
const REFRESH_MS = 5000

const GiB = 2 ** 30
const gb = (b: number) => `${(b / GiB).toFixed(1)}GB`
const size = (b: number) => (b >= GiB ? gb(b) : `${Math.round(b / 2 ** 20)}MB`)

/**
 * 오른쪽 칸: 이 PC 의 메모리.
 * 메모리가 모자라면 Claude 관리자가 쉬는 세션을 끈다. 무엇을 끌지 고를 때 같이 본다.
 */
export function MemoryPanel() {
  const [memory, setMemory] = useState<PcMemory | null>(null)

  useEffect(() => {
    const load = () =>
      fetchPcMemory()
        .then(setMemory)
        // 서버에 닿지 않으면 세션 칸이 이미 알린다. 여기선 마지막 값을 그대로 둔다
        .catch(() => {})
    load()
    const timer = setInterval(load, REFRESH_MS)
    return () => clearInterval(timer)
  }, [])

  const usedPct = memory ? Math.round((1 - memory.avail / memory.total) * 100) : 0

  return (
    <Column title={<ColumnTitle>메모리</ColumnTitle>}>
      {memory == null ? (
        <p className="px-1.5 text-sm text-gray-500">불러오는 중…</p>
      ) : (
        <>
          <div className="flex items-baseline justify-between px-1.5">
            <b className="text-xl font-semibold text-gray-100">{gb(memory.avail)}</b>
            <span className="text-xs text-gray-400">남음 / 전체 {gb(memory.total)}</span>
          </div>
          <div className="mx-1.5 mt-2 mb-1 h-1.5 overflow-hidden rounded-full bg-btn">
            <div
              className="h-full bg-accent transition-[width] duration-500"
              style={{ width: `${usedPct}%` }}
            />
          </div>
          <div className="mb-[18px] px-1.5 text-xs text-gray-400">
            사용 {gb(memory.total - memory.avail)} ({usedPct}%)
          </div>

          <div className="px-1.5 py-1 text-xs text-gray-400">메모리를 많이 쓰는 앱</div>
          {memory.apps.map((app) => (
            <div
              key={app.name}
              className="flex justify-between rounded-md px-1.5 py-[5px] text-sm hover:bg-hover"
            >
              <span className="truncate">{app.name}</span>
              <span className="ml-3 shrink-0 tabular-nums text-gray-400">{size(app.bytes)}</span>
            </div>
          ))}
        </>
      )}
    </Column>
  )
}
