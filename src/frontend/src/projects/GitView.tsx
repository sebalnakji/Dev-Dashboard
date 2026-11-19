import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { ThemedToken } from 'shiki'

import { fetchCommit, fetchCommitDiff, fetchCommitSummary, makeCommitSummary } from '../api/client'
import { BUTTON, WARNING, agoIso, reasonOf } from '../components/styles'
import { useNow } from '../hooks/useNow'
import type { CommitDetail, CommitFile, DiffHunk, FileDiff, GitHistory } from '../types'
import { MAX_HIGHLIGHT_LINES, tokenize } from './highlight'
import { langOfPath } from './lang'
import { MarkdownView } from './MarkdownView'
import { gitSeen } from './remember'

/**
 * [git] 보기 (T-10).
 *
 *   왼쪽   최근 커밋 (동료가 올리고 아직 받지 않은 커밋 포함) · 커밋 안 된 변경 · [갱신]
 *   오른쪽 고른 커밋의 제목 · 본문 · 바뀐 파일 → 파일을 누르면 아래에 이전(왼쪽) · 이후(오른쪽) 비교
 *
 * 비교는 git 이 계산한 변경 덩어리를 그대로 펼친다 (gitdiff.py). 바뀌지 않은 곳은 "변경 없는 줄 N개"로
 * 접고, [전체 보기]를 누르면 파일 전체를 보인다.
 */

/** 새 커밋 표시('+3' · 점)의 빨강. 세션 목록의 새 답과 같은 색 */
const FRESH = '#f06368'

const CHANGE_LABEL: Record<string, string> = { M: '수정', A: '추가', D: '삭제', R: '이름', '??': '새 파일' }

const FILE_STATUS: Record<string, { label: string; color: string }> = {
  A: { label: '추가', color: 'text-on' },
  M: { label: '수정', color: 'text-accent' },
  D: { label: '삭제', color: 'text-danger' },
  R: { label: '이름', color: 'text-gray-400' },
  C: { label: '복사', color: 'text-gray-400' },
}

/** 제목 끝의 [T-12] 는 버튼으로 따로 보이므로 뺀다. 그러고 나서 비면(제목이 [T-2] 뿐) 원문을 둔다. */
const withoutRefs = (subject: string) => subject.replace(/\s*(\[T-\d+\]\s*)+$/, '') || subject

/**
 * 왼쪽: 최근 커밋. 누르면 오른쪽에 그 커밋이 열린다.
 * 이 보기를 연 순간에 본 적 없던 커밋에는 빨간 점 — 보기를 열면 [git] 옆 숫자는 바로 사라지므로
 * 무엇이 새것이었는지는 여기서 보인다.
 */
