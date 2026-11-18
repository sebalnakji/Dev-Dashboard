import { useCallback, useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react'

import {
  addGroup,
  deleteGroup,
  fetchSessions,
  patchGroup,
  placeSession,
  postRestore,
  postSessionAction,
  type SessionAction,
} from '../api/client'
import { Column, ColumnTitle } from '../components/Column'
import { ContextMeter } from '../components/ContextMeter'
import { BUTTON, OFFLINE } from '../components/styles'
import { useNow } from '../hooks/useNow'
import type { ActionResult, DeletedSession, SessionRow, SessionState, SessionsView } from '../types'
import { NameModal, NewSessionModal } from './SessionDialogs'

const REFRESH_MS = 3000
const NOTICE_MS = 4000
/** 실패는 이유가 길고 늦게 알게 되어 더 오래 보여준다. */
const FAIL_NOTICE_MS = 10000
/** 서버에 저장하지 않는 묶음(분류 안 됨 · 대화형 · 삭제한 세션)의 접힘. 이 브라우저만 기억한다 */
const CLOSED_KEY = 'closedSections'

/** 화면 안에서 "분류 안 됨"을 가리키는 열쇠. 그룹 이름은 비어 있을 수 없어 겹치지 않는다 */
const UNGROUPED = ''

function loadClosed(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(CLOSED_KEY) ?? '[]') as string[])
  } catch {
    return new Set()
  }
}

type Dialog =
  | { kind: 'new'; group: string | null }
  | { kind: 'addGroup' }
  | { kind: 'renameGroup'; name: string }

/** 끌어 놓을 자리: 그 그룹의 index 번째 (끄는 세션을 뺀 목록 기준) */
interface DropAt {
  group: string
  index: number
}

/**
 * 가운데 칸(메인): Claude 세션. 등록 없이 `claude agents --all` 의 세션이 모두 보이고,
 * 사람은 그룹 · 순서만 정한다. 필요 없는 세션은 [삭제] (Claude 목록에서도 빠지고 30일 동안 복원 가능).
 * 하나씩 연결/해제해 쓸 것만 켜 둔다.
 *
 * 버튼을 누르면 응답을 기다리는 동안 그 줄을 먼저 '연결 중/해제 중'으로 바꿔 둔다.
 * 그 사이 주기 갱신이 옛 상태로 덮어쓰지 않도록 요청 중에는 갱신을 건너뛴다.
 */
