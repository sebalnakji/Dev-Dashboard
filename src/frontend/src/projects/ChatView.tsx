import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { fetchChat, fetchTurnDiff } from '../api/client'
import { BUTTON, WARNING, agoIso, reasonOf } from '../components/styles'
import { useNow } from '../hooks/useNow'
import type { ChatMessage, ChatResponse, ChatWork } from '../types'
import { DiffView, LineCounts } from './GitView'
import { MarkdownView } from './MarkdownView'

/** 처음에 보이는 질문 수, [더 보기]마다 더하는 수 (사용자 결정). */
const PAGE = 10
/** 보는 동안 새 대화를 가져오는 간격. */
const REFRESH_MS = 5000

/**
 * [세션] 보기 오른쪽: 고른 세션의 대화 (T-8). 질문과 그 답을 한 차례로 묶어 위에서 아래로 읽는다 (T-9).
 * 도구 사용(파일 읽기 · 명령 실행 …)은 숨기고 답 머리줄에 횟수만 — 자세한 것은 Claude Code 앱에서 본다.
 */
export function ChatView({
  sessionId,
  projectKey,
  working,
  mdFiles,
  onOpenFile,
  onOpenTask,
  initialTop = null,
}: {
  sessionId: string
  /** 작업 상자에 할 일 번호 · 프로젝트 기준 경로를 붙이려고 함께 묻는다 (T-27) */
  projectKey: string
  /** 세션이 지금 답을 만드는 중이면 맨 아래에 그렇게 보인다 */
  working: boolean
  mdFiles: string[]
  onOpenFile: (path: string) => void
  onOpenTask: (number: number) => void
  /** 뒤로 가기로 돌아왔을 때 보던 스크롤 위치 (T-28). 없으면 맨 아래(최근 대화)부터 */
  initialTop?: number | null
}) {
  const now = useNow()
  const [limit, setLimit] = useState(PAGE)
  const [data, setData] = useState<ChatResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  // 스크롤을 어떻게 맞출지: 처음(맨 아래, 또는 뒤로 가기면 보던 자리) · 새 대화(맨 아래였으면 따라 내려감) ·
  // 더 보기(읽던 자리 유지)
  const pending = useRef<{ kind: 'bottom' | 'keep' | 'top'; height: number } | null>(
    initialTop != null ? { kind: 'top', height: initialTop } : { kind: 'bottom', height: 0 },
  )
  const lastText = useRef('')
  // 요청 번호. [더 보기] 직후 앞서 보낸(적은 limit) 요청이 늦게 오면 목록이 잠깐 줄고 읽던 자리가 튀었다.
  // 가장 나중에 보낸 요청의 응답만 쓴다 (8단계 Fable 검토)
  const seq = useRef(0)

  const load = useCallback(() => {
    const mine = ++seq.current
    fetchChat(sessionId, limit, projectKey)
      .then((next) => {
        if (mine !== seq.current) return
        const text = JSON.stringify(next)
        setError(null)
        if (text === lastText.current) return // 그대로면 다시 그리지 않는다
        lastText.current = text
        const el = box.current?.closest('[data-scroll]') as HTMLElement | null
        if (el && !pending.current) {
          const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
          if (nearBottom) pending.current = { kind: 'bottom', height: 0 }
        }
        setData(next)
      })
      .catch((e) => mine === seq.current && setError(reasonOf(e)))
  }, [sessionId, limit, projectKey])

  useEffect(() => {
    const tick = () => !document.hidden && load()
    const first = window.setTimeout(load, 0)
    const timer = window.setInterval(tick, REFRESH_MS)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(timer)
    }
  }, [load])

  // 그린 뒤에 스크롤을 맞춘다 (그리기 전에 맞추면 높이가 달라 어긋난다)
  useLayoutEffect(() => {
    const el = box.current?.closest('[data-scroll]') as HTMLElement | null
    const want = pending.current
    if (!el || !want || !data) return
    pending.current = null
    if (want.kind === 'bottom') el.scrollTop = el.scrollHeight
    else if (want.kind === 'top') el.scrollTop = want.height // 뒤로 가기: 떠나기 전 보던 자리
    else el.scrollTop += el.scrollHeight - want.height // 위에 붙은 만큼 내려 읽던 자리를 지킨다
  }, [data])

  function more() {
    const el = box.current?.closest('[data-scroll]') as HTMLElement | null
    pending.current = { kind: 'keep', height: el?.scrollHeight ?? 0 }
    setLimit((n) => n + PAGE)
  }

  if (error && !data) return <p className={WARNING}>{error}</p>
  if (!data) return <p className="text-[15px] text-gray-500">불러오는 중…</p>
  if (data.messages.length === 0) {
    return <p className="text-[15px] text-gray-500">대화 기록이 없습니다.</p>
  }

  const turns = toTurns(data.messages)
  return (
    <div ref={box} className="pb-6">
      {data.more && (
        <div className="pb-2 text-center">
          <button type="button" className={BUTTON} onClick={more}>
            이전 대화 {PAGE}개 더 보기
          </button>
        </div>
      )}
      {turns.map((t, i) => (
        // 한 차례(질문 + 답)를 한 덩어리로. 차례 사이는 선과 넓은 간격으로 나눈다
        <section key={i} className={`py-7 ${i > 0 ? 'border-t border-edge' : 'pt-2'}`}>
          {t.ask && <Ask message={t.ask} now={now} />}
          {t.answer && (
            <div className={t.ask ? 'mt-6' : ''}>
              <Who name="Claude" at={t.answer.at} now={now} tools={t.answer.tools} />
              {/* 질문은 파랑 상자, 답은 초록 상자 (사용자 결정) */}
              <div className="mt-2 rounded-md border-l-2 border-on bg-[#262f29] px-4 py-3">
                {t.answer.text ? (
                  <MarkdownView text={t.answer.text} path="" mdFiles={mdFiles} onOpen={onOpenFile} compact />
                ) : (
                  <p className="text-sm text-gray-500">(글 없이 도구만 썼습니다)</p>
                )}
              </div>
            </div>
          )}
          {/* 질문 → 답 → 작업: 답을 읽고 나서 무엇을 고쳤는지 본다 (작업 상자는 보라, 사용자 결정 T-27 · T-28) */}
          {t.answer?.work && (
            <div className="mt-6">
              <WorkBox projectKey={projectKey} shellTracked={data.shellTracked !== false} work={t.answer.work} onOpenFile={onOpenFile} onOpenTask={onOpenTask} />
            </div>
          )}
          {working && i === turns.length - 1 && (
            <p className="mt-5 flex items-center gap-2 text-sm text-accent">
              <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
              답변을 만드는 중…
            </p>
          )}
        </section>
      ))}
      {error && <p className={WARNING}>{error}</p>}
    </div>
  )
}

