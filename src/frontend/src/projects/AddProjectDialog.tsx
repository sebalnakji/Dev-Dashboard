import { useEffect, useState } from 'react'

import { addProject, fetchCandidates, pickFolder } from '../api/client'
import { Modal } from '../components/Modal'
import { BUTTON, INPUT, NOTICE, reasonOf } from '../components/styles'
import { useAbilities } from '../hooks/useAbilities'
import type { ProjectCandidate, ProjectRow } from '../types'

/**
 * [+ 프로젝트 추가] 창. 세 길로 폴더를 정한다:
 * 세션 목록에서 온 후보를 누르거나, 경로를 적거나, [폴더 찾기]로 이 PC 의 폴더 선택 창을 띄운다.
 * 작업용 복사본 · 하위 폴더를 골라도 원래 저장소가 등록된다 (ADR 0008).
 */
export function AddProjectDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void
  onAdded: (project: ProjectRow) => void
}) {
  const [candidates, setCandidates] = useState<ProjectCandidate[] | null>(null)
  const [root, setRoot] = useState('')
  const [name, setName] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState<'pick' | 'add' | null>(null)
  const { pickFolder: canPick } = useAbilities() // 윈도우가 아니면 [폴더 찾기] 를 숨긴다

  useEffect(() => {
    fetchCandidates()
      .then(setCandidates)
      .catch((e) => {
        // 읽지 못한 것을 '후보 없음'으로 보이지 않게 이유를 띄운다
        setCandidates([])
        setMessage(reasonOf(e))
      })
  }, [])

  async function pick() {
    setBusy('pick')
    setMessage('이 PC 화면에 폴더 선택 창을 띄웠습니다. 폴더를 고르세요.')
    const result = await pickFolder().catch((e) => ({ path: null, error: reasonOf(e) }))
    setBusy(null)
    setMessage(result.error ?? null)
    if (result.path) setRoot(result.path)
  }

  async function add() {
    setBusy('add')
    const result = await addProject(root, name.trim() || undefined).catch((e) => ({ error: reasonOf(e) }))
    setBusy(null)
    if ('key' in result && !result.error) {
      onAdded(result as ProjectRow)
      return
    }
    setMessage(result.error ?? '추가하지 못했습니다')
  }

  return (
    <Modal
      title="프로젝트 추가"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={BUTTON} onClick={onClose}>
            취소
          </button>
          <button type="button" className={`${BUTTON} border-accent`} disabled={!root.trim() || busy != null} onClick={add}>
            추가
          </button>
        </>
      }
    >
      {message && <p className={`mb-3 ${NOTICE}`}>{message}</p>}

      <div className="mb-1 text-xs text-gray-400">세션 목록의 폴더 (아직 등록 안 된 것)</div>
      {candidates == null && <p className="mb-3 text-sm text-gray-500">찾는 중…</p>}
      {candidates?.length === 0 && <p className="mb-3 text-sm text-gray-500">후보가 없습니다.</p>}
      <div className="mb-4">
        {candidates?.map((c) => (
          <button
            key={c.root}
            type="button"
            onClick={() => {
              setRoot(c.root)
              setName('')
            }}
            className={`flex w-full cursor-pointer items-baseline gap-2 rounded-md px-1.5 py-[5px] text-left text-sm ${
              root === c.root ? 'bg-btn' : 'hover:bg-hover'
            }`}
          >
            <span className="text-gray-100">{c.name}</span>
            <span className="min-w-0 flex-1 truncate text-xs text-gray-500">{c.root}</span>
            <span className="shrink-0 text-[11px] text-gray-500">{c.sessions.join(', ')}</span>
          </button>
        ))}
      </div>

      <label className="mb-1 block text-xs text-gray-400" htmlFor="project-root">
        폴더 경로
      </label>
      <div className="mb-3 flex gap-2">
        <input
          id="project-root"
          className={INPUT}
          value={root}
          placeholder={canPick ? 'C:\\Projects\\이름' : '/home/나/projects/이름'}
          onChange={(e) => setRoot(e.target.value)}
        />
        {canPick && (
          <button type="button" className={`${BUTTON} shrink-0`} disabled={busy != null} onClick={pick}>
            {busy === 'pick' ? '고르는 중…' : '폴더 찾기'}
          </button>
        )}
      </div>

      <label className="mb-1 block text-xs text-gray-400" htmlFor="project-name">
        표시 이름 (비우면 폴더 이름)
      </label>
      <input
        id="project-name"
        className={INPUT}
        value={name}
        placeholder={root.split(/[\\/]/).filter(Boolean).pop() ?? ''}
        onChange={(e) => setName(e.target.value)}
      />
    </Modal>
  )
}
