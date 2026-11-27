import { rememberView, type ProjectViewName } from './remember'

/* 프로젝트 화면의 이동(뒤로 가기)을 브라우저 방문 기록에 적는 모양. ProjectView 와 다른 탭이 같이 쓴다. */

/** 뒤로 가기용 화면 하나. 브라우저 방문 기록의 state 에 넣는다 — 주소(해시)는 프로젝트까지만 쓴다. */
export interface Nav {
  key: string
  view: ProjectViewName
  chosenFile: string | null
  selectedPath: string | null
  selectedSession: string | null
  selectedCommit: string | null
  /** 오른쪽 칸의 스크롤 위치 */
  scroll: number
}

export const NAV = 'devdashNav'

/**
 * 다른 탭(화면분할)에서 이 프로젝트의 파일 · 할 일을 연다. 방문 기록에 한 칸 남기므로 뒤로 가기를
 * 누르면 떠나온 탭으로 돌아온다. 그 프로젝트 화면이 이미 (숨어) 있으면 popstate 로 알려 그 화면으로
 * 바꾸고, 없으면 새로 열리면서 방문 기록의 화면에서 시작한다 (savedNav).
 */
export function openProjectAt(key: string, patch: Partial<Omit<Nav, 'key' | 'scroll'>>) {
  const to: Nav = {
    key,
    view: 'tasks',
    chosenFile: null,
    selectedPath: null,
    selectedSession: null,
    selectedCommit: null,
    ...patch,
    scroll: 0,
  }
  rememberView(to.view)
  window.history.pushState({ [NAV]: to }, '', `#/projects/${encodeURIComponent(key)}`)
  window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }))
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

/** 지금 방문 기록 칸에 적힌 이 프로젝트의 화면 (없거나 다른 프로젝트 것이면 null) */
export function savedNav(key: string): Nav | null {
  const nav = (window.history.state as Record<string, unknown> | null)?.[NAV] as Nav | undefined
  return nav && nav.key === key ? nav : null
}
