import { useEffect, useState } from 'react'

/**
 * 주소창 해시로 화면을 나눈다.
 *
 * 화면이 몇 개뿐이라 라우터 라이브러리는 과하다. 해시를 쓰면 뒤로가기와 새로고침이
 * 그대로 동작하고, 서버 설정도 건드릴 필요가 없다.
 *
 *   (비어 있음)          모니터링 탭 (서버 | 세션 | 메모리)
 *   #/projects           프로젝트 탭 (프로젝트는 화면이 고른다)
 *   #/projects/<키>      프로젝트 탭의 그 프로젝트
 *   #/split              화면분할 탭 (화면은 페이지가 고른다)
 *   #/split/<화면 id>    화면분할 탭의 그 화면
 *   #/<서버 id>          그 서버의 상세
 */
export type Route =
  | { view: 'monitor' }
  | { view: 'projects'; key: string | null }
  | { view: 'split'; id: string | null }
  | { view: 'server'; id: string }

export function useHashRoute(): Route {
  const [route, setRoute] = useState(readHash)

  useEffect(() => {
    const onChange = () => setRoute(readHash())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  return route
}

function readHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, '')
  if (!raw) return { view: 'monitor' }
  const [head, ...rest] = raw.split('/')
  try {
    if (head === 'projects') {
      return { view: 'projects', key: rest.length ? decodeURIComponent(rest.join('/')) : null }
    }
    if (head === 'split') {
      return { view: 'split', id: rest.length ? decodeURIComponent(rest.join('/')) : null }
    }
    return { view: 'server', id: decodeURIComponent(raw) }
  } catch {
    // 손으로 고친 주소 등 풀 수 없는 해시면 흰 화면 대신 모니터링으로
    return { view: 'monitor' }
  }
}

export function goToServer(serverId: string) {
  window.location.hash = `/${encodeURIComponent(serverId)}`
}

export function goToGrid() {
  window.location.hash = ''
}

export function goToProject(key: string | null) {
  window.location.hash = key ? `/projects/${encodeURIComponent(key)}` : '/projects'
}

/** 화면이 스스로 프로젝트를 정할 때(주소에 없음 · 사라짐)는 이력을 덮어써 뒤로가기가 갇히지 않게 한다. */
export function replaceProject(key: string) {
  window.location.replace(`#/projects/${encodeURIComponent(key)}`)
}

export function goToSplit(id: string | null) {
  window.location.hash = id ? `/split/${encodeURIComponent(id)}` : '/split'
}

/** 화면이 스스로 정한 이동(주소에 없음 · 지운 화면)은 이력을 덮어쓴다 — replaceProject 와 같은 까닭 */
export function replaceSplit(id: string) {
  window.location.replace(`#/split/${encodeURIComponent(id)}`)
}
