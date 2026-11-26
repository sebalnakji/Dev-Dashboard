import { memo, useEffect, useMemo, useRef, useState } from 'react'

import { fetchFileFolders } from '../api/client'
import { WARNING, reasonOf } from '../components/styles'
import type { FileRow, FolderListing } from '../types'
import { usePolling } from './useProjectData'
import { openFolders, rememberFolders } from './remember'

/** git 에 올리지 않는 파일 · 폴더 표시. 이 PC 의 작업 폴더에는 있지만 저장소에는 없다 */
const IgnoredMark = () => (
  <span
    title="깃에 올리지 않는 파일입니다 (.gitignore). 이 PC 에만 있습니다"
    className="shrink-0 rounded border border-edge px-1 text-[10px] leading-4 text-gray-500"
  >
    깃 제외
  </span>
)

/** 하나를 만들어 계속 쓴다 — 비교마다 localeCompare 를 부르면 파일 수천 개에 수십 ms 가 든다. */
const collator = new Intl.Collator('ko', { sensitivity: 'base' })
const byPath = (a: { path: string }, b: { path: string }) => collator.compare(a.path, b.path)

/** 펼친 폴더에 path 를 품은 폴더들을 더한 것 (이미 다 펼쳐져 있으면 같은 크기). */
function withAncestors(open: Set<string>, path: string | null): Set<string> {
  if (!path) return open
  const parts = path.split('/')
  const ancestors = parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join('/'))
  return ancestors.every((a) => open.has(a)) ? open : new Set([...open, ...ancestors])
}

/** 지금 보이는(바깥 폴더까지 모두 펼친) 폴더들 — 맨 위('') 포함. 이것만 받아 온다. */
function shownFolders(open: Set<string>): string[] {
  const shown = [...open].filter((dir) => {
    const parts = dir.split('/')
    return parts.slice(0, -1).every((_, i) => open.has(parts.slice(0, i + 1).join('/')))
  })
  return ['', ...shown.sort()]
}

/** 마우스를 올리면 보이는 설명: 생긴 작업 · 최근 작업 · 변경 횟수 (훅 기록에서만). */
function hoverText(file: FileRow): string {
  if (!file.changes) return `${file.path}\n기록 없음`
  return [
    file.path,
    `생긴 작업: ${file.createdTask ?? '기록 없음'}`,
    `최근 작업: ${file.lastTask ?? '작업 없이 고침'}`,
    `변경 ${file.changes}번`,
  ].join('\n')
}

/**
 * 보이는 폴더들의 바로 아래를 몇 초마다 받는다. 폴더를 펼치면 곧바로 받는다.
 * 받은 폴더가 응답에 없으면(지워짐) 비운다. 내용이 같으면 상태를 바꾸지 않는다 (다시 그리지 않게).
 */
function useFolders(projectKey: string, dirs: string[]) {
  const [listings, setListings] = useState<Record<string, FolderListing>>({})
  const [error, setError] = useState<string | null>(null)
  const last = useRef<Record<string, string>>({})
  const want = dirs.join('\n')

  const load = useMemo(() => {
    const asked = want.split('\n')
    return () => {
      fetchFileFolders(projectKey, asked).then(
        (r) => {
          setError(null)
          let changed = false
          const next: Record<string, string> = { ...last.current }
          for (const dir of asked) {
            const text = r.folders[dir] ? JSON.stringify(r.folders[dir]) : ''
            if (text !== (next[dir] ?? '')) changed = true
            next[dir] = text
          }
          if (!changed) return
          last.current = next
          setListings((prev) => {
            const out = { ...prev }
            for (const dir of asked) {
              const listing = r.folders[dir]
              if (listing) {
                out[dir] = { ...listing, folders: [...listing.folders].sort(byPath), files: [...listing.files].sort(byPath) }
              } else delete out[dir]
            }
            return out
          })
        },
        (e: unknown) => setError(reasonOf(e)),
      )
    }
  }, [projectKey, want])
  usePolling(load)
  return { listings, error }
}

/**
 * 왼쪽 아래: 파일 구조. 폴더는 눌러서 펼친다 (펼친 폴더는 프로젝트마다 기억한다).
 * 펼친 폴더만 받아 온다 — 한꺼번에 받으면 파일 10만 개 폴더(KKU 의 Moodle)에서 상한에 걸려
 * 옆 폴더가 안 보였다.
 * 기록이 있는 파일은 변경 횟수를 오른쪽에 보이고, 고른 할 일에서 바뀐 파일과 그 파일을 품은
 * 폴더는 밝게 표시한다 (접힌 폴더 안에 있어도 보이게).
 */