interface Turn {
  ask?: ChatMessage
  answer?: ChatMessage
}

/** 질문과 그 뒤의 답을 한 차례로 묶는다. 첫 질문 앞에 남은 답은 질문 없는 차례가 된다. */
function toTurns(messages: ChatMessage[]): Turn[] {
  const turns: Turn[] = []
  for (const m of messages) {
    const last = turns.at(-1)
    if (m.role === 'user') turns.push({ ask: m })
    else if (last && !last.answer) last.answer = m
    else turns.push({ answer: m })
  }
  return turns
}

/**
 * 그 차례에 고친 파일들 (답 아래, 보라 상자). 할 일 번호를 누르면 [작업] 보기의 그 문서로, 파일을 누르면
 * 상자 안에 그 차례가 바꾼 내용(처음 고치기 직전 → 마지막으로 고친 직후)이 좌우 비교로 펼쳐진다.
 * 다시 누르면 접힌다. 비교는 훅이 남긴 스냅샷이 원본 — 이 기능 전의 옛 차례는 파일 이름만 보인다.
 */
function WorkBox({
  projectKey,
  shellTracked,
  work,
  onOpenFile,
  onOpenTask,
}: {
  projectKey: string
  /** false 면 git 이 아닌 폴더 — 셸 명령으로 고친 파일은 빠질 수 있다고 알린다 */
  shellTracked: boolean
  work: ChatWork
  onOpenFile: (path: string) => void
  onOpenTask: (number: number) => void
}) {
  const [chosen, setChosen] = useState<string | null>(null)
  const shown = work.files.find((f) => f.path === chosen)
  const prompt = work.prompt
  // 고정해서 넘긴다 (DiffView 는 memo — 5초마다 대화를 다시 받아도 펼친 비교를 다시 그리지 않게)
  const load = useMemo(
    () => (chosen ? (full: boolean) => fetchTurnDiff(projectKey, prompt, chosen, full) : null),
    [projectKey, prompt, chosen],
  )
  return (
    <div>
      <div className="flex items-baseline gap-2 text-xs">
        <span className="font-semibold text-violet-300">작업</span>
        <span className="text-gray-600">파일 {work.files.length}개</span>
        {!shellTracked && (
          <span className="text-gray-600" title="셸 명령 전후의 git 상태를 견줘 잡는데, 이 폴더는 git 저장소가 아니다">
            · git 이 아닌 폴더라 셸 명령으로 고친 파일은 빠질 수 있음
          </span>
        )}
      </div>
      <div className="mt-2 rounded-md border-l-2 border-violet-400 bg-[#2b2838] px-4 py-3">
        {work.task ? (
          <button
            type="button"
            onClick={() => onOpenTask(work.task!.number)}
            className="mb-2 flex cursor-pointer items-baseline gap-2 text-left text-[15px] font-semibold text-gray-100 hover:text-violet-300"
          >
            <span className="tabular-nums font-normal text-gray-400">T-{work.task.number}</span>
            <span>{work.task.title || '(제목 없음)'}</span>
            <span className="text-gray-500">›</span>
          </button>
        ) : (
          <p className="mb-2 text-sm text-gray-500">할 일 기록 없이 고친 변경</p>
        )}
        {work.files.map((f) => (
          <button
            key={f.path}
            type="button"
            // 스냅샷이 없는 옛 차례는 비교 대신 [파일] 보기로 연다
            onClick={() => (f.diff ? setChosen(chosen === f.path ? null : f.path) : onOpenFile(f.path))}
            title={f.diff ? undefined : '이 차례의 스냅샷이 없어 파일을 엽니다'}
            className={`group flex w-full cursor-pointer items-center gap-2 rounded-md px-1 text-left ${
              f.agent ? 'py-0.5 text-xs' : 'py-1 text-sm'
            } ${chosen === f.path ? 'bg-btn' : 'hover:bg-hover'}`}
          >
            <span className="w-4 shrink-0 text-center text-xs text-violet-300" title={f.op === 'create' ? '새로 만듦' : '고침'}>
              {f.op === 'create' ? '+' : '~'}
            </span>
            <span
              className={`min-w-0 flex-1 truncate group-hover:text-violet-300 ${f.agent ? 'text-gray-400' : 'text-gray-200'}`}
            >
              {f.path}
            </span>
            {f.agent && (
              <span className="shrink-0 text-[11px] text-gray-500" title="하위 작업(서브에이전트)이 고쳤다">
                하위 작업
              </span>
            )}
            {f.shell && (
              <span className="shrink-0 text-[11px] text-gray-500" title="셸 명령으로 고쳤다">
                셸
              </span>
            )}
            {f.diff && <LineCounts added={f.added} deleted={f.deleted} />}
          </button>
        ))}
        {shown && load && (
          <div className="mt-3">
            <DiffView
              key={shown.path}
              path={shown.path}
              load={load}
              note={
                <span className="flex items-center gap-2">
                  <span>이 차례의 변경</span>
                  <button type="button" onClick={() => onOpenFile(shown.path)} className="cursor-pointer text-accent hover:underline">
                    파일 열기
                  </button>
                </span>
              }
            />
          </div>
        )}
      </div>
    </div>
  )
}

