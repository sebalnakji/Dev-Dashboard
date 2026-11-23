import { useEffect, useRef, useState, type ReactNode } from 'react'

import { fetchSnapshotSize, renameProject, saveProjectOrder, unregisterProject } from '../api/client'
import { Modal } from '../components/Modal'
import { BUTTON, INPUT, NOTICE, reasonOf } from '../components/styles'
import { goToProject } from '../hooks/useHashRoute'
import type { ProjectRow } from '../types'

/**
 * 프로젝트 탭 위쪽의 프로젝트 이름 줄.
 *   - 이름 상자를 꾹 눌러 끌면 순서가 바뀐다 (놓으면 저장. 어느 브라우저로 열어도 같은 순서)
 *   - 상자 오른쪽 ⋮ 로 이름 바꾸기 · 등록 해제
 */
export function ProjectBar({
  projects,
  currentKey,
  onChange,
  onDragging,
}: {
  projects: ProjectRow[]
  currentKey: string | null
  /** 목록이 바뀌었다 (순서 · 이름 · 해제). 넘긴 목록을 바로 쓰고 서버에서 다시 읽는다 */
  onChange: (next: ProjectRow[] | null) => void
  /** 끄는 동안 주기 갱신이 순서를 되돌리지 않게 알린다 */
  onDragging: (dragging: boolean) => void
}) {
  const [order, setOrder] = useState<ProjectRow[] | null>(null)
  const [dragKey, setDragKey] = useState<string | null>(null)
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<ProjectRow | null>(null)
  const [removing, setRemoving] = useState<ProjectRow | null>(null)
  const shown = order ?? projects

  function moveOver(overKey: string) {
    const from = dragKey
    if (!from || from === overKey) return
    const list = [...shown]
    const a = list.findIndex((p) => p.key === from)
    const b = list.findIndex((p) => p.key === overKey)
    if (a < 0 || b < 0) return
    const [moved] = list.splice(a, 1)
    list.splice(b, 0, moved)
    setOrder(list)
  }

  async function finishDrag() {
    const list = order
    setDragKey(null)
    onDragging(false)
    setOrder(null)
    if (!list || list.map((p) => p.key).join() === projects.map((p) => p.key).join()) return
    onChange(list)
    await saveProjectOrder(list.map((p) => p.key)).catch(() => {})
    onChange(null)
  }

  return (
    <>
      {shown.map((p) => (
        <div
          key={p.key}
          draggable
          title={p.root}
          onDragStart={(e) => {
            setDragKey(p.key)
            onDragging(true)
            setMenuFor(null)
            e.dataTransfer.effectAllowed = 'move'
          }}
          onDragOver={(e) => {
            e.preventDefault()
            moveOver(p.key)
          }}
          onDrop={(e) => e.preventDefault()}
          onDragEnd={finishDrag}
          className={`relative flex max-w-[280px] shrink-0 items-center rounded-md text-sm ${
            dragKey === p.key ? 'opacity-50' : ''
          } ${p.key === currentKey ? 'bg-btn text-gray-100' : 'text-gray-400 hover:bg-hover hover:text-gray-200'}`}
        >
          <button
            type="button"
            onClick={() => goToProject(p.key)}
            className="flex min-w-0 cursor-pointer items-center gap-1.5 py-1 pr-1 pl-2.5"
          >
            <span className="truncate">{p.name}</span>
            {p.doing > 0 && (
              <span title="진행 중" className="shrink-0 rounded-full bg-[#2c3440] px-1.5 text-[11px] text-accent">
                {p.doing}
              </span>
            )}
          </button>
          <button
            type="button"
            title="이름 바꾸기 · 등록 해제"
            onClick={() => setMenuFor(menuFor === p.key ? null : p.key)}
            className="cursor-pointer rounded px-1.5 py-1 text-gray-500 hover:text-gray-100"
          >
            ⋮
          </button>
          {menuFor === p.key && (
            <Menu
              onClose={() => setMenuFor(null)}
              footer={<SnapshotSize projectKey={p.key} />}
              items={[
                { label: '이름 바꾸기', run: () => setRenaming(p) },
                { label: '등록 해제', danger: true, run: () => setRemoving(p) },
              ]}
            />
          )}
        </div>
      ))}

      {renaming && (
        <RenameDialog
          project={renaming}
          onClose={() => setRenaming(null)}
          onDone={(row) => {
            setRenaming(null)
            onChange(projects.map((p) => (p.key === row.key ? row : p)))
            onChange(null)
          }}
        />
      )}
      {removing && (
        <RemoveDialog
          project={removing}
          onClose={() => setRemoving(null)}
          onDone={() => {
            setRemoving(null)
            // 지금 보던 프로젝트면 남은 첫째로 간다 (목록 화면이 알아서 고른다)
            onChange(projects.filter((p) => p.key !== removing.key))
            onChange(null)
          }}
        />
      )}
    </>
  )
}