export function GitList({
  projectKey,
  git,
  selected,
  onSelect,
  onRefresh,
}: {
  projectKey: string
  git: GitHistory
  selected: string | null
  onSelect: (hash: string) => void
  onRefresh: () => Promise<void>
}) {
  const now = useNow()
  // 이 보기를 열 때의 '본 커밋'. 연 뒤에는 ProjectView 가 지금 목록으로 바꿔 적는다
  const [before] = useState(() => gitSeen(projectKey))
  const [busy, setBusy] = useState(false)

  if (!git.git) {
    return <p className="px-1.5 text-sm text-gray-500">깃 저장소가 아니라 이력이 없습니다.</p>
  }

  async function refresh() {
    setBusy(true)
    await onRefresh()
    setBusy(false)
  }

  return (
    <div>
      {git.fetch && (
        <div className="mb-3 flex items-center justify-between gap-2 px-1.5">
          <span className="min-w-0 truncate text-xs text-gray-500" title={git.upstream ?? undefined}>
            {git.fetch.at ? `원격 확인 ${agoIso(git.fetch.at, now)}` : '원격 확인 전'}
          </span>
          <button type="button" className={BUTTON} disabled={busy} onClick={refresh}>
            {busy ? '갱신 중…' : '갱신'}
          </button>
        </div>
      )}
      {git.fetch?.error && <p className={`mb-3 ${WARNING}`}>원격에서 가져오지 못했습니다: {git.fetch.error}</p>}

      {git.changes.length > 0 && (
        <section className="mb-4">
          <div className="px-1.5 py-1 text-xs tracking-wide text-gray-400">
            커밋 안 된 변경 <span className="text-gray-500">{git.changes.length}</span>
          </div>
          {git.changes.map((c) => (
            <div key={c.path} className="flex items-center gap-2 rounded-md px-1.5 py-0.5 text-sm">
              <span className="w-12 shrink-0 text-xs text-gray-500">
                {CHANGE_LABEL[c.status[0]] ?? CHANGE_LABEL[c.status] ?? c.status}
              </span>
              <span className="min-w-0 flex-1 truncate text-gray-300" title={c.path}>
                {c.path}
              </span>
            </div>
          ))}
        </section>
      )}

      <div className="px-1.5 py-1 text-xs tracking-wide text-gray-400">최근 커밋</div>
      {git.commits.length === 0 && <p className="px-1.5 text-sm text-gray-600">아직 커밋이 없습니다</p>}
      <div className="space-y-0.5">
        {git.commits.map((c) => {
          const fresh = before != null && !before.has(c.hash)
          return (
            <button
              key={c.hash}
              type="button"
              onClick={() => onSelect(c.hash)}
              className={`block w-full cursor-pointer rounded-md px-2.5 py-2 text-left ${
                c.hash === selected ? 'bg-btn' : 'hover:bg-hover'
              }`}
            >
              <span className="flex items-start gap-2">
                <span
                  title={fresh ? '새 커밋' : undefined}
                  className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: fresh ? FRESH : 'transparent' }}
                />
                <span className="line-clamp-2 min-w-0 flex-1 text-[14px] break-words text-gray-100">
                  {withoutRefs(c.subject)}
                </span>
              </span>
              <span className="mt-1 ml-3.5 flex flex-wrap items-center gap-x-1.5 text-xs text-gray-500">
                <span className="text-gray-400">{c.author}</span>
                <span>· {agoIso(c.at, now)}</span>
                {c.incoming && (
                  <span title="원격에만 있고 아직 pull 하지 않은 커밋입니다" className="text-accent">
                    · 아직 안 받음
                  </span>
                )}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

/** 오른쪽: 고른 커밋의 제목 · 작성자 · 본문 · 바뀐 파일. 파일을 누르면 그 아래에 좌우 비교가 열린다. */
export function CommitView({
  projectKey,
  hash,
  known,
  onSelectTask,
}: {
  projectKey: string
  hash: string
  /** 지금 있는 할 일 번호들 */
  known: Set<number>
  onSelectTask: (n: number) => void
}) {
  const now = useNow()
  const [detail, setDetail] = useState<CommitDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [chosen, setChosen] = useState<CommitFile | null>(null)
  const diffBox = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    fetchCommit(projectKey, hash)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError(reasonOf(e)))
    return () => {
      alive = false
    }
  }, [projectKey, hash])

  // 파일을 고르면 비교가 보이는 자리로 내려간다 (파일 목록이 길면 비교가 화면 밖에 열린다)
  useEffect(() => {
    if (chosen) diffBox.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [chosen])

  // 고른 파일의 비교를 가져오는 곳 — 고정해서 넘겨야 DiffView 가 매번 다시 가져오지 않는다
  const loadDiff = useMemo(
    () =>
      chosen && detail
        ? (full: boolean) => fetchCommitDiff(projectKey, detail.hash, chosen.path, chosen.old, full)
        : null,
    [chosen, detail, projectKey],
  )

  if (error) return <p className={WARNING}>{error}</p>
  if (!detail) return <p className="text-[15px] text-gray-500">불러오는 중…</p>

  return (
    <article>
      <h3 className="text-lg font-semibold break-words text-gray-100">{withoutRefs(detail.subject)}</h3>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
        <span className="text-gray-300">{detail.author}</span>
        <span title={new Date(detail.at).toLocaleString()}>{agoIso(detail.at, now)}</span>
        <span className="font-mono">{detail.hash.slice(0, 7)}</span>
        {[...new Set(detail.tasks)].map((n) => (
          <button
            key={n}
            type="button"
            disabled={!known.has(n)}
            title={known.has(n) ? undefined : '지금은 없는 작업입니다'}
            onClick={() => onSelectTask(n)}
            className="cursor-pointer rounded border border-edge px-1 text-accent hover:border-accent disabled:cursor-default disabled:text-gray-600 disabled:hover:border-edge"
          >
            T-{n}
          </button>
        ))}
      </div>
      <SummaryBox projectKey={projectKey} hash={detail.hash} />
      {/* 본문은 요약과 겹치므로 접어 둔다 */}
      {detail.body && (
        <details className="mt-3 text-[13px] text-gray-500">
          <summary className="cursor-pointer select-none hover:text-gray-300">커밋 메시지 본문</summary>
          <p className="mt-2 leading-relaxed whitespace-pre-wrap text-gray-300">{detail.body}</p>
        </details>
      )}

      <h4 className="mt-6 mb-2 text-[13px] font-semibold text-gray-300">
        바뀐 파일 <span className="font-normal text-gray-500">{detail.files.length}</span>
      </h4>
      <div className="space-y-px">
        {detail.files.map((f) => {
          const st = FILE_STATUS[f.status] ?? { label: f.status, color: 'text-gray-400' }
          const on = chosen?.path === f.path
          return (
            <button
              key={f.path}
              type="button"
              onClick={() => setChosen(f)}
              className={`flex w-full cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 text-left text-sm ${
                on ? 'bg-btn' : 'hover:bg-hover'
              }`}
            >
              <span className={`w-8 shrink-0 text-xs ${st.color}`}>{st.label}</span>
              <span className="min-w-0 flex-1 truncate text-gray-200" title={f.old ? `${f.old} → ${f.path}` : f.path}>
                {f.old && <span className="text-gray-500">{f.old} → </span>}
                {f.path}
              </span>
              <LineCounts added={f.added} deleted={f.deleted} />
            </button>
          )
        })}
      </div>

      <div ref={diffBox} className="scroll-mt-4">
        {chosen && loadDiff && <DiffView key={chosen.path} path={chosen.path} load={loadDiff} />}
      </div>
    </article>
  )
}

/**
 * AI 요약 상자 (T-11). 저장된 요약이 있으면 바로 보이고, 없으면 [AI 요약 만들기]를 누를 때만 만든다
 * (사용자 결정 — 보기를 열 때 자동으로 골라지는 커밋까지 Sonnet 을 부르지 않게).
 * 만드는 중에 다른 커밋으로 옮겨도 서버는 끝까지 만들어 저장한다 — 다시 열면 바로 보인다.
 */
function SummaryBox({ projectKey, hash }: { projectKey: string; hash: string }) {
  const [state, setState] = useState<{
    step: 'loading' | 'none' | 'making' | 'done' | 'error'
    text?: string
    error?: string
  }>({ step: 'loading' })
  const alive = useRef(true)

  const make = useCallback(() => {
    setState({ step: 'making' })
    makeCommitSummary(projectKey, hash).then((r) => {
      if (!alive.current) return
      if (r.error || !r.text) setState({ step: 'error', error: r.error ?? '요약이 비어 있습니다' })
      else setState({ step: 'done', text: r.text })
    }, (e: unknown) => alive.current && setState({ step: 'error', error: reasonOf(e) }))
  }, [projectKey, hash])

  useEffect(() => {
    alive.current = true
    fetchCommitSummary(projectKey, hash).then(
      (s) => {
        if (!alive.current) return
        setState(s.text ? { step: 'done', text: s.text } : { step: 'none' })
      },
      (e: unknown) => alive.current && setState({ step: 'error', error: reasonOf(e) }),
    )
    return () => {
      alive.current = false
    }
  }, [projectKey, hash])

  // 아직 없으면 상자 대신 버튼 하나만 (자리를 덜 차지하게)
  if (state.step === 'none') {
    return (
      <div className="mt-4">
        <button type="button" className={BUTTON} onClick={make}>
          AI 요약 만들기
        </button>
      </div>
    )
  }
  return (
    <section className="mt-4 rounded-md border border-edge bg-panel px-4 py-3">
      <div className="mb-1.5 text-xs font-semibold text-gray-400">AI 요약</div>
      {state.step === 'loading' && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {state.step === 'making' && (
        <p className="flex items-center gap-2 text-sm text-accent">
          <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
          요약을 만드는 중…
        </p>
      )}
      {state.step === 'error' && (
        <div className="flex items-start gap-3">
          <p className={`min-w-0 flex-1 ${WARNING}`}>{state.error}</p>
          <button type="button" className={BUTTON} onClick={make}>
            다시 시도
          </button>
        </div>
      )}
      {state.step === 'done' && state.text && (
        <MarkdownView text={state.text} path="" mdFiles={NO_FILES} onOpen={NO_OPEN} compact />
      )}
    </section>
  )
}

// 요약 안의 링크는 열 곳이 없다. 렌더마다 새 값을 넘기면 MarkdownView 가 매초 다시 그린다
const NO_FILES: string[] = []
const NO_OPEN = () => {}

/** "+12 −3". 글이 아닌 파일은 줄 수가 없다 */
export function LineCounts({ added, deleted }: { added: number | null; deleted: number | null }) {
  if (added == null || deleted == null) return <span className="shrink-0 text-xs text-gray-600">글 아님</span>
  return (
    <span className="shrink-0 text-xs tabular-nums">
      <span className="text-on">+{added}</span> <span className="text-danger">−{deleted}</span>
    </span>
  )
}

// --- 좌우 비교 ---

type Kind = ' ' | '-' | '+'
/** 한 쪽 칸: 줄 번호 · 글 · 종류, 그리고 그쪽 글 묶음에서의 순번 (색 조각을 찾을 때 쓴다) */
type Side = { no: number; text: string; kind: Kind; at: number }
type Row = { gap: number } | { left: Side | null; right: Side | null }

/**
 * 변경 덩어리를 표의 줄로. 지운 줄 묶음과 바로 뒤의 더한 줄 묶음은 한 줄씩 짝지어 나란히 놓고
 * (고친 줄은 이전 · 이후가 마주 보게), 남는 쪽은 빈 칸으로 둔다. 덩어리 사이의 건너뛴 줄 수는 gap.
 */
function toRows(hunks: DiffHunk[]): { rows: Row[]; left: string[]; right: string[] } {
  const rows: Row[] = []
  const left: string[] = []
  const right: string[] = []
  let nextOld = 1
  const side = (list: string[], no: number, text: string, kind: Kind): Side => {
    list.push(text)
    return { no, text, kind, at: list.length - 1 }
  }
  for (const h of hunks) {
    // 줄 수가 0 인 쪽(새 파일의 이전 등)은 시작 번호가 '그 앞 줄'이다
    let o = h.oldLines === 0 ? h.oldStart + 1 : h.oldStart
    let n = h.newLines === 0 ? h.newStart + 1 : h.newStart
    if (o > nextOld) rows.push({ gap: o - nextOld })
    let i = 0
    while (i < h.lines.length) {
      const [kind, text] = h.lines[i]
      if (kind === ' ') {
        rows.push({ left: side(left, o++, text, ' '), right: side(right, n++, text, ' ') })
        i++
        continue
      }
      const dels: string[] = []
      const adds: string[] = []
      while (i < h.lines.length && h.lines[i][0] === '-') dels.push(h.lines[i++][1])
      while (i < h.lines.length && h.lines[i][0] === '+') adds.push(h.lines[i++][1])
      for (let k = 0; k < Math.max(dels.length, adds.length); k++) {
        rows.push({
          left: k < dels.length ? side(left, o++, dels[k], '-') : null,
          right: k < adds.length ? side(right, n++, adds[k], '+') : null,
        })
      }
    }
    nextOld = o
  }
  return { rows, left, right }
}

/** 한 쪽 글 묶음의 색 조각. 아직 못 칠했거나 너무 길면 null (글자만 보인다) */
function useTokens(lines: string[], lang: string): ThemedToken[][] | null {
  const code = lines.join('\n')
  const count = lines.length
  const [result, setResult] = useState<{ code: string; lang: string; tokens: ThemedToken[][] } | null>(null)
  useEffect(() => {
    if (count === 0 || count > MAX_HIGHLIGHT_LINES) return
    let alive = true
    tokenize(code, lang)
      .then((tokens) => alive && setResult({ code, lang, tokens }))
      .catch(() => {}) // 칠하지 못하면 글자 그대로
    return () => {
      alive = false
    }
  }, [code, lang, count])
  return result && result.code === code && result.lang === lang ? result.tokens : null
}

const SIDE_BG: Record<Kind, string> = { ' ': '', '-': 'bg-[#3d2427]', '+': 'bg-[#1e3627]' }
const NUM_BG: Record<Kind, string> = { ' ': '', '-': 'bg-[#4a2a2e]', '+': 'bg-[#22412d]' }
const EMPTY_BG = 'bg-[#191918]'

/**
 * 파일 하나의 이전(왼쪽) · 이후(오른쪽). [전체 보기]는 바뀌지 않은 줄까지 파일 전체를 보인다.
 * memo: 커밋 상세가 '3분 전'을 흘리려고 1초마다 다시 그려도 수천 줄 표는 그대로 둔다.
 */
export const DiffView = memo(function DiffView({
  path,
  load,
  note,
}: {
  path: string
  /** 비교를 가져오는 곳 — 커밋 하나(깃 보기) 또는 작업 하나(작업 문서, T-19). 바뀌지 않게 고정해서 넘긴다 */
  load: (full: boolean) => Promise<FileDiff>
  /** 머리줄 아래 한 줄 (무엇을 비교하는지 · [파일 열기] 등) */
  note?: ReactNode
}) {
  const [full, setFull] = useState(false)
  const [loaded, setLoaded] = useState<{ full: boolean; diff?: FileDiff; error?: string } | null>(null)

  useEffect(() => {
    let alive = true
    load(full)
      .then((diff) => alive && setLoaded({ full, diff }))
      .catch((e) => alive && setLoaded({ full, error: reasonOf(e) }))
    return () => {
      alive = false
    }
  }, [load, full])

  const shown = loaded?.full === full ? loaded : null
  const { rows, left, right } = useMemo(() => toRows(shown?.diff?.hunks ?? []), [shown])
  const lang = langOfPath(path)
  const leftTokens = useTokens(left, lang)
  const rightTokens = useTokens(right, lang)

  const diff = shown?.diff
  // +N −M 은 받은 변경 덩어리에서 센다 ([전체 보기]여도 바뀐 줄 수는 같다)
  const lines = diff?.hunks.flatMap((h) => h.lines) ?? []
  return (
    <section className="mt-6">
      <div className="mb-2 flex items-center gap-3">
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-gray-200" title={path}>
          {path}
        </span>
        {diff && !diff.binary && (
          <LineCounts added={lines.filter((l) => l[0] === '+').length} deleted={lines.filter((l) => l[0] === '-').length} />
        )}
        {diff && !diff.binary && !diff.tooLarge && diff.hunks.length > 0 && (
          <button type="button" className={BUTTON} onClick={() => setFull(!full)}>
            {full ? '바뀐 곳만' : '전체 보기'}
          </button>
        )}
      </div>
      {note && <div className="mb-2 text-xs text-gray-500">{note}</div>}
      {shown?.error && <p className={WARNING}>{shown.error}</p>}
      {!shown && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {diff?.binary && <p className="text-sm text-gray-500">글 파일이 아니라 비교할 수 없습니다.</p>}
      {diff?.tooLarge && <p className="text-sm text-gray-500">변경이 너무 커서(1MB 넘음) 그리지 않습니다.</p>}
      {diff && !diff.binary && !diff.tooLarge && diff.hunks.length === 0 && (
        <p className="text-sm text-gray-500">내용은 그대로이고 이름이나 권한만 바뀌었습니다.</p>
      )}
      {rows.length > 0 && (
        <div className="overflow-hidden rounded-md border border-edge bg-[#1e1e1e]">
          <table className="diff-view w-full table-fixed border-collapse text-[13px] leading-5">
            <colgroup>
              <col className="w-12" />
              <col />
              <col className="w-12" />
              <col />
            </colgroup>
            <tbody>
              {rows.map((r, i) =>
                'gap' in r ? (
                  <tr key={i}>
                    <td colSpan={4} className="border-y border-edge bg-panel py-1 text-center font-sans text-xs text-gray-500">
                      ⋯ 변경 없는 줄 {r.gap}개
                    </td>
                  </tr>
                ) : (
                  <tr key={i}>
                    <Cells side={r.left} tokens={leftTokens} />
                    <Cells side={r.right} tokens={rightTokens} divider />
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
})

/** 한 쪽의 줄 번호 칸 + 글 칸. 짝이 없는 쪽은 어두운 빈 칸 */
function Cells({ side, tokens, divider = false }: { side: Side | null; tokens: ThemedToken[][] | null; divider?: boolean }) {
  const edge = divider ? 'border-l border-edge' : ''
  if (!side) {
    return (
      <>
        <td className={`${EMPTY_BG} ${edge}`} />
        <td className={EMPTY_BG} />
      </>
    )
  }
  const parts = tokens?.[side.at]
  return (
    <>
      <td className={`pr-2 text-right align-top text-gray-600 select-none ${NUM_BG[side.kind]} ${edge}`}>{side.no}</td>
      <td className={`pl-2 align-top break-all whitespace-pre-wrap text-[#d4d4d4] ${SIDE_BG[side.kind]}`}>
        {parts
          ? parts.map((t, k) => (
              <span key={k} style={{ color: t.color }}>
                {t.content}
              </span>
            ))
          : side.text}
      </td>
    </>
  )
}
