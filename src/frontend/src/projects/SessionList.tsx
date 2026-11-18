import { useCallback, useEffect, useState } from 'react'

import { fetchSessionChoices, postSessionAction, saveProjectSessions } from '../api/client'
import { ContextMeter } from '../components/ContextMeter'
import { Modal } from '../components/Modal'
import { BUTTON, NOTICE, agoIso, reasonOf } from '../components/styles'
import { useNow } from '../hooks/useNow'
import type { ActionResult, ProjectSession, SessionChoice } from '../types'
import { rememberSeen, seenAt } from './remember'

/** 백엔드가 보내는 최근 답 시각의 수 (chat.REPLY_WINDOW). 새 답이 이만큼이면 '20+' 로 보인다. */
const REPLY_WINDOW = 20

/**
 * 새 답 수('+3')의 색: 바탕 없이 글자만 빨강 (사용자 결정). 오류 · 위험 색(danger, 주황빛)과
 * 섞여 '문제가 생겼다'로 읽히지 않게 더 선명한 빨강을 따로 쓴다. 바탕 대비 5.3:1 · hover 4.5:1
 */
const FRESH = 'text-[#f06368]'

/** 확인이 필요할 때의 색. 위험(주황)과 구분되는 노랑 */
const BLOCKED = 'text-[#e3b75f]'

/** 세션이 지금 무엇을 하나: 점 색 · 글 · 글 색 */
function statusOf(s: ProjectSession): { dot: string; label: string; text: string } {
  if (s.state === 'starting') return { dot: 'bg-gray-600 animate-pulse', label: '연결 중', text: 'text-gray-500' }
  if (s.state === 'stopping') return { dot: 'bg-gray-600 animate-pulse', label: '해제 중', text: 'text-gray-500' }
  if (s.state === 'off') return { dot: 'bg-gray-700', label: '꺼짐', text: 'text-gray-600' }
  if (s.activity === 'working') return { dot: 'bg-accent animate-pulse', label: '답변 중', text: 'text-accent' }
  if (s.activity === 'blocked') return { dot: 'bg-[#e3b75f]', label: '확인 필요', text: BLOCKED }
  return { dot: 'bg-on', label: '쉬는 중', text: 'text-gray-500' }
}

/**
 * 마지막으로 본 뒤 끝난 답의 수. 처음 보는 세션 · 지금 보고 있는 세션은 0.
 * 답을 만드는 중이면 마지막 답은 아직 끝나지 않았으므로 세지 않는다 (8단계 Fable 검토 — '+1'이 미리 떴다).
 */
function freshCount(s: ProjectSession, selected: boolean): number {
  const seen = seenAt(s.id)
  if (selected || !seen) return 0
  const since = Date.parse(seen)
  const done = s.activity === 'working' ? s.replies.slice(0, -1) : s.replies
  return done.filter((at) => Date.parse(at) > since).length
}

/**
 * [세션] 보기 왼쪽: 이 프로젝트의 세션 목록. 누르면 오른쪽에 그 세션의 대화가 열린다 (T-8).
 * 한 줄에 이름 · 지금 하는 일(답변 중 / 확인 필요 / 쉬는 중 / 꺼짐) · 마지막으로 본 뒤 새 답 수 ·
 * 시각 (T-9). 대화 미리보기는 두지 않는다 (사용자 결정). 본 시각은 이 브라우저가 세션마다 기억한다.
 * 어떤 세션이 이 프로젝트 것인지는 작업 폴더로 자동으로 정해지고, 바꾸고 싶을 때만 [세션 고르기]로 바꾼다.
 */
export function SessionList({
  sessions,
  selected,
  onSelect,
  onChanged,
}: {
  sessions: ProjectSession[]
  selected: string | null
  onSelect: (id: string) => void
  /** 연결 · 해제를 누른 뒤 목록을 다시 읽게 한다 */
  onChanged: () => void
}) {
  // "3분 전" 이 흐르게. 이 칸만 1초마다 다시 그린다 (프로젝트 화면 전체가 아니라)
  const now = useNow()
  const [notice, setNotice] = useState<string | null>(null)

  // 연결 · 해제 (T-17): 모니터링 탭의 세션 칸과 같은 API · 같은 확인 창을 쓴다
  async function toggle(s: ProjectSession) {
    const action = s.state === 'off' ? 'start' : 'stop'
    let result = await postSessionAction(s.id, action).catch((e) => ({ error: reasonOf(e) }) as ActionResult)
    // 서버 같은 백그라운드 작업이 돌고 있으면 한 번 묻고, 확인하면 끝까지 해제한다
    if (result.confirm && window.confirm(result.confirm)) {
      result = await postSessionAction(s.id, action, true).catch((e) => ({ error: reasonOf(e) }) as ActionResult)
    }
    setNotice(result.error ?? null)
    onChanged()
  }

  // 본 시각 기억: 보고 있는 세션은 마지막 답까지 본 것으로, 처음 보는 세션은 지금부터 센다.
  // 화면에 그리는 수는 위 freshCount 가 그때그때 계산하므로 여기서 상태를 바꾸지 않는다
  useEffect(() => {
    for (const s of sessions) {
      const seen = seenAt(s.id)
      const last = s.replies.at(-1)
      if (s.id === selected && last && (!seen || Date.parse(last) > Date.parse(seen))) rememberSeen(s.id, last)
      else if (!seen) rememberSeen(s.id, new Date().toISOString())
    }
  }, [sessions, selected])

  if (sessions.length === 0) {
    return <p className="px-1.5 text-sm text-gray-500">고른 세션이 없습니다.</p>
  }
  return (
    <div className="space-y-1">
      {notice && (
        <p className={`mb-2 ${NOTICE}`} onClick={() => setNotice(null)}>
          {notice}
        </p>
      )}
      {sessions.map((s) => {
        const status = statusOf(s)
        const fresh = freshCount(s, s.id === selected)
        const off = s.state === 'off'
        return (
          // 줄 전체가 고르기 버튼이고, 연결 · 해제는 그 위에 따로 얹는다 (버튼 안에 버튼을 넣을 수 없다)
          <div key={s.id} className="relative">
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              className={`block w-full cursor-pointer rounded-md px-3 py-2.5 text-left ${
                s.id === selected ? 'bg-btn' : 'hover:bg-hover'
              }`}
            >
              <span className="flex items-center gap-2.5">
                <span className={`h-2 w-2 shrink-0 rounded-full ${status.dot}`} />
                <span className={`min-w-0 flex-1 truncate text-[15px] font-semibold ${off ? 'text-gray-400' : 'text-gray-100'}`}>
                  {s.name}
                </span>
                {fresh > 0 && (
                  <span title={`마지막으로 본 뒤 새 답 ${fresh}개`} className={`shrink-0 text-[13px] font-semibold ${FRESH}`}>
                    {fresh >= REPLY_WINDOW ? `${REPLY_WINDOW}+` : `+${fresh}`}
                  </span>
                )}
              </span>
              <span className="mt-1 ml-[18px] flex items-center gap-1.5 pr-14 text-xs">
                <span className={status.text}>{status.label}</span>
                {s.lastAt && <span className="text-gray-600">· {agoIso(s.lastAt, now)}</span>}
                {(s.tokens != null || s.compactions > 0) && (
                  <>
                    <span className="text-gray-600">·</span>
                    <ContextMeter tokens={s.tokens} compactions={s.compactions} />
                  </>
                )}
              </span>
            </button>
            <SwitchButton session={s} onClick={() => void toggle(s)} />
          </div>
        )
      })}
    </div>
  )
}