/** ⋮ 를 누르면 상자 아래에 뜨는 작은 메뉴. 바깥을 누르거나 Esc 로 닫힌다. */
function Menu({
  items,
  onClose,
  footer,
}: {
  items: { label: string; danger?: boolean; run: () => void }[]
  onClose: () => void
  /** 메뉴 아래의 누를 수 없는 안내 줄 */
  footer?: ReactNode
}) {
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    // 메뉴를 연 그 클릭이 곧바로 닫지 않게 다음 차례부터 듣는다
    const timer = window.setTimeout(() => window.addEventListener('mousedown', onDown), 0)
    window.addEventListener('keydown', onKey)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  return (
    <div
      ref={box}
      className="absolute top-full right-0 z-20 mt-1 min-w-32 rounded-md border border-edge bg-panel py-1 shadow-lg"
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          onClick={() => {
            onClose()
            item.run()
          }}
          className={`block w-full cursor-pointer px-3 py-1.5 text-left text-sm hover:bg-hover ${
            item.danger ? 'text-danger' : 'text-gray-100'
          }`}
        >
          {item.label}
        </button>
      ))}
      {footer && <div className="mt-1 border-t border-edge px-3 pt-1.5 pb-1 text-xs text-gray-500">{footer}</div>}
    </div>
  )
}

/** "12.3 MB" */
function bytesText(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`
}

/** 스냅샷 용량 — 메뉴를 열 때 묻는다. 자동으로 지우지 않으므로 얼마나 쌓였는지 보이게 한다 */
/** 스냅샷 용량을 1분 기억한다 — 메뉴를 열 때마다 서버가 폴더를 훑지 않게 (한 번에 0.2초쯤) */
const sizeCache = new Map<string, { bytes: number; at: number }>()

function SnapshotSize({ projectKey, prefix = '스냅샷' }: { projectKey: string; prefix?: string }) {
  const [bytes, setBytes] = useState<number | null>(() => {
    const hit = sizeCache.get(projectKey)
    return hit && Date.now() - hit.at < 60_000 ? hit.bytes : null
  })
  useEffect(() => {
    let alive = true
    const hit = sizeCache.get(projectKey)
    if (hit && Date.now() - hit.at < 60_000) return
    fetchSnapshotSize(projectKey)
      .then((r) => {
        sizeCache.set(projectKey, { bytes: r.bytes, at: Date.now() })
        if (alive) setBytes(r.bytes)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [projectKey])
  return (
    <span title="고치기 직전 · 직후 내용. 자동으로 지우지 않습니다 (등록 해제 때 지울 수 있습니다)">
      {prefix} {bytes == null ? '…' : bytesText(bytes)}
    </span>
  )
}

function RenameDialog({
  project,
  onClose,
  onDone,
}: {
  project: ProjectRow
  onClose: () => void
  onDone: (row: ProjectRow) => void
}) {
  const [name, setName] = useState(project.name)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    const result = await renameProject(project.key, name).catch((e) => ({ error: reasonOf(e) }))
    setBusy(false)
    if ('key' in result && !result.error) onDone(result as ProjectRow)
    else setMessage(result.error ?? '바꾸지 못했습니다')
  }

  return (
    <Modal
      title="이름 바꾸기"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON} onClick={onClose}>
            취소
          </button>
          <button type="button" className={`${BUTTON} border-accent`} disabled={busy || !name.trim()} onClick={save}>
            바꾸기
          </button>
        </>
      }
    >
      {message && <p className={`mb-3 ${NOTICE}`}>{message}</p>}
      <input
        autoFocus
        className={INPUT}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && name.trim() && save()}
      />
      <p className="mt-2 text-xs text-gray-500">
        화면에 보이는 이름만 바뀝니다. 폴더와 기록은 그대로이고, 이미 붙은 세션 이름(예: 옛이름_001)도 그대로입니다.
      </p>
    </Modal>
  )
}

function RemoveDialog({
  project,
  onClose,
  onDone,
}: {
  project: ProjectRow
  onClose: () => void
  onDone: () => void
}) {
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [dropSnapshots, setDropSnapshots] = useState(false)

  async function remove() {
    setBusy(true)
    const result = await unregisterProject(project.key, dropSnapshots).catch((e) => ({ error: reasonOf(e) }))
    setBusy(false)
    if (result.error) setMessage(result.error)
    else onDone()
  }

  return (
    <Modal
      title="등록 해제"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON} onClick={onClose}>
            취소
          </button>
          <button type="button" className={`${BUTTON} border-danger`} disabled={busy} onClick={remove}>
            등록 해제
          </button>
        </>
      }
    >
      {message && <p className={`mb-3 ${NOTICE}`}>{message}</p>}
      <p className="text-sm text-gray-100">'{project.name}' 의 등록을 풀까요?</p>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-gray-400">
        <li>목록에서 빠지고, 이 프로젝트에서 일하는 세션의 작업 기록이 멈춥니다.</li>
        <li>지금까지의 작업 · 변경 기록은 지우지 않습니다. 같은 폴더를 다시 추가하면 그대로 이어집니다.</li>
        <li>[세션 고르기]로 이 프로젝트에 넣어 둔 세션은 작업 폴더 기준 소속으로 돌아갑니다.</li>
      </ul>
      <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm text-gray-200">
        <input type="checkbox" checked={dropSnapshots} onChange={(e) => setDropSnapshots(e.target.checked)} />
        <span>
          스냅샷도 지우기 <span className="text-xs text-gray-500">(<SnapshotSize projectKey={project.key} prefix="" />)</span>
        </span>
      </label>
      <p className="mt-1 pl-6 text-xs text-gray-500">
        고치기 직전 · 직후 내용입니다. 지우면 작업별 · 차례별 비교를 다시 볼 수 없습니다.
      </p>
      <p className="mt-2 truncate text-xs text-gray-500" title={project.root}>
        {project.root}
      </p>
    </Modal>
  )
}
