import { useEffect, useState } from 'react'

import { MAX_HIGHLIGHT_LINES, highlight } from './highlight'

/**
 * 코드를 VS Code 와 같은 색으로 보여 준다 (Shiki, T-6 — 칠하는 일은 highlight.ts).
 * 색이 입혀지기 전에는 줄 번호 붙은 글자로 먼저 보인다.
 */

/**
 * 코드 상자. numbers 면 줄 번호를 붙인다 (파일 보기). md 안의 코드 블록은 짧아 붙이지 않는다.
 * Shiki 가 만든 HTML 은 코드 글자를 모두 이스케이프하므로 그대로 넣어도 안전하다.
 */
export function CodeView({ code, lang, numbers = true }: { code: string; lang: string; numbers?: boolean }) {
  const text = code.replace(/\n$/, '')
  const lineCount = text.split('\n').length
  const [colored, setColored] = useState<{ text: string; lang: string; html: string } | null>(null)

  useEffect(() => {
    if (lineCount > MAX_HIGHLIGHT_LINES) return
    let alive = true
    highlight(text, lang)
      .then((html) => alive && setColored({ text, lang, html }))
      .catch(() => {}) // 칠하지 못하면 글자 그대로 둔다
    return () => {
      alive = false
    }
  }, [text, lang, lineCount])

  const cls = `code-view ${numbers ? 'numbered' : ''}`
  if (colored && colored.text === text && colored.lang === lang) {
    return <div className={cls} dangerouslySetInnerHTML={{ __html: colored.html }} />
  }
  return (
    <div className={cls}>
      <pre>
        <code>
          {text.split('\n').map((line, i) => (
            <span key={i} className="line">
              {line}
              {'\n'}
            </span>
          ))}
        </code>
      </pre>
      {lineCount > MAX_HIGHLIGHT_LINES && (
        <p className="mt-2 text-xs text-gray-500">
          {lineCount.toLocaleString()}줄이라 색을 입히지 않았습니다 ({MAX_HIGHLIGHT_LINES.toLocaleString()}줄 넘음).
        </p>
      )}
    </div>
  )
}

let mermaid: Promise<typeof import('mermaid')['default']> | null = null

function loadMermaid() {
  mermaid ??= import('mermaid').then(({ default: m }) => {
    // strict: 그림 안의 HTML · 클릭 스크립트를 막는다 (문서는 Claude 가 쓴 것이라도 화면에서 그대로 실행되면 안 된다)
    m.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'strict', fontFamily: 'Pretendard Variable, sans-serif' })
    return m
  })
  return mermaid
}

let mermaidSeq = 0

/** md 안의 ```mermaid 블록을 그림으로 (T-6). 문법이 틀리면 이유와 원문을 보여 준다. */
export function MermaidView({ code }: { code: string }) {
  const [result, setResult] = useState<{ code: string; svg?: string; error?: string } | null>(null)

  useEffect(() => {
    let alive = true
    const id = `mermaid-${++mermaidSeq}`
    loadMermaid()
      .then((m) => m.render(id, code))
      .then(({ svg }) => alive && setResult({ code, svg }))
      .catch((e: unknown) => alive && setResult({ code, error: e instanceof Error ? e.message : String(e) }))
    return () => {
      alive = false
    }
  }, [code])

  const shown = result?.code === code ? result : null
  if (shown?.svg) {
    return (
      <div
        className="my-4 overflow-x-auto rounded-md border border-edge bg-[#191918] p-4 [&_svg]:mx-auto [&_svg]:max-w-full"
        dangerouslySetInnerHTML={{ __html: shown.svg }}
      />
    )
  }
  return (
    <div className="my-4">
      {shown?.error && <p className="mb-2 text-xs text-danger">다이어그램을 그리지 못했습니다: {shown.error}</p>}
      {!shown && <p className="mb-2 text-xs text-gray-500">다이어그램을 그리는 중…</p>}
      <CodeView code={code} lang="text" numbers={false} />
    </div>
  )
}
