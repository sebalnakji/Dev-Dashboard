import { useState } from 'react'

import { patchScreen, removeScreen, saveScreenOrder } from '../api/client'
import { Modal } from '../components/Modal'
import { BUTTON, INPUT, NOTICE, reasonOf } from '../components/styles'
import { goToSplit } from '../hooks/useHashRoute'
import { Menu } from '../projects/ProjectBar'
import type { Screen, ScreensResponse } from '../types'

/**
 * 화면분할 탭 위쪽의 화면 이름 줄 (프로젝트 줄과 같은 모양).
 *   - 이름 상자를 꾹 눌러 끌면 순서가 바뀐다 (놓으면 저장)
 *   - 상자 오른쪽 ⋮ 로 이름 바꾸기 · 지우기
 */
export function ScreenBar({
  screens,
  currentId,
  onChange,
  onDragging,
}: {
  screens: Screen[]
  currentId: string | null
  /** 서버가 돌려준 화면 목록 (고친 뒤) */
  onChange: (screens: Screen[]) => void
  /** 끄는 동안 주기 갱신이 순서를 되돌리지 않게 알린다 */
  onDragging: (dragging: boolean) => void
}) {
  const [order, setOrder] = useState<Screen[] | null>(null)
  const [dragId, setDragId] = useState<string | null>(null)
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<Screen | null>(null)
  const [removing, setRemoving] = useState<Screen | null>(null)
  const shown = order ?? screens

  function moveOver(overId: string) {
    if (!dragId || dragId === overId) return
    const list = [...shown]
    const a = list.findIndex((s) => s.id === dragId)
    const b = list.findIndex((s) => s.id === overId)
    if (a < 0 || b < 0) return
    const [moved] = list.splice(a, 1)
    list.splice(b, 0, moved)
    setOrder(list)
  }

  async function finishDrag() {
    const list = order
    setDragId(null)
    setOrder(null)
    onDragging(false)
    if (!list || list.map((s) => s.id).join() === screens.map((s) => s.id).join()) return
    onChange(list)
    const r = await saveScreenOrder(list.map((s) => s.id)).catch(() => null)
    if (r?.screens) onChange(r.screens)
  }

  return (
    <>
      {shown.map((s) => (
        <div
          key={s.id}
          draggable
          onDragStart={(e) => {
            setDragId(s.id)
            onDragging(true)
            setMenuFor(null)
            e.dataTransfer.effectAllowed = 'move'
          }}
          onDragOver={(e) => {
            e.preventDefault()
            moveOver(s.id)
          }}
          onDrop={(e) => e.preventDefault()}
          onDragEnd={finishDrag}
          className={`relative flex max-w-[280px] shrink-0 items-center rounded-md text-sm ${
            dragId === s.id ? 'opacity-50' : ''
          } ${s.id === currentId ? 'bg-btn text-gray-100' : 'text-gray-400 hover:bg-hover hover:text-gray-200'}`}
        >
          <button
            type="button"
            onClick={() => goToSplit(s.id)}
            className="flex min-w-0 cursor-pointer items-center gap-1.5 py-1 pr-1 pl-2.5"
          >
            <span className="truncate">{s.name}</span>
            <span className="shrink-0 text-[11px] text-gray-500">{s.layout}분할</span>
          </button>
          <button
            type="button"
            title="이름 바꾸기 · 지우기"
            onClick={() => setMenuFor(menuFor === s.id ? null : s.id)}
            className="cursor-pointer rounded px-1.5 py-1 text-gray-500 hover:text-gray-100"
          >
            ⋮
          </button>
          {menuFor === s.id && (
            <Menu
              onClose={() => setMenuFor(null)}
              items={[
                { label: '이름 바꾸기', run: () => setRenaming(s) },
                { label: '지우기', danger: true, run: () => setRemoving(s) },
              ]}
            />
          )}
        </div>
      ))}

      {renaming && (
        <NameDialog
          title="이름 바꾸기"
          action="바꾸기"
          initial={renaming.name}
          onClose={() => setRenaming(null)}
          onSubmit={(name) => patchScreen(renaming.id, { name })}
          onDone={(r) => {
            setRenaming(null)
            onChange(r.screens)
          }}
        />
      )}
      {removing && (
        <RemoveDialog
          screen={removing}
          onClose={() => setRemoving(null)}
          onDone={(r) => {
            setRemoving(null)
            // 보던 화면이면 페이지가 남은 첫째로 옮긴다
            onChange(r.screens)
          }}
        />
      )}
    </>
  )
}

/** 화면 이름을 받는 창 (추가 · 이름 바꾸기). 실패 이유는 창 안에 보인다. */
export function NameDialog({
  title,
  action,
  initial = '',
  onClose,
  onSubmit,
  onDone,
}: {
  title: string
  action: string
  initial?: string
  onClose: () => void
  onSubmit: (name: string) => Promise<ScreensResponse & { error?: string }>
  onDone: (r: ScreensResponse) => void
}) {
  const [name, setName] = useState(initial)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    const result = await onSubmit(name).catch((e) => ({ error: reasonOf(e), screens: [] }))
    setBusy(false)
    if (result.error) setMessage(result.error)
    else onDone(result)
  }

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON} onClick={onClose}>
            취소
          </button>
          <button type="button" className={`${BUTTON} border-accent`} disabled={busy || !name.trim()} onClick={save}>
            {action}
          </button>
        </>
      }
    >
      {message && <p className={`mb-3 ${NOTICE}`}>{message}</p>}
      <input
        autoFocus
        className={INPUT}
        value={name}
        maxLength={40}
        placeholder="예: 업무 · 개인 공부"
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && name.trim() && !busy && save()}
      />
    </Modal>
  )
}

function RemoveDialog({
  screen,
  onClose,
  onDone,
}: {
  screen: Screen
  onClose: () => void
  onDone: (r: ScreensResponse) => void
}) {
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function remove() {
    setBusy(true)
    const result = await removeScreen(screen.id).catch((e) => ({ error: reasonOf(e), screens: [] }))
    setBusy(false)
    if (result.error) setMessage(result.error)
    else onDone(result)
  }

  return (
    <Modal
      title="화면 지우기"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON} onClick={onClose}>
            취소
          </button>
          <button type="button" className={`${BUTTON} border-danger`} disabled={busy} onClick={remove}>
            지우기
          </button>
        </>
      }
    >
      {message && <p className={`mb-3 ${NOTICE}`}>{message}</p>}
      <p className="text-sm text-gray-100">'{screen.name}' 화면을 지울까요?</p>
      <p className="mt-2 text-xs text-gray-400">
        칸 배치만 지워집니다. 세션과 대화는 그대로입니다.
      </p>
    </Modal>
  )
}
