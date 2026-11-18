import { useEffect, useState } from 'react'

import { createSession, fetchFolders } from '../api/client'
import { Modal } from '../components/Modal'
import { BUTTON, INPUT, NOTICE, reasonOf } from '../components/styles'
import type { ActionResult, FolderChoice, SessionGroup } from '../types'

const MANUAL = '__manual__'

const baseName = (path: string) => path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() ?? ''

/**
 * [+] 새 세션: 폴더 · 이름 · 그룹을 골라 그 폴더에서 `claude --bg --name <이름>` 을 띄운다.
 *
 * 브라우저의 폴더 선택 창은 실제 경로를 주지 않아 목록에서 고른다 — 프로젝트 탭에 등록된 폴더와
 * Claude 가 이미 신뢰한 폴더. 그 밖은 경로를 직접 적는다. 처음 보는 폴더는 Claude 가 거절하므로
 * (신뢰 승인 전) 서버가 그 안내를 돌려준다.
 */
export function NewSessionModal({
  groups,
  presetGroup,
  onClose,
  onCreated,
}: {
  groups: SessionGroup[]
  presetGroup: string | null
  onClose: () => void
  onCreated: () => void
}) {
  const [folders, setFolders] = useState<FolderChoice[] | null>(null)
  const [choice, setChoice] = useState('')
  const [manual, setManual] = useState('')
  const [name, setName] = useState('')
  const [group, setGroup] = useState(presetGroup ?? '')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    fetchFolders()
      .then((list) => {
        setFolders(list)
        setChoice(list.length ? list[0].path : MANUAL)
      })
      .catch((e) => {
        setMessage(reasonOf(e))
        setFolders([])
        setChoice(MANUAL)
      })
  }, [])

  const cwd = choice === MANUAL ? manual.trim().replace(/^"|"$/g, '') : choice

  async function submit() {
    setBusy(true)
    setMessage(null)
    const body = { cwd, name: name.trim() || baseName(cwd), group: group || null }
    let result: ActionResult = await createSession(body).catch((e) => ({ error: reasonOf(e) }))
    // 같은 이름이 이미 있으면 한 번 묻는다
    if (result.confirm && window.confirm(result.confirm)) {
      result = await createSession({ ...body, force: true }).catch((e) => ({ error: reasonOf(e) }))
    }
    setBusy(false)
    if (result.ok) {
      onCreated()
      onClose()
    } else if (result.error) {
      setMessage(result.error)
    }
  }

  const projects = (folders ?? []).filter((f) => f.project)
  const trusted = (folders ?? []).filter((f) => !f.project)

  return (
    <Modal
      title="새 세션"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON} onClick={onClose}>
            취소
          </button>
          <button
            type="button"
            className={`${BUTTON} border-accent`}
            disabled={busy || !cwd || folders == null}
            onClick={submit}
          >
            {busy ? '만드는 중…' : '만들기'}
          </button>
        </>
      }
    >
      {message && <p className={`mb-3 ${NOTICE}`}>{message}</p>}
      <label className="mb-3 block text-xs text-gray-400">
        폴더
        <select
          className={`${INPUT} mt-1`}
          value={choice}
          disabled={folders == null}
          onChange={(e) => setChoice(e.target.value)}
        >
          {folders == null && <option value="">불러오는 중…</option>}
          {projects.length > 0 && (
            <optgroup label="프로젝트">
              {projects.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.path}
                </option>
              ))}
            </optgroup>
          )}
          {trusted.length > 0 && (
            <optgroup label="Claude 가 신뢰한 폴더">
              {trusted.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.path}
                </option>
              ))}
            </optgroup>
          )}
          <option value={MANUAL}>직접 입력…</option>
        </select>
      </label>
      {choice === MANUAL && (
        <label className="mb-3 block text-xs text-gray-400">
          폴더 경로
          <input
            className={`${INPUT} mt-1`}
            value={manual}
            placeholder="C:\Projects\이름"
            onChange={(e) => setManual(e.target.value)}
          />
          <span className="mt-1 block text-[11px] text-gray-500">
            처음 쓰는 폴더면 그 폴더에서 claude 를 한 번 열어 신뢰를 승인해야 합니다.
          </span>
        </label>
      )}
      <label className="mb-3 block text-xs text-gray-400">
        이름
        <input
          className={`${INPUT} mt-1`}
          value={name}
          placeholder={baseName(cwd) || '세션 이름'}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="block text-xs text-gray-400">
        그룹
        <select className={`${INPUT} mt-1`} value={group} onChange={(e) => setGroup(e.target.value)}>
          {groups.map((g) => (
            <option key={g.name} value={g.name}>
              {g.name}
            </option>
          ))}
          <option value="">분류 안 됨</option>
        </select>
      </label>
    </Modal>
  )
}

/** 그룹 만들기 · 이름 바꾸기: 이름 하나를 받는 창. 실패하면 이유를 창 안에 보인다. */
export function NameModal({
  title,
  initial,
  onClose,
  onSubmit,
}: {
  title: string
  initial: string
  onClose: () => void
  onSubmit: (name: string) => Promise<ActionResult>
}) {
  const [name, setName] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    const result = await onSubmit(name.trim()).catch((e) => ({ error: reasonOf(e) }) as ActionResult)
    setBusy(false)
    if (result.ok) onClose()
    else setMessage(result.error ?? '저장하지 못했습니다.')
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
          <button
            type="button"
            className={`${BUTTON} border-accent`}
            disabled={busy || !name.trim()}
            onClick={submit}
          >
            저장
          </button>
        </>
      }
    >
      {message && <p className={`mb-3 ${NOTICE}`}>{message}</p>}
      <input
        className={INPUT}
        value={name}
        autoFocus
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && name.trim() && !busy && submit()}
      />
    </Modal>
  )
}
