import { memo, useEffect, useMemo, useRef, useState } from 'react'

import type { FileRow } from '../types'
import { openFolders, rememberFolders } from './remember'

interface Folder {
  name: string
  path: string
  folders: Folder[]
  files: FileRow[]
  /** 안의 파일이 모두 git 제외 (logs/ · .claude/ 등). 'git 제외' 표시를 폴더에 한 번만 붙인다 */
  ignored: boolean
}

/** git 에 올리지 않는 파일 · 폴더 표시. 이 PC 의 작업 폴더에는 있지만 저장소에는 없다 */
const IgnoredMark = () => (
  <span
    title="깃에 올리지 않는 파일입니다 (.gitignore). 이 PC 에만 있습니다"
    className="shrink-0 rounded border border-edge px-1 text-[10px] leading-4 text-gray-500"
  >
    깃 제외
  </span>
)

/** 하나를 만들어 계속 쓴다 — 비교마다 localeCompare 를 부르면 파일 4천 개에 20ms 가 든다. */
const collator = new Intl.Collator('ko', { sensitivity: 'base' })

/** 경로 목록을 폴더 나무로. 정렬도 여기서 한 번만 한다 (그릴 때마다 정렬하지 않게). */
function buildTree(files: FileRow[]): Folder {
  type Node = { name: string; path: string; folders: Map<string, Node>; files: FileRow[] }
  const root: Node = { name: '', path: '', folders: new Map(), files: [] }
  for (const file of files) {
    let node = root
    for (const part of file.path.split('/').slice(0, -1)) {
      let next = node.folders.get(part)
      if (!next) {
        next = { name: part, path: node.path ? `${node.path}/${part}` : part, folders: new Map(), files: [] }
        node.folders.set(part, next)
      }
      node = next
    }
    node.files.push(file)
  }
  const finish = (n: Node): Folder => {
    const folders = [...n.folders.values()].sort((a, b) => collator.compare(a.name, b.name)).map(finish)
    const files = n.files.sort((a, b) => collator.compare(a.path, b.path))
    return {
      name: n.name,
      path: n.path,
      folders,
      files,
      ignored: folders.every((f) => f.ignored) && files.every((f) => f.ignored),
    }
  }
  return finish(root)
}

/** 펼친 폴더에 path 를 품은 폴더들을 더한 것 (이미 다 펼쳐져 있으면 같은 크기). */
function withAncestors(open: Set<string>, path: string | null): Set<string> {
  if (!path) return open
  const parts = path.split('/')
  const ancestors = parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join('/'))
  return ancestors.every((a) => open.has(a)) ? open : new Set([...open, ...ancestors])
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
 * 왼쪽 아래: 파일 구조. 폴더는 눌러서 펼친다 (펼친 폴더는 프로젝트마다 기억한다).
 * 기록이 있는 파일은 변경 횟수를 오른쪽에 보이고, 고른 할 일에서 바뀐 파일과 그 파일을 품은
 * 폴더는 밝게 표시한다 (접힌 폴더 안에 있어도 보이게).
 * 내용이 그대로면 다시 그리지 않는다(memo) — 파일이 수천 개면 그리는 데만 수십 ms 다.
 */
export const FileTree = memo(function FileTree({
  projectKey,
  files,
  highlight,
  selected,
  onSelect,
}: {
  projectKey: string
  files: FileRow[]
  /** 고른 할 일에서 바뀐 파일들 */
  highlight: Set<string>
  /** [파일] 보기에서 고른 파일 (오른쪽에 그 파일의 이력이 열린다) */
  selected: string | null
  onSelect: (path: string) => void
}) {
  const tree = useMemo(() => buildTree(files), [files])
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
  // 고른 파일이 바뀌면 (다른 보기 · 문서 링크에서 넘어왔을 수 있다) 트리에서 그 줄이 보이게 스크롤한다
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!selected) return
    const el = box.current?.querySelector(`[data-path="${CSS.escape(selected)}"]`)
    el?.scrollIntoView({ block: 'center' })
  }, [selected])
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

  function render(folder: Folder, depth: number) {
    const pad = { paddingLeft: 6 + depth * 14 }
    return (
      <>
        {folder.folders.map((sub) => (
          <div key={sub.path}>
            <button
              type="button"
              onClick={() => toggle(sub.path)}
              style={pad}
              className={`flex w-full cursor-pointer items-center gap-1.5 rounded-md py-1 pr-1.5 text-left text-[15px] hover:bg-hover ${
                litFolders.has(sub.path) ? 'text-accent' : sub.ignored ? 'text-gray-500' : 'text-gray-300'
              }`}
            >
              <span className={`text-[9px] text-gray-500 transition-transform ${open.has(sub.path) ? '' : '-rotate-90'}`}>
                ▼
              </span>
              <span className="min-w-0 truncate">{sub.name}</span>
              {/* 바깥 폴더가 이미 'git 제외'면 되풀이하지 않는다 */}
              {sub.ignored && !folder.ignored && <IgnoredMark />}
            </button>
            {open.has(sub.path) && render(sub, depth + 1)}
          </div>
        ))}
        {folder.files.map((file) => (
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
                  : `hover:bg-hover ${file.changes && !file.ignored ? 'text-gray-200' : 'text-gray-500'}`
            }`}
          >
            <span className="min-w-0 flex-1 truncate">{file.path.split('/').pop()}</span>
            {file.ignored && !folder.ignored && <IgnoredMark />}
            {file.changes ? <span className="shrink-0 text-xs tabular-nums text-gray-500">{file.changes}</span> : null}
          </button>
        ))}
      </>
    )
  }

  if (files.length === 0) return <p className="px-1.5 text-sm text-gray-500">파일이 없습니다.</p>
  return (
    <div ref={box} className="min-w-0">
      {render(tree, 0)}
    </div>
  )
})
