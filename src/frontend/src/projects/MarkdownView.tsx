import { memo, useMemo, useState } from 'react'
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { CodeView, MermaidView } from './CodeView'

/**
 * [파일] 보기에서 고른 .md 문서를 읽기 좋게 그린다 (T-4).
 *
 * - 맨 위 머리말(--- 사이 tags · 상태 · 날짜 등)은 작은 정보 줄로
 * - 본문은 react-markdown + remark-gfm (표 · 체크박스 · 취소선까지). 이 저장소의 docs 는 ADR · 지식 노트라
 *   표와 코드 블록이 많아 직접 만든 렌더러로는 금방 깨졌다
 * - Obsidian 위키링크 [[문서]] · [[문서|별칭]] 과 상대 경로 .md 링크는 누르면 그 문서로 넘어간다
 *   (같은 프로젝트에서 이름이 맞는 .md 를 찾는다. 못 찾으면 흐리게 둔다)
 */
export const MarkdownView = memo(MarkdownViewInner)

/**
 * 같은 글이면 다시 그리지 않는다 (memo). 부르는 쪽(대화 · 커밋)이 '3분 전'을 흘리려고 1초마다 다시
 * 그려도 마크다운은 그대로 둔다 — 그러지 않으면 드래그로 고른 글이 풀리고 코드 색 · 그림을 매번 다시
 * 만들었다 (8단계 Fable 검토). 그래서 부르는 쪽도 onOpen · mdFiles 를 고정해서 넘긴다.
 */