/** 세션 줄 오른쪽 아래의 [연결] · [해제]. 터미널에서 연 세션은 그 창을 닫아야 꺼지므로 두지 않는다 */
function SwitchButton({ session, onClick }: { session: ProjectSession; onClick: () => void }) {
  if (session.state === 'terminal') return null
  const busy = session.state === 'starting' || session.state === 'stopping'
  const label = session.state === 'starting' ? '연결 중' : session.state === 'stopping' ? '해제 중' : session.state === 'off' ? '연결' : '해제'
  return (
    <button
      type="button"
      disabled={busy}
      onClick={onClick}
      className="absolute right-2.5 bottom-2 cursor-pointer rounded border border-edge px-1.5 py-px text-[11px] text-gray-400 hover:border-gray-500 hover:text-gray-100 disabled:cursor-default disabled:opacity-60"
    >
      {label}
    </button>
  )
}

/**
 * [세션 고르기] 창: 세션 목록(모니터링 탭의 세션) 전부를 그룹별로 체크한다.
 * 지금 소속(작업 폴더로 자동 + 사람이 바꾼 것)이 체크된 채로 열린다. 서버는 자동과 다르게 고른
 * 것만 적어 둔다.
 */
export function SessionPicker({
  projectKey,
  onClose,
  onSaved,
}: {
  projectKey: string
  onClose: () => void
  onSaved: () => void
}) {
  const [choices, setChoices] = useState<SessionChoice[] | null>(null)
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    fetchSessionChoices(projectKey)
      .then((list) => {
        setChoices(list)
        setChecked(new Set(list.filter((c) => c.member).map((c) => c.id)))
      })
      .catch((e) => setMessage(reasonOf(e)))
  }, [projectKey])

  // 창을 여는 순간 한 번 읽는다 (효과 안에서 곧바로 상태를 바꾸지 않게 타이머로 미룬다)
  useEffect(() => {
    const first = window.setTimeout(load, 0)
    return () => window.clearTimeout(first)
  }, [load])

  async function save() {
    setSaving(true)
    const result = await saveProjectSessions(projectKey, [...checked]).catch((e) => ({ error: reasonOf(e) }))
    setSaving(false)
    if (result.error) {
      // 창을 연 사이 세션이 삭제됐으면 목록을 다시 읽어 보여 준다
      setMessage(result.error)
      load()
      return
    }
    onSaved()
    onClose()
  }

  const groups = new Map<string, SessionChoice[]>()
  for (const c of choices ?? []) groups.set(c.group, [...(groups.get(c.group) ?? []), c])

  return (
    <Modal
      title="세션 고르기"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON} onClick={onClose}>
            취소
          </button>
          <button type="button" className={`${BUTTON} border-accent`} disabled={saving || choices == null} onClick={save}>
            저장
          </button>
        </>
      }
    >
      {message && <p className={`mb-3 ${NOTICE}`}>{message}</p>}
      {choices == null && !message && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {[...groups.entries()].map(([group, list]) => (
        <section key={group} className="mb-3">
          <div className="py-1 text-xs tracking-wide text-gray-400">{group || '분류 안 됨'}</div>
          {list.map((c) => (
            <label key={c.id} className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-[5px] text-sm hover:bg-hover">
              <input
                type="checkbox"
                className="accent-[#7fa7d9]"
                checked={checked.has(c.id)}
                onChange={(e) =>
                  setChecked((prev) => {
                    const next = new Set(prev)
                    if (e.target.checked) next.add(c.id)
                    else next.delete(c.id)
                    return next
                  })
                }
              />
              <span className="flex-1 text-gray-100">{c.name}</span>
              {c.auto && <span className="text-[11px] text-accent">이 저장소에서 일함</span>}
              {c.elsewhere && <span className="text-[11px] text-gray-500">다른 프로젝트 소속</span>}
            </label>
          ))}
        </section>
      ))}
    </Modal>
  )
}