export function SessionPanel() {
  const [view, setView] = useState<SessionsView | null>(null)
  const [offline, setOffline] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [pending, setPending] = useState<Record<string, SessionState>>({})
  const [closed, setClosed] = useState(loadClosed)
  const [menu, setMenu] = useState<string | null>(null)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [dragId, setDragId] = useState<string | null>(null)
  const [dropAt, setDropAt] = useState<DropAt | null>(null)
  const busy = useRef(false)
  const shownFailures = useRef(new Set<string>())
  const noticeTimer = useRef<number | undefined>(undefined)

  const showNotice = useCallback((message: string, ms = NOTICE_MS) => {
    setNotice(message)
    window.clearTimeout(noticeTimer.current)
    noticeTimer.current = window.setTimeout(() => setNotice(null), ms)
  }, [])

  const refresh = useCallback(async () => {
    if (busy.current) return
    try {
      const next = await fetchSessions()
      setView(next)
      setPending({})
      setOffline(false)
      // 실패는 몇 초 뒤에야 알 수 있어 목록 갱신에 실려 온다.
      // 서버가 같은 실패를 1분 동안 계속 실어 보내므로 (내용, 시각)으로 한 번만 띄운다
      const all = [
        ...next.sessions.filter((r) => r.error).map((r) => ({ error: r.error!, errorAt: r.errorAt! })),
        ...next.failures,
      ]
      const fresh = all.filter((f) => !shownFailures.current.has(`${f.error}${f.errorAt}`))
      for (const f of fresh) shownFailures.current.add(`${f.error}${f.errorAt}`)
      if (fresh.length) showNotice(fresh.map((f) => f.error).join('\n'), FAIL_NOTICE_MS)
    } catch {
      setOffline(true)
    }
  }, [showNotice])

  useEffect(() => {
    // 첫 읽기도 타이머로 미룬다 — 효과 안에서 곧바로 상태를 바꾸지 않게 (react 검사 규칙)
    const first = window.setTimeout(refresh, 0)
    const timer = window.setInterval(refresh, REFRESH_MS)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(timer)
    }
  }, [refresh])

  /** 요청 하나를 보내고 결과를 알린 뒤 다시 읽는다. 확인 요청이면 물어보고 force 로 다시 보낸다 */
  async function run(send: (force: boolean) => Promise<ActionResult>) {
    busy.current = true
    try {
      let result = await send(false)
      if (result.confirm && window.confirm(result.confirm)) result = await send(true)
      if (result.error) showNotice(result.error)
    } catch {
      showNotice('요청이 실패했습니다.')
    }
    busy.current = false
    await refresh()
  }

  function act(row: SessionRow, action: SessionAction) {
    setMenu(null)
    if (action === 'delete') {
      const ask =
        `'${row.name}' 세션을 삭제할까요?\n` +
        'Claude 세션 목록과 앱에서도 빠집니다. 대화 기록은 남아 30일 동안 [삭제한 세션]에서 복원할 수 있습니다.'
      if (!window.confirm(ask)) return
    }
    if (action !== 'foreground') {
      const shown: SessionState = action === 'start' ? 'starting' : action === 'stop' ? 'stopping' : 'deleting'
      setPending((p) => ({ ...p, [row.id]: shown }))
    }
    return run((force) => postSessionAction(row.id, action, force))
  }

  function toggleLocal(key: string) {
    setClosed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      try {
        localStorage.setItem(CLOSED_KEY, JSON.stringify([...next]))
      } catch {
        // 저장이 막힌 브라우저면 이번 화면에서만 기억한다
      }
      return next
    })
  }

  function toggleGroup(name: string, collapsed: boolean) {
    // 바로 접어 보이고, 서버에 저장한다
    setView((v) => v && { ...v, groups: v.groups.map((g) => (g.name === name ? { ...g, collapsed } : g)) })
    return run(() => patchGroup(name, { collapsed }))
  }

  function removeGroup(name: string) {
    setMenu(null)
    if (!window.confirm(`'${name}' 그룹을 지울까요?\n안의 세션은 지우지 않고 "분류 안 됨"으로 옮깁니다.`)) return
    return run(() => deleteGroup(name))
  }

  const stateOf = (row: SessionRow) => pending[row.id] ?? row.state
  const groups = view?.groups ?? []
  const sessions = view?.sessions ?? []
  const rowsOf = (key: string) => sessions.filter((r) => (r.group ?? UNGROUPED) === key)
  // 같은 이름이 여럿인 세션 (옛 판이 남는 일이 흔하다) — 줄에 목록 번호를 붙여 가른다
  const nameCount = new Map<string, number>()
  for (const r of sessions) nameCount.set(r.name, (nameCount.get(r.name) ?? 0) + 1)

  function move(id: string, key: string, index: number) {
    setMenu(null)
    const order = rowsOf(key)
      .map((r) => r.id)
      .filter((x) => x !== id)
    order.splice(Math.max(0, Math.min(index, order.length)), 0, id)
    // 바로 옮겨 보이고, 서버에 저장한다
    setView(
      (v) =>
        v && {
          ...v,
          sessions: v.sessions.map((r) =>
            r.id === id ? { ...r, group: key === UNGROUPED ? null : key, order: order.indexOf(id) } : r,
          ),
        },
    )
    return run(() => placeSession(id, key === UNGROUPED ? null : key, order))
  }

  // --- 끌어 놓기 ---

  function dragOverRow(e: DragEvent, key: string, index: number) {
    if (!dragId) return
    e.preventDefault()
    e.stopPropagation()
    const box = e.currentTarget.getBoundingClientRect()
    const after = e.clientY > box.top + box.height / 2
    const without = rowsOf(key).filter((r) => r.id !== dragId)
    const at = Math.min(index + (after ? 1 : 0), without.length)
    if (dropAt?.group !== key || dropAt.index !== at) setDropAt({ group: key, index: at })
  }

  function dragOverSection(e: DragEvent, key: string) {
    if (!dragId) return
    e.preventDefault()
    const end = rowsOf(key).filter((r) => r.id !== dragId).length
    if (dropAt?.group !== key || dropAt.index !== end) setDropAt({ group: key, index: end })
  }

  function drop(e: DragEvent) {
    e.preventDefault()
    const id = dragId
    const at = dropAt
    setDragId(null)
    setDropAt(null)
    if (id && at) void move(id, at.group, at.index)
  }

  function endDrag() {
    setDragId(null)
    setDropAt(null)
  }

  const interactive = view?.interactive ?? []
  const connected = sessions.filter((r) => stateOf(r) === 'on').length + interactive.length
  const total = sessions.length + interactive.length
  const sections: { key: string; title: string; collapsed: boolean; grouped: boolean }[] = [
    ...groups.map((g) => ({ key: g.name, title: g.name, collapsed: g.collapsed, grouped: true })),
  ]
  // "분류 안 됨"은 세션이 있거나 끌고 있을 때만 (거기로 옮길 수 있게)
  if (rowsOf(UNGROUPED).length || dragId) {
    sections.push({ key: UNGROUPED, title: '분류 안 됨', collapsed: closed.has('ungrouped'), grouped: false })
  }

  return (
    <Column
      title={
        <>
          <ColumnTitle>Claude 세션</ColumnTitle>
          {view && (
            <span className="text-xs text-gray-400">
              연결 {connected} / 전체 {total}
            </span>
          )}
        </>
      }
      side={
        <button
          type="button"
          className={BUTTON}
          title="새 백그라운드 세션을 만듭니다"
          disabled={!view}
          onClick={() => setDialog({ kind: 'new', group: null })}
        >
          + 세션
        </button>
      }
    >
      {offline && <p className="mb-3.5 rounded-md bg-[#3a2621] px-2.5 py-2 text-xs text-[#f0b8a4]">{OFFLINE}</p>}
      {notice && (
        <p className="mb-3.5 whitespace-pre-line rounded-md bg-btn px-2.5 py-2 text-xs text-gray-100">{notice}</p>
      )}
      {view == null && !offline && <p className="px-1.5 text-sm text-gray-500">불러오는 중…</p>}
      {view && total === 0 && (
        <p className="px-1.5 text-sm text-gray-500">Claude 백그라운드 세션이 없습니다. [+ 세션]으로 만듭니다.</p>
      )}

      {/* 메뉴가 열려 있으면 바깥을 눌러 닫는다 */}
      {menu && <div className="fixed inset-0 z-10" onMouseDown={() => setMenu(null)} />}

      {sections.map((s) => {
        const rows = rowsOf(s.key)
        const isClosed = s.collapsed
        return (
          <section
            key={`g:${s.key}`}
            className="mb-[18px]"
            onDragOver={(e) => dragOverSection(e, s.key)}
            onDrop={drop}
          >
            <SectionHeader
              title={s.title}
              count={isClosed ? rows.length : undefined}
              closed={isClosed}
              onToggle={() => (s.grouped ? toggleGroup(s.key, !isClosed) : toggleLocal('ungrouped'))}
            >
              {s.grouped && (
                <>
                  <IconButton title={`'${s.title}' 그룹에 새 세션`} onClick={() => setDialog({ kind: 'new', group: s.key })}>
                    +
                  </IconButton>
                  <Menu
                    open={menu === `group:${s.key}`}
                    onOpen={() => setMenu(`group:${s.key}`)}
                    items={[
                      { label: '이름 바꾸기', onClick: () => (setMenu(null), setDialog({ kind: 'renameGroup', name: s.key })) },
                      { label: '그룹 지우기', danger: true, onClick: () => removeGroup(s.key) },
                    ]}
                  />
                </>
              )}
            </SectionHeader>
            {!isClosed &&
              rows.map((row) => (
                <SessionLine
                  key={row.id}
                  row={row}
                  state={stateOf(row)}
                  dropBefore={dropAt?.group === s.key && dropAt.index === rows.filter((r) => r.id !== dragId).indexOf(row)}
                  dragging={dragId === row.id}
                  duplicate={(nameCount.get(row.name) ?? 0) > 1}
                  onDragStart={() => (setMenu(null), setDragId(row.id))}
                  onDragEnd={endDrag}
                  onDragOver={(e) => dragOverRow(e, s.key, rows.filter((r) => r.id !== dragId).indexOf(row))}
                  onAct={act}
                  menu={
                    <Menu
                      open={menu === row.id}
                      onOpen={() => setMenu(row.id)}
                      items={[
                        ...[...groups.map((g) => g.name), UNGROUPED]
                          .filter((g) => g !== s.key)
                          .map((g) => ({
                            label: `→ ${g || '분류 안 됨'}`,
                            onClick: () => move(row.id, g, rowsOf(g).length),
                          })),
                        { label: '삭제', danger: true, onClick: () => act(row, 'delete') },
                      ]}
                    />
                  }
                />
              ))}
            {/* 끝에 놓을 자리 표시 (빈 그룹 · 접힌 그룹 포함) */}
            {dropAt?.group === s.key && dropAt.index === rows.filter((r) => r.id !== dragId).length && (
              <div className="mx-1.5 h-0.5 rounded bg-accent" />
            )}
            {!isClosed && rows.length === 0 && !dragId && (
              <p className="px-1.5 py-1 text-xs text-gray-600">세션을 여기로 끌어 놓습니다</p>
            )}
          </section>
        )
      })}

      {view && (
        <button
          type="button"
          onClick={() => setDialog({ kind: 'addGroup' })}
          className="mb-[18px] cursor-pointer px-1.5 text-xs text-gray-500 hover:text-accent"
        >
          + 새 그룹
        </button>
      )}

      {interactive.length > 0 && (
        <section className="mb-[18px]">
          <SectionHeader
            title="대화형"
            count={closed.has('interactive') ? interactive.length : undefined}
            closed={closed.has('interactive')}
            onToggle={() => toggleLocal('interactive')}
          />
          {!closed.has('interactive') &&
            interactive.map((s) => (
              <div
                key={s.sessionId}
                className="flex items-center gap-2.5 rounded-md px-1.5 py-[7px] hover:bg-hover"
                title={`터미널에서 연 세션입니다. 그 터미널을 닫아야 꺼집니다.\n${s.cwd}`}
              >
                <span className="min-w-0 flex-1 truncate text-gray-100">{s.name}</span>
                <ContextMeter tokens={s.tokens} compactions={s.compactions} />
                <MonitorIcon state="terminal" />
                <span className="w-[78px] shrink-0 text-right">
                  <TerminalBadge />
                </span>
                <span className="w-6 shrink-0" />
              </div>
            ))}
        </section>
      )}

      {(view?.deleted.length ?? 0) > 0 && (
        <section className="mb-[18px]">
          <SectionHeader
            title="삭제한 세션"
            closed={closed.has('deleted')}
            onToggle={() => toggleLocal('deleted')}
            note={`${view!.deleted.length}개 · 30일 동안 복원할 수 있습니다`}
          />
          {!closed.has('deleted') &&
            view!.deleted.map((d) => (
              <DeletedLine key={d.id} item={d} onRestore={() => run(() => postRestore(d.id))} />
            ))}
        </section>
      )}

      {dialog?.kind === 'new' && (
        <NewSessionModal groups={groups} presetGroup={dialog.group} onClose={() => setDialog(null)} onCreated={refresh} />
      )}
      {dialog?.kind === 'addGroup' && (
        <NameModal
          title="새 그룹"
          initial=""
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            const result = await addGroup(name)
            if (result.ok) await refresh()
            return result
          }}
        />
      )}
      {dialog?.kind === 'renameGroup' && (
        <NameModal
          title="그룹 이름 바꾸기"
          initial={dialog.name}
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            const result = await patchGroup(dialog.name, { name })
            if (result.ok) await refresh()
            return result
          }}
        />
      )}
    </Column>
  )
}