function MarkdownViewInner({
  text,
  path,
  mdFiles,
  onOpen,
  imageUrl,
  compact = false,
}: {
  text: string
  /** 이 문서의 저장소 기준 경로 (상대 링크를 풀 때 쓴다) */
  path: string
  /** 프로젝트의 .md 파일 경로들 (위키링크를 찾을 때 쓴다) */
  mdFiles: string[]
  onOpen: (path: string) => void
  /** 문서 속 상대 경로 그림(![](images/a.png))의 주소. 없으면 그림 대신 이름만 보인다 (대화 등) */
  imageUrl?: (path: string) => string
  /** 대화의 답처럼 짧은 글: 제목을 작게, 간격을 좁게 (T-9) */
  compact?: boolean
}) {
  const s = compact ? COMPACT : DOCUMENT
  const { meta, body } = useMemo(() => splitFrontmatter(text), [text])
  const source = useMemo(() => toLinks(body), [body])
  const byName = useMemo(() => {
    const map = new Map<string, string>()
    for (const p of mdFiles) map.set(baseName(p).toLowerCase(), p)
    return map
  }, [mdFiles])

  // 요소를 그리는 함수들. 렌더마다 새로 만들면 react-markdown 이 요소 종류가 바뀐 것으로 보고
  // 문단 · 목록 · 코드를 전부 떼었다 새로 붙인다. 그래서 고정해 둔다
  const components = useMemo<Components>(() => {
    /** 링크가 가리키는 이 프로젝트의 .md 경로. 아니면 null. */
    function resolve(href: string): string | null {
      if (href.startsWith('wikilink:')) {
        const name = safeDecode(href.slice('wikilink:'.length))
        return name == null ? null : (byName.get(name.toLowerCase()) ?? null)
      }
      if (/^[a-z]+:/i.test(href) || !/\.md(#.*)?$/i.test(href)) return null
      const rel = safeDecode(href.replace(/#.*$/, ''))
      if (rel == null) return null
      const target = joinPath(dirOf(path), rel)
      return mdFiles.includes(target) ? target : null
    }

    return {
    h1: (p) => <h1 className={s.h1} {...p} />,
    h2: (p) => <h2 className={s.h2} {...p} />,
    h3: (p) => <h3 className={s.h3} {...p} />,
    h4: (p) => <h4 className={s.h4} {...p} />,
    p: (p) => <p className={s.p} {...p} />,
    ul: (p) => <ul className={`${s.list} list-disc pl-6 marker:text-gray-600`} {...p} />,
    ol: (p) => <ol className={`${s.list} list-decimal pl-6 marker:text-gray-500`} {...p} />,
    li: (p) => <li className={s.li} {...p} />,
    blockquote: (p) => <blockquote className={`${s.block} border-l-2 border-accent/60 pl-4 text-gray-300`} {...p} />,
    hr: () => <hr className={`${s.hr} border-edge`} />,
    table: (p) => (
      <div className={`${s.block} overflow-x-auto`}>
        <table className={`w-full border-collapse ${s.table}`} {...p} />
      </div>
    ),
    th: (p) => <th className={`border border-edge bg-panel ${s.cell} text-left font-semibold text-gray-100`} {...p} />,
    td: (p) => <td className={`border border-edge ${s.cell} align-top`} {...p} />,
    // 코드 블록은 CodeView 가 상자째 그리므로 pre 는 감싸지 않는다
    pre: ({ children }) => <div className={s.block}>{children}</div>,
    code: ({ className, children }) => {
      const text = String(children ?? '')
      const lang = /language-([\w-]+)/.exec(className ?? '')?.[1]
      // 언어가 붙었거나 여러 줄이면 코드 블록, 아니면 줄 안의 `코드`
      if (!lang && !text.includes('\n')) {
        return <code className="rounded bg-btn px-1.5 py-0.5 font-mono text-[0.88em] text-gray-100">{children}</code>
      }
      if (lang === 'mermaid') return <MermaidView code={text} />
      return <CodeView code={text} lang={lang ?? 'text'} numbers={false} />
    },
    img: ({ alt, src }) => {
      // 이 프로젝트 안의 상대 경로 그림만 보인다. 바깥 주소는 불러오지 않는다 —
      // 이 PC 안에서만 도는 대시보드가 문서를 열었다고 밖으로 요청을 내보내지 않게
      const rel = typeof src === 'string' && !/^([a-z]+:|\/|#)/i.test(src) ? safeDecode(src) : null
      if (rel == null || !imageUrl) return <span className="text-gray-500">[그림: {alt || '이름 없음'}]</span>
      return <DocImage src={imageUrl(joinPath(dirOf(path), rel))} alt={alt ?? ''} />
    },
    a: ({ href = '', children }) => {
      const internal = href.startsWith('wikilink:') || (!/^[a-z]+:/i.test(href) && /\.md(#.*)?$/i.test(href))
      if (internal) {
        const target = resolve(href)
        return target ? (
          <button type="button" onClick={() => onOpen(target)} className="cursor-pointer text-accent hover:underline">
            {children}
          </button>
        ) : (
          <span title="이 프로젝트에서 찾지 못한 문서입니다" className="text-gray-500">
            {children}
          </span>
        )
      }
      return (
        <a href={href} target="_blank" rel="noreferrer" className="text-accent hover:underline">
          {children}
        </a>
      )
    },
    }
  }, [s, byName, path, mdFiles, onOpen, imageUrl])

  return (
    <div className={s.root}>
      {meta.length > 0 && (
        <div className="mb-5 flex flex-wrap gap-x-4 gap-y-1 rounded-md border border-edge px-3 py-2 text-xs text-gray-400">
          {meta.map(([k, v]) => (
            <span key={k}>
              <span className="text-gray-500">{k}</span> {v}
            </span>
          ))}
        </div>
      )}
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
        // 위키링크는 우리가 만든 'wikilink:' 주소라 그대로 두고, 나머지는 기본 검사(위험한 주소 제거)를 거친다
        urlTransform={(url) => (url.startsWith('wikilink:') ? url : defaultUrlTransform(url))}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}

/** 문서 보기 ([파일] 보기의 .md): 제목이 문서의 뼈대라 크고 간격이 넓다. */
const DOCUMENT = {
  root: 'text-[15px] text-gray-200',
  h1: 'mt-2 mb-4 text-2xl font-bold text-gray-50',
  h2: 'mt-8 mb-3 border-b border-edge pb-1.5 text-xl font-semibold text-gray-100',
  h3: 'mt-6 mb-2 text-[17px] font-semibold text-gray-100',
  h4: 'mt-5 mb-2 text-[15px] font-semibold text-gray-200',
  p: 'my-3 leading-relaxed',
  list: 'my-3 space-y-1.5',
  li: 'leading-relaxed',
  block: 'my-4',
  hr: 'my-6',
  table: 'text-sm',
  cell: 'px-3 py-1.5',
}

/**
 * 대화의 답: 답 안의 '## 제목'은 문단 머리 정도라 본문보다 조금만 크게, 간격은 좁게.
 * 상자 위아래에 빈 틈이 생기지 않게 첫 · 마지막 요소의 바깥 간격을 없앤다.
 */
const COMPACT: typeof DOCUMENT = {
  root: 'text-[15px] leading-[1.75] text-gray-200 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0',
  h1: 'mt-5 mb-2 text-[17px] font-bold text-gray-50',
  h2: 'mt-5 mb-2 text-[16px] font-semibold text-gray-50',
  h3: 'mt-4 mb-1.5 text-[15px] font-semibold text-gray-100',
  h4: 'mt-3 mb-1 text-[15px] font-semibold text-gray-200',
  p: 'my-2',
  list: 'my-2 space-y-1',
  li: '',
  block: 'my-3',
  hr: 'my-4',
  table: 'text-[13px] leading-normal',
  cell: 'px-2.5 py-1',
}

/** 맨 위 '---' 사이 머리말을 떼어 (키, 값) 목록으로. 목록 값(- a)은 쉼표로 잇는다. */
function splitFrontmatter(text: string): { meta: [string, string][]; body: string } {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(text)
  if (!m) return { meta: [], body: text }
  const meta: [string, string][] = []
  for (const line of m[1].split(/\r?\n/)) {
    const item = /^\s+-\s+(.*)$/.exec(line)
    if (item && meta.length) {
      const last = meta[meta.length - 1]
      last[1] = last[1] ? `${last[1]}, ${item[1]}` : item[1]
      continue
    }
    const kv = /^([^:\s][^:]*):\s*(.*)$/.exec(line)
    if (kv) meta.push([kv[1].trim(), kv[2].trim()])
  }
  return { meta, body: text.slice(m[0].length) }
}

/**
 * 코드 블록(``` · ~~~ 로 감싼 줄들)과 줄 안의 `코드`. 위키링크 바꾸기에서 건너뛴다 —
 * bash 의 `if [[ -f a ]]` 나 TOML 의 `[[tool.x]]` 가 링크로 바뀌어 틀린 코드가 보였다 (8단계 Fable 검토).
 * 닫히지 않은 코드 블록은 문서 끝까지 코드로 본다 (마크다운도 그렇게 그린다).
 */
const CODE = /(^ {0,3}(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^ {0,3}\2[^\S\n]*$|(?![\s\S]))|(`+)[^`]*?\3)/gm

/** [[문서]] · [[문서|별칭]] · [[문서#제목]] 을 마크다운 링크로 바꾼다. 코드 안은 그대로 둔다. */
function toLinks(body: string): string {
  return body
    .split(CODE)
    .map((part, i) => {
      // split 이 잡은 묶음(코드 · 여는 울타리 · 백틱)이 사이사이 끼어 온다. 넷마다 첫째가 글이다
      if (i % 4 !== 0 || part === undefined) return part ?? ''
      return part.replace(/\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g, (_, target: string, alias?: string) => {
        const name = target.trim()
        return `[${(alias ?? name).trim()}](wikilink:${encodeURIComponent(name)})`
      })
    })
    .filter((_, i) => i % 4 === 0 || i % 4 === 1) // 글과 코드 묶음만 다시 잇는다
    .join('')
}

/** 퍼센트 인코딩을 푼다. 깨진 주소(a%FF.md)면 null — 그대로 풀면 예외로 화면 전체가 하얗게 됐다. */
/** 문서 속 그림. 못 불러오면(없음 · 너무 큼 · 폴더 밖) 이름만 남긴다 */
function DocImage({ src, alt }: { src: string; alt: string }) {
  const [broken, setBroken] = useState(false)
  if (broken) return <span className="text-gray-500">[그림을 열지 못함: {alt || src}]</span>
  return <img src={src} alt={alt} onError={() => setBroken(true)} className="my-2 inline-block max-w-full rounded" />
}

function safeDecode(value: string): string | null {
  try {
    return decodeURIComponent(value)
  } catch {
    return null
  }
}

const baseName = (p: string) => p.split('/').pop()!.replace(/\.md$/i, '')
const dirOf = (p: string) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '')

/** 'docs/decisions' + '../knowledge/x.md' → 'docs/knowledge/x.md' */
function joinPath(dir: string, rel: string): string {
  const parts = rel.startsWith('/') ? [] : dir.split('/').filter(Boolean)
  for (const seg of rel.split('/')) {
    if (seg === '..') parts.pop()
    else if (seg && seg !== '.') parts.push(seg)
  }
  return parts.join('/')
}
