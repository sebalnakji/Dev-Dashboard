/**
 * 화면이 이 브라우저에만 기억해 두는 것들. 저장이 막힌 브라우저면 이번 화면에서만 기억한다.
 * 같은 주소(localhost)를 쓰는 다른 앱과 겹치지 않게 이름 앞에 'devdash:' 를 붙인다.
 */

const PREFIX = 'devdash:'

function read(key: string): unknown {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    return raw == null ? null : JSON.parse(raw)
  } catch {
    return null
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value))
  } catch {
    // 저장이 막혔으면 기억하지 않을 뿐이다
  }
}

/** 프로젝트 탭으로 돌아오면 마지막에 보던 프로젝트를 다시 연다. */
export function lastProject(): string | null {
  const value = read('lastProject')
  return typeof value === 'string' ? value : null
}
export const rememberProject = (key: string) => write('lastProject', key)

/** 파일 구조에서 펼쳐 둔 폴더 (프로젝트마다). 모양이 다른 값이면 버린다 — 흰 화면이 됐다. */
export function openFolders(key: string): Set<string> {
  const value = read(`openFolders:${key}`)
  return new Set(Array.isArray(value) ? value.filter((v) => typeof v === 'string') : [])
}
export const rememberFolders = (key: string, open: Set<string>) => write(`openFolders:${key}`, [...open])

/**
 * [세션 고르기]를 한 번이라도 저장한 프로젝트. 같은 저장소 세션을 미리 체크하는 것은 처음 고를
 * 때뿐이다 — 일부러 다 뺀 뒤 다시 열었을 때 또 체크되면 안 된다.
 */
export const pickedBefore = (key: string) => read(`picked:${key}`) === true
export const rememberPicked = (key: string) => write(`picked:${key}`, true)

/**
 * 세션마다 마지막으로 대화를 본 시각 (ISO). 이 뒤에 끝난 답을 '새 답'으로 센다.
 * 처음 보는 세션은 기억이 없으므로 null — 화면이 그때의 시각을 기억해 지난 답을 새 답으로 세지 않는다.
 */
export function seenAt(sessionId: string): string | null {
  const value = read(`seen:${sessionId}`)
  return typeof value === 'string' ? value : null
}
export const rememberSeen = (sessionId: string, iso: string) => write(`seen:${sessionId}`, iso)

/**
 * [git] 보기를 마지막으로 볼 때 목록에 있던 커밋 번호들 (프로젝트마다, T-10). 여기 없는 커밋이 '새 커밋'이다.
 * 시각이 아니라 번호로 센다 — 동료가 어제 만든 커밋을 오늘 받아 오면, 만든 시각은 본 시각보다 앞이어도 새것이다.
 * 처음 보는 프로젝트는 null.
 */
export function gitSeen(key: string): Set<string> | null {
  const value = read(`gitSeen:${key}`)
  return Array.isArray(value) ? new Set(value.filter((v) => typeof v === 'string')) : null
}
export const rememberGitSeen = (key: string, hashes: string[]) => write(`gitSeen:${key}`, hashes)

/** 프로젝트 탭에서 마지막에 고른 보기 ([할 일] [파일] [git] [세션]). */
export type ProjectViewName = 'tasks' | 'files' | 'git' | 'sessions'
const VIEWS: ProjectViewName[] = ['tasks', 'files', 'git', 'sessions']
export function lastView(): ProjectViewName {
  const value = read('projectView')
  return VIEWS.includes(value as ProjectViewName) ? (value as ProjectViewName) : 'tasks'
}
export const rememberView = (view: ProjectViewName) => write('projectView', view)