function SectionHeader({
  title,
  closed,
  count,
  note,
  onToggle,
  children,
}: {
  title: string
  closed: boolean
  /** 접혀 있을 때 안의 세션 수 */
  count?: number
  note?: string
  onToggle: () => void
  /** 오른쪽 버튼들 ([+] · ⋯). 늘 보인다 — 마우스를 올려야 보이면 있는 줄 모른다 */
  children?: ReactNode
}) {
  return (
    <div className="flex items-center gap-1.5 px-1.5 py-1">
      <button
        type="button"
        onClick={onToggle}
        className="flex cursor-pointer items-center gap-1.5 text-xs tracking-wide text-gray-400 hover:text-gray-100"
      >
        {title}
        <span className={`text-[9px] transition-transform ${closed ? '-rotate-90' : ''}`}>▼</span>
        {count != null && <span className="text-gray-600">{count}</span>}
      </button>
      {note && <span className="text-[11px] text-gray-600">{note}</span>}
      <span className="ml-auto flex items-center gap-1">
        {children}
      </span>
    </div>
  )
}

function IconButton({ title, onClick, children }: { title: string; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="h-6 w-6 cursor-pointer rounded-[5px] text-sm leading-none text-gray-400 hover:bg-btn hover:text-gray-100"
    >
      {children}
    </button>
  )
}

