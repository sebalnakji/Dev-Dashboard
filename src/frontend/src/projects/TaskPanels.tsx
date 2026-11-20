import { useEffect, useMemo, useRef, useState } from 'react'

import { fetchTaskDiff } from '../api/client'
import { WARNING } from '../components/styles'
import type { TaskDetail, TaskFileDiff, TaskRow, TaskStatus } from '../types'
import { DiffView } from './GitView'

// 탭이 '작업'이라 안에서는 상태만 짧게 (사용자 결정). 문서 섹션의 '한 일'과 겹치지 않게 '완료'
const STATUS_LABEL: Record<TaskStatus, string> = { doing: '진행 중', todo: '대기', done: '완료' }
const STATUS_ORDER: TaskStatus[] = ['doing', 'todo', 'done']
const STATUS_COLOR: Record<TaskStatus, string> = {
  doing: 'border-accent text-accent',
  todo: 'border-edge text-gray-300',
  done: 'border-edge text-gray-500',
}

/**
 * 가운데 아래: 할 일 (진행 중 · 할 일 · 한 일). 누르면 위 칸에 그 정리 문서가 열린다.
 * 고른 것은 파일 이름으로 가린다 — 같은 번호가 둘이어도 각각 열 수 있게.
 */
export function TaskList({
  tasks,
  selected,
  onSelect,
}: {
  tasks: TaskRow[]
  /** 고른 할 일의 파일 이름 */
  selected: string | null
  onSelect: (file: string) => void
}) {
  if (tasks.length === 0) {
    return (
      <p className="px-1.5 text-sm text-gray-500">
        아직 작업이 없습니다.
      </p>
    )
  }
  const other = tasks.filter((t) => !STATUS_ORDER.includes(t.status as TaskStatus))
  return (
    <div>
      {STATUS_ORDER.map((status) => {
        const list = tasks.filter((t) => t.status === status)
        // 한 일은 최근 것이 위로
        if (status === 'done') list.reverse()
        if (list.length === 0) return null
        return (
          <section key={status} className="mb-3">
            <div className="px-1.5 py-1 text-xs tracking-wide text-gray-400">
              {STATUS_LABEL[status]} <span className="text-gray-500">{list.length}</span>
            </div>
            {list.map((t) => (
              <TaskLine key={t.file} task={t} selected={t.file === selected} onSelect={onSelect} />
            ))}
          </section>
        )
      })}
      {other.length > 0 && (
        <section className="mb-3">
          <div className="px-1.5 py-1 text-xs text-gray-400">상태를 읽을 수 없음</div>
          {other.map((t) => (
            <TaskLine key={t.file} task={t} selected={t.file === selected} onSelect={onSelect} />
          ))}
        </section>
      )}
    </div>
  )
}

function TaskLine({
  task,
  selected,
  onSelect,
}: {
  task: TaskRow
  selected: boolean
  onSelect: (file: string) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(task.file)}
      title={task.problems.length ? task.problems.join('\n') : undefined}
      className={`flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-left text-[15px] ${
        selected ? 'bg-btn' : 'hover:bg-hover'
      }`}
    >
      <span className="w-11 shrink-0 tabular-nums text-gray-500">T-{task.number}</span>
      <span className={`min-w-0 flex-1 truncate ${task.status === 'done' ? 'text-gray-400' : 'text-gray-100'}`}>
        {task.title}
      </span>
      {task.problems.length > 0 && <span className="shrink-0 text-xs text-danger">⚠</span>}
      {task.session && <span className="shrink-0 text-[11px] text-gray-500">{task.session}</span>}
    </button>
  )
}

/** 템플릿의 안내 주석(<!-- -->)은 읽는 사람에게 필요 없어 뺀다. */
const stripComments = (text: string) => text.replace(/<!--[\s\S]*?(-->|$)/g, '').trim()

/**
 * 항목 본문을 그린다. '- ' 로 시작하는 줄은 실제 목록으로, 들여 쓴 다음 줄은 그 항목에 잇는다.
 * 그대로 한 덩어리 글로 두면 '- 2026-11-13 …' 줄들이 붙어 보여 어디서 끊기는지 알기 어려웠다.
 * 마크다운 전체를 그리지는 않는다 — 기록 규칙이 쓰는 것은 문장과 '- ' 목록뿐이다.
 */
function Prose({ text }: { text: string }) {
  type Block = { list: boolean; lines: string[] }
  const blocks: Block[] = []
  for (const raw of text.split('\n')) {
    const item = /^\s*[-*]\s+(.*)$/.exec(raw)
    const last = blocks.at(-1)
    if (item) {
      if (last?.list) last.lines.push(item[1])
      else blocks.push({ list: true, lines: [item[1]] })
    } else if (last?.list && /^\s+\S/.test(raw)) {
      last.lines[last.lines.length - 1] += ` ${raw.trim()}`
    } else if (raw.trim() === '') {
      if (last && !last.list) last.lines.push('')
    } else if (last && !last.list) {
      last.lines.push(raw)
    } else {
      blocks.push({ list: false, lines: [raw] })
    }
  }
  return (
    <div className="space-y-2 text-[15px] leading-relaxed text-gray-100">
      {blocks.map((b, i) =>
        b.list ? (
          <ul key={i} className="list-disc space-y-1.5 pl-5 marker:text-gray-600">
            {b.lines.map((line, j) => (
              <li key={j}>{line}</li>
            ))}
          </ul>
        ) : (
          <p key={i} className="whitespace-pre-wrap">
            {b.lines.join('\n').trim()}
          </p>
        ),
      )}
    </div>
  )
}