export const FileTree = memo(function FileTree({
  projectKey,
  highlight,
  selected,
  onSelect,
}: {
  projectKey: string
  /** 고른 할 일에서 바뀐 파일들 */
  highlight: Set<string>
  /** [파일] 보기에서 고른 파일 (오른쪽에 그 파일의 이력이 열린다) */
  selected: string | null
  onSelect: (path: string) => void
}) {
  const litFolders = useMemo(() => {
    const out = new Set<string>()
    for (const path of highlight) {
      const parts = path.split('/')
      for (let i = 1; i < parts.length; i++) out.add(parts.slice(0, i).join('/'))
    }
    return out
  }, [highlight])
  // 처음 그릴 때부터 고른 파일을 품은 폴더는 펼쳐 둔다 — 다른 보기에서 넘어오면 트리가 새로
  // 만들어져 아래의 '고른 파일이 바뀌면' 이 돌지 않아, 파일이 접힌 폴더 안에 숨었다
  const [open, setOpen] = useState(() => withAncestors(openFolders(projectKey), selected))
  const dirs = useMemo(() => shownFolders(open), [open])
  const { listings, error } = useFolders(projectKey, dirs)

  // 고른 파일이 바뀌면 (다른 보기 · 문서 링크에서 넘어왔을 수 있다) 트리에서 그 줄이 보이게 스크롤한다.
  // 그 폴더를 아직 받지 않았을 수 있어, 줄이 그려질 때까지 받을 때마다 다시 찾는다
  const box = useRef<HTMLDivElement>(null)
  const scrolledFor = useRef<string | null>(null)
  useEffect(() => {
    if (!selected || scrolledFor.current === selected) return
    const el = box.current?.querySelector(`[data-path="${CSS.escape(selected)}"]`)
    if (!el) return
    el.scrollIntoView({ block: 'center' })
    scrolledFor.current = selected
  }, [selected, listings])
  // 문서 속 링크로 다른 파일을 열면, 그 파일을 품은 폴더를 펼쳐 트리에서도 보이게 한다
  const [shownSelected, setShownSelected] = useState(selected)
  if (selected !== shownSelected) {
    setShownSelected(selected)
    const next = withAncestors(open, selected)
    if (next.size !== open.size) {
      setOpen(next)
      rememberFolders(projectKey, next)
    }
  }

  function toggle(path: string) {
    const next = new Set(open)
    if (next.has(path)) next.delete(path)
    else next.add(path)
    setOpen(next)
    rememberFolders(projectKey, next)
  }

  function render(dir: string, depth: number, parentIgnored: boolean) {
    const listing = listings[dir]
    const pad = { paddingLeft: 6 + depth * 14 }
    if (!listing) {
      return (
        <p style={pad} className="py-1 text-xs text-gray-600">
          불러오는 중…
        </p>
      )
    }
    return (
      <>
        {listing.folders.map((sub) => (
          <div key={sub.path}>
            <button
              type="button"
              onClick={() => toggle(sub.path)}
              style={pad}
              className={`flex w-full cursor-pointer items-center gap-1.5 rounded-md py-1 pr-1.5 text-left text-[15px] hover:bg-hover ${
                litFolders.has(sub.path) ? 'text-accent' : sub.ignored || parentIgnored ? 'text-gray-500' : 'text-gray-300'
              }`}
            >
              <span className={`text-[9px] text-gray-500 transition-transform ${open.has(sub.path) ? '' : '-rotate-90'}`}>
                ▼
              </span>
              <span className="min-w-0 truncate">{sub.path.split('/').pop()}</span>
              {/* 바깥 폴더가 이미 '깃 제외'면 되풀이하지 않는다 */}
              {sub.ignored && !parentIgnored && <IgnoredMark />}
            </button>
            {open.has(sub.path) && render(sub.path, depth + 1, parentIgnored || !!sub.ignored)}
          </div>
        ))}
        {listing.files.map((file) => (
          <button
            type="button"
            key={file.path}
            data-path={file.path}
            title={hoverText(file)}
            onClick={() => onSelect(file.path)}
            style={{ paddingLeft: 6 + depth * 14 + 15 }}
            className={`flex w-full min-w-0 cursor-pointer items-center gap-2 rounded-md py-1 pr-1.5 text-left text-[15px] ${
              selected === file.path
                ? 'bg-btn text-gray-100'
                : highlight.has(file.path)
                  ? 'bg-[#2c3440] text-gray-100 hover:bg-hover'
                  : `hover:bg-hover ${file.changes && !file.ignored && !parentIgnored ? 'text-gray-200' : 'text-gray-500'}`
            }`}
          >
            <span className="min-w-0 flex-1 truncate">{file.path.split('/').pop()}</span>
            {file.ignored && !parentIgnored && <IgnoredMark />}
            {file.changes ? <span className="shrink-0 text-xs tabular-nums text-gray-500">{file.changes}</span> : null}
          </button>
        ))}
        {listing.truncated && (
          <p style={pad} className="py-1 text-xs text-gray-500">
            이 폴더는 항목이 많아 앞 5,000개만 보입니다
          </p>
        )}
      </>
    )
  }

  const top = listings['']
  return (
    <div ref={box} className="min-w-0">
      {error && <p className={`mb-3 ${WARNING}`}>{error}</p>}
      {top && top.folders.length === 0 && top.files.length === 0 ? (
        <p className="px-1.5 text-sm text-gray-500">파일이 없습니다.</p>
      ) : (
        (top || !error) && render('', 0, false)
      )}
    </div>
  )
})