/** ⋯ 메뉴. 바깥 누르기 처리는 칸이 맡는다 (열린 메뉴 하나만) */
function Menu({
  open,
  onOpen,
  items,
}: {
  open: boolean
  onOpen: () => void
  items: { label: string; danger?: boolean; onClick: () => void }[]
}) {
  return (
    <span className="relative">
      <IconButton title="메뉴" onClick={onOpen}>
        ⋯
      </IconButton>
      {open && (
        <span className="absolute right-0 top-7 z-20 flex min-w-36 flex-col rounded-md border border-edge bg-panel py-1 shadow-lg">
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              onClick={item.onClick}
              className={`cursor-pointer px-3 py-1.5 text-left text-xs hover:bg-hover ${
                item.danger ? 'text-[#f0b8a4]' : 'text-gray-100'
              }`}
            >
              {item.label}
            </button>
          ))}
        </span>
      )}
    </span>
  )
}

const ACTION: Partial<Record<SessionState, { label: string; action?: SessionAction }>> = {
  on: { label: '해제', action: 'stop' },
  off: { label: '연결', action: 'start' },
  starting: { label: '연결 중…' },
  stopping: { label: '해제 중…' },
  deleting: { label: '삭제 중…' },
}

function SessionLine({
  row,
  state,
  dropBefore,
  dragging,
  duplicate,
  onDragStart,
  onDragEnd,
  onDragOver,
  onAct,
  menu,
}: {
  row: SessionRow
  state: SessionState
  /** 이 줄 위에 놓을 자리 표시 */
  dropBefore: boolean
  dragging: boolean
  /** 같은 이름의 세션이 또 있다 */
  duplicate: boolean
  onDragStart: () => void
  onDragEnd: () => void
  onDragOver: (e: DragEvent) => void
  onAct: (row: SessionRow, action: SessionAction) => void
  menu: ReactNode
}) {
  const action = ACTION[state]
  // 포그라운드: 꺼졌으면 터미널 창에서 이어 열고, 켜졌으면 붙는다
  const fgTitle =
    state === 'on'
      ? '터미널 창에서 이 세션에 붙습니다 (창을 닫아도 세션은 백그라운드에서 계속 돕니다)'
      : '터미널 창에서 이 세션을 엽니다 (창을 닫으면 세션도 꺼집니다)'

  return (
    <>
      {dropBefore && <div className="mx-1.5 h-0.5 rounded bg-accent" />}
      <div
        draggable={state !== 'deleting'}
        onDragStart={(e) => {
          e.dataTransfer.effectAllowed = 'move'
          e.dataTransfer.setData('text/plain', row.id)
          onDragStart()
        }}
        onDragEnd={onDragEnd}
        onDragOver={onDragOver}
        className={`flex cursor-grab items-center gap-2.5 rounded-md px-1.5 py-[7px] hover:bg-hover ${
          dragging ? 'opacity-40' : ''
        }`}
      >
        <span
          title={row.cwd}
          className={`min-w-0 flex-1 truncate ${state === 'off' ? 'text-gray-400' : 'text-gray-100'} ${
            row.named ? '' : 'italic'
          }`}
        >
          {row.name}
          {/* 같은 이름이 여럿이면 목록 번호로 가른다 (이름 없는 세션은 이미 번호가 붙어 있다) */}
          {duplicate && row.named && <span className="ml-1.5 text-[11px] text-gray-600">{row.id.slice(0, 8)}</span>}
        </span>
        <ContextMeter tokens={row.tokens} compactions={row.compactions} />
        {/* 다른 상태엔 자리만 비워 둬서 아이콘 · 버튼 열이 흐트러지지 않게 한다 */}
        <span className="w-[30px] shrink-0 text-right">
          {(state === 'on' || state === 'off') && (
            <button
              type="button"
              title={fgTitle}
              onClick={() => onAct(row, 'foreground')}
              className="w-7 cursor-pointer rounded-[5px] border border-edge bg-btn py-0.5 font-mono text-[11px] font-semibold text-gray-400 hover:border-accent hover:text-accent"
            >
              &gt;_
            </button>
          )}
        </span>
        <MonitorIcon state={state} />
        <span className="w-[78px] shrink-0 text-right">
          {action && (
            <button
              type="button"
              disabled={!action.action}
              onClick={() => action.action && onAct(row, action.action)}
              className={`${BUTTON} ${state === 'on' ? 'hover:border-danger' : ''}`}
            >
              {action.label}
            </button>
          )}
        </span>
        <span className="w-6 shrink-0">{state !== 'deleting' && menu}</span>
      </div>
    </>
  )
}