/** 정리 문서의 항목 제목: 본문보다 작지만 또렷하게 (흐린 작은 글자면 구조가 안 잡혔다). */
const SECTION_TITLE = 'mb-1.5 text-[13px] font-semibold text-gray-300'

/**
 * 고른 작업의 정리 문서. 목표 · 한 일 · 남은 일과 이 작업에서 바뀐 파일.
 * 바뀐 파일을 누르면 그 아래에 이 작업이 그 파일을 어떻게 바꿨는지 좌우 비교가 열린다.
 * 비교할 커밋이 없으면(커밋에 [T-n] 이 없음) 전처럼 [파일] 보기로 넘어가 그 파일을 연다.
 */
export function TaskDoc({
  projectKey,
  doc,
  onOpenFile,
}: {
  projectKey: string
  doc: TaskDetail
  onOpenFile: (path: string) => void
}) {
  const status = doc.status as TaskStatus
  const [chosen, setChosen] = useState<{ path: string; first: TaskFileDiff } | null>(null)
  const diffBox = useRef<HTMLDivElement>(null)

  async function pick(path: string) {
    if (chosen?.path === path) return setChosen(null) // 다시 누르면 닫는다
    const first = await fetchTaskDiff(projectKey, doc.number, path, false).catch(() => null)
    if (!first || (!first.snapshot && first.commits.length === 0 && !first.uncommitted)) return onOpenFile(path)
    setChosen({ path, first })
  }

  useEffect(() => {
    if (chosen) diffBox.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [chosen])

  // 처음 받은 비교를 다시 쓰고, [전체 보기]일 때만 다시 묻는다. 고정해서 넘긴다 (DiffView 는 memo)
  const load = useMemo(
    () =>
      chosen
        ? (full: boolean) =>
            full ? fetchTaskDiff(projectKey, doc.number, chosen.path, true) : Promise.resolve(chosen.first)
        : null,
    [chosen, projectKey, doc.number],
  )
  return (
    <article>
      <header className="mb-4 px-1.5">
        <div className="flex items-baseline gap-2">
          <span className="tabular-nums text-gray-500">T-{doc.number}</span>
          <h3 className="min-w-0 flex-1 text-lg font-semibold text-gray-100">{doc.title}</h3>
          {STATUS_LABEL[status] && (
            <span className={`shrink-0 rounded-[5px] border px-2 py-px text-xs ${STATUS_COLOR[status]}`}>
              {STATUS_LABEL[status]}
            </span>
          )}
        </div>
        <div className="mt-1 text-xs text-gray-500">
          {doc.session && `${doc.session} · `}
          {doc.created && `만든 날 ${doc.created} · `}
          {doc.file}
        </div>
      </header>
      {doc.problems.length > 0 && <p className={`mb-4 ${WARNING}`}>{doc.problems.join('\n')}</p>}
      {(['목표', '한 일', '남은 일'] as const).map((name) => {
        const text = stripComments(doc.sections[name])
        return (
          <section key={name} className="mb-5 px-1.5">
            <h4 className={SECTION_TITLE}>{name}</h4>
            {text ? <Prose text={text} /> : <p className="text-sm text-gray-600">비어 있음</p>}
          </section>
        )
      })}
      <section className="px-1.5">
        <h4 className={SECTION_TITLE}>
          이 작업에서 바뀐 파일 <span className="font-normal text-gray-500">{doc.files.length}</span>
        </h4>
        {doc.files.length === 0 ? (
          <p className="text-sm text-gray-600">기록 없음</p>
        ) : (
          doc.files.map((f) => (
            <button
              key={f.path}
              type="button"
              onClick={() => void pick(f.path)}
              className={`group flex w-full cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-left text-sm ${
                chosen?.path === f.path ? 'bg-btn' : 'hover:bg-hover'
              }`}
            >
              <span className="w-4 shrink-0 text-center text-xs text-on" title={f.created ? '이 작업에서 만듦' : undefined}>
                {f.created ? '+' : ''}
              </span>
              <span className="min-w-0 flex-1 truncate text-gray-200 group-hover:text-accent">{f.path}</span>
              <span className="shrink-0 text-xs tabular-nums text-gray-500">{f.changes}번</span>
            </button>
          ))
        )}
        <div ref={diffBox}>
          {chosen && load && (
            <DiffView
              key={chosen.path}
              path={chosen.path}
              load={load}
              note={
                <span className="flex items-center gap-2">
                  {chosen.first.snapshot ? (
                    <span title="훅이 남긴 스냅샷으로 비교합니다 (커밋과 상관없이)">
                      {chosen.first.beforeUnknown
                        ? '고치기 직전 내용을 알 수 없어 지금 내용만'
                        : '이 작업이 고치기 직전 → 마지막으로 고친 직후'}
                    </span>
                  ) : (
                    // 무엇을 합친 비교인지 — 사이에 다른 작업의 커밋이 끼었으면 그 변경도 섞인다
                    <span title={chosen.first.commits.join(', ')}>
                      {[
                        chosen.first.commits.length > 0 && `커밋 ${chosen.first.commits.length}개`,
                        chosen.first.uncommitted && '커밋 안 된 변경',
                      ]
                        .filter(Boolean)
                        .join(' + ')}
                    </span>
                  )}
                  <button type="button" onClick={() => onOpenFile(chosen.path)} className="cursor-pointer text-accent hover:underline">
                    파일 열기
                  </button>
                </span>
              }
            />
          )}
        </div>
      </section>
    </article>
  )
}
