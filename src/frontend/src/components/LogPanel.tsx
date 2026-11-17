import { useCallback, useEffect, useRef, useState } from 'react'

import { fetchContainerLogs } from '../api/client'
import { stripAnsi } from '../format'
import type { ContainerRow } from '../types'

const TAILS = [100, 200, 500]

/**
 * 컨테이너 로그 마지막 몇 줄.
 *
 * 실시간 따라가기(follow)는 넣지 않았다. 대부분의 확인은 "방금 왜 죽었나" 라
 * 마지막 몇 줄이면 되고, 스트림을 유지하면 브라우저를 닫았을 때 서버 쪽 프로세스를
 * 정리하는 일이 따라붙는다. 필요해지면 그때 붙인다.
 */
export function LogPanel({
  serverId,
  container,
  onClose,
}: {
  serverId: string
  container: ContainerRow
  onClose: () => void
}) {
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tail, setTail] = useState(TAILS[1])
  // 열자마자 받아오기 시작하므로 처음부터 참이다
  const [loading, setLoading] = useState(true)
  const bodyRef = useRef<HTMLPreElement>(null)

  const load = useCallback(() => {
    fetchContainerLogs(serverId, container.id, tail)
      .then((body) => {
        setText(stripAnsi(body.text))
        setError(null)
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
  }, [serverId, container.id, tail])

  // 컨테이너나 줄 수가 바뀌면 다시 받아온다. 로딩 표시는 버튼을 누른 쪽에서 켠다.
  useEffect(load, [load])

  const reload = () => {
    setLoading(true)
    load()
  }

  const changeTail = (value: number) => {
    setLoading(true)
    setTail(value)
  }

  // 최신 줄이 아래에 있으므로 열 때마다 끝으로 내린다
  useEffect(() => {
    if (text != null && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [text])

  return (
    <section className="mt-4 overflow-hidden rounded-xl border border-[var(--color-edge)] bg-[var(--color-panel)]">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-edge)] px-4 py-2.5">
        <h3 className="text-sm text-gray-200">
          <span className="text-gray-500">로그</span> {container.name}
        </h3>

        <div className="flex items-center gap-2">
          <div className="flex gap-0.5 rounded-lg border border-[var(--color-edge)] p-0.5">
            {TAILS.map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => changeTail(value)}
                className={`rounded-md px-2 py-0.5 text-xs ${
                  tail === value ? 'bg-white/10 text-gray-100' : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                {value}줄
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={reload}
            disabled={loading}
            className="rounded-lg border border-[var(--color-edge)] px-2.5 py-1 text-xs text-gray-400 hover:text-gray-200 disabled:opacity-50"
          >
            {loading ? '불러오는 중…' : '새로고침'}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-[var(--color-edge)] px-2.5 py-1 text-xs text-gray-400 hover:text-gray-200"
          >
            닫기
          </button>
        </div>
      </header>

      {error ? (
        <p className="p-4 text-sm text-rose-300">{error}</p>
      ) : (
        <pre
          ref={bodyRef}
          className="max-h-96 overflow-auto px-4 py-3 font-mono text-xs leading-relaxed whitespace-pre text-gray-300"
        >
          {text ?? '불러오는 중…'}
          {text === '' && '(로그가 비어 있습니다)'}
        </pre>
      )}
    </section>
  )
}
