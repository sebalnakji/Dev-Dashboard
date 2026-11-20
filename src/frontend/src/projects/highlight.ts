import type { BundledLanguage, ThemedToken } from 'shiki'

/**
 * 코드를 VS Code 와 같은 색으로 칠한다 (Shiki).
 *
 * Shiki 는 VS Code 의 문법 정의(TextMate)를 그대로 써서 TSX 같은 복잡한 문법도 VS Code 와 똑같이 칠한다.
 * 전부 합치면 수 MB 라, 처음 코드를 열 때 불러오고 언어도 연 파일의 것만 내려받는다 —
 * 평소 화면(모니터링 · 할 일)은 가벼운 그대로다.
 *
 * 파일 보기(CodeView)와 좌우 비교(GitView)가 함께 쓴다. 불러오기를 한 번만 하려고 한곳에 둔다.
 */

const THEME = 'dark-plus'
/** 이보다 긴 코드는 칠하지 않는다 — 칠하는 데 수 초가 걸려 화면이 멈춘 듯 보인다. */
export const MAX_HIGHLIGHT_LINES = 3000

let shiki: Promise<typeof import('shiki')> | null = null

async function load(lang: string) {
  shiki ??= import('shiki')
  const module = await shiki
  // 모르는 확장자는 색 없이 (Shiki 가 모르는 언어 이름이면 오류를 낸다)
  const known = (lang in module.bundledLanguages ? lang : 'text') as BundledLanguage
  return { module, known }
}

/** 통째로 칠한 HTML (파일 보기). 코드 글자는 모두 이스케이프되어 그대로 넣어도 안전하다. */
export async function highlight(code: string, lang: string): Promise<string> {
  const { module, known } = await load(lang)
  return module.codeToHtml(code, { lang: known, theme: THEME })
}

/**
 * 줄마다 색 조각으로 (좌우 비교). 비교는 이전 · 이후 줄을 표의 칸에 하나씩 넣어야 해서
 * 통째로 만든 HTML 대신 줄 단위 조각을 받는다.
 */
export async function tokenize(code: string, lang: string): Promise<ThemedToken[][]> {
  const { module, known } = await load(lang)
  return (await module.codeToTokens(code, { lang: known, theme: THEME })).tokens
}