/** 이보다 긴 질문(붙여 넣은 로그 등)은 접어 두고 [펼치기]로 연다. */
const ASK_LINES = 12
const ASK_CHARS = 1200

function Ask({ message, now }: { message: ChatMessage; now: number }) {
  const [open, setOpen] = useState(false)
  const long = message.text.split('\n').length > ASK_LINES || message.text.length > ASK_CHARS
  return (
    <div>
      <Who name="나" at={message.at} now={now} />
      <div className="mt-2 rounded-md border-l-2 border-accent bg-[#2a2f36] px-4 py-3">
        <div
          className={`text-[15px] leading-[1.7] whitespace-pre-wrap text-gray-100 ${long && !open ? 'line-clamp-[8]' : ''}`}
        >
          {message.text}
        </div>
        {long && (
          <button type="button" onClick={() => setOpen(!open)} className="mt-1.5 cursor-pointer text-xs text-accent hover:underline">
            {open ? '접기' : '펼치기'}
          </button>
        )}
      </div>
    </div>
  )
}

/** 말한 쪽 머리줄: "Claude  15:12  · 도구 10번". 시각에 마우스를 올리면 "3분 전" */
function Who({ name, at, now, tools }: { name: string; at: string | null; now: number; tools?: Record<string, number> }) {
  const entries = Object.entries(tools ?? {}).sort((a, b) => b[1] - a[1])
  const total = entries.reduce((sum, [, n]) => sum + n, 0)
  return (
    <div className="flex items-baseline gap-2 text-xs">
      <span className={`font-semibold ${name === '나' ? 'text-accent' : 'text-on'}`}>{name}</span>
      {at && (
        <span title={agoIso(at, now)} className="text-gray-600">
          {clock(at, now)}
        </span>
      )}
      {total > 0 && (
        <span title={entries.map(([k, n]) => `${k} ${n}`).join(' · ')} className="text-gray-600">
          · 도구 {total}번
        </span>
      )}
    </div>
  )
}

/** 오늘이면 "15:12", 아니면 "11/12 15:12" */
function clock(iso: string, now: number): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return d.toDateString() === new Date(now).toDateString() ? time : `${d.getMonth() + 1}/${d.getDate()} ${time}`
}