function DeletedLine({ item, onRestore }: { item: DeletedSession; onRestore: () => void }) {
  const now = useNow()
  const days = Math.floor((now / 1000 - item.deletedAt) / 86400)
  const when = days < 1 ? '오늘' : `${days}일 전`
  return (
    <div className="flex items-center gap-2.5 rounded-md px-1.5 py-[7px] hover:bg-hover" title={item.cwd}>
      <span className="min-w-0 flex-1 truncate text-gray-500">{item.name}</span>
      <span className="shrink-0 text-[11px] text-gray-600">
        {item.group ? `${item.group} · ` : ''}
        {when} 삭제
      </span>
      <span className="w-[78px] shrink-0 text-right">
        <button
          type="button"
          className={BUTTON}
          disabled={!item.restorable || item.restoring}
          title={item.restorable ? '마지막 대화로 다시 띄우고 그룹을 되돌립니다' : '대화가 정리돼 복원할 수 없습니다'}
          onClick={onRestore}
        >
          {item.restoring ? '복원 중…' : '복원'}
        </button>
      </span>
      <span className="w-6 shrink-0" />
    </div>
  )
}

/** 누를 수 없는 표시라 버튼과 다르게 — 배경 없음 · 점선 · 파란 글자 */
function TerminalBadge() {
  return (
    <span className="inline-block min-w-16 select-none rounded-[5px] border border-dashed border-accent px-2.5 py-[3px] text-center text-xs text-accent">
      터미널
    </span>
  )
}

/** 연결됨: 밝은 모니터 + 초록 점 · 꺼짐: 흐린 회색 · 바뀌는 중: 깜빡임 · 터미널: 화면에 >_ */
function MonitorIcon({ state }: { state: SessionState }) {
  const color = {
    on: 'text-gray-100',
    terminal: 'text-gray-100',
    off: 'text-gray-600',
    starting: 'animate-pulse text-gray-400',
    stopping: 'animate-pulse text-gray-400',
    deleting: 'animate-pulse text-gray-600',
  }[state]
  return (
    <span className={`relative h-[18px] w-[22px] shrink-0 ${color}`}>
      <svg viewBox="0 0 22 18" fill="none" stroke="currentColor" strokeWidth="1.5" className="block h-[18px] w-[22px]">
        <rect x="1.5" y="1.5" width="19" height="11.5" rx="1.5" />
        <path d="M8 16.5h6M11 13v3.5" />
        {state === 'terminal' && <path d="M6 5.5l2 1.75L6 9M10 9h3" strokeWidth="1.3" />}
      </svg>
      {state === 'on' && (
        <span className="absolute right-[-2px] bottom-px h-[7px] w-[7px] rounded-full border-[1.5px] border-ground bg-on" />
      )}
    </span>
  )
}
