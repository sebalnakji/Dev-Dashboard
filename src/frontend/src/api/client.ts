import type {
  ActionResult,
  ChatResponse,
  CommitDetail,
  CommitSummary,
  ContainerLogs,
  ContainersResponse,
  FileContent,
  FolderChoice,
  FileDiff,
  FileHistoryRow,
  FilesResponse,
  GitHistory,
  PcMemory,
  ProjectCandidate,
  ProjectRow,
  ProjectSession,
  SamplesResponse,
  ServerSummary,
  SessionChoice,
  SessionsView,
  TaskDetail,
  TaskFileDiff,
  TaskRow,
} from '../types'

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) {
    // 404 는 본문에 안내가 들어 있다
    const detail = await response.text().catch(() => '')
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  return (await response.json()) as T
}

export function fetchServers(): Promise<ServerSummary[]> {
  return getJson<ServerSummary[]>('/api/servers')
}

export function fetchSamples(serverId: string, points = 600): Promise<SamplesResponse> {
  return getJson<SamplesResponse>(
    `/api/servers/${encodeURIComponent(serverId)}/samples?points=${points}`,
  )
}

export function fetchContainers(serverId: string): Promise<ContainersResponse> {
  return getJson<ContainersResponse>(`/api/servers/${encodeURIComponent(serverId)}/containers`)
}

export function fetchSessions(): Promise<SessionsView> {
  return getJson<SessionsView>('/api/claude/sessions')
}

export function fetchPcMemory(): Promise<PcMemory> {
  return getJson<PcMemory>('/api/pc/memory')
}

export type SessionAction = 'start' | 'stop' | 'foreground' | 'delete'

/**
 * 세션 칸의 동작. 거절(409)도 본문에 이유나 확인 요청이 담겨 오므로 예외로 던지지 않고
 * 그대로 돌려준다. 화면이 그 말을 안내 상자에 띄우거나 물어본다.
 */
async function sendJson(method: string, path: string, body?: unknown): Promise<ActionResult> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return (await response.json()) as ActionResult
}

const sessionPath = (id: string) => `/api/claude/sessions/${encodeURIComponent(id)}`
const groupPath = (name: string) => `/api/claude/groups/${encodeURIComponent(name)}`

export const postSessionAction = (id: string, action: SessionAction, force = false) =>
  sendJson('POST', `${sessionPath(id)}/${action}`, { force })

export const postStartAll = () => sendJson('POST', '/api/claude/start-all')

export const postRestore = (id: string) => sendJson('POST', `/api/claude/deleted/${encodeURIComponent(id)}/restore`)

export const createSession = (body: { cwd: string; name: string; group: string | null; force?: boolean }) =>
  sendJson('POST', '/api/claude/sessions', body)

export const fetchFolders = () => getJson<FolderChoice[]>('/api/claude/folders')

export const addGroup = (name: string) => sendJson('POST', '/api/claude/groups', { name })

export const patchGroup = (name: string, change: { name?: string; collapsed?: boolean }) =>
  sendJson('PATCH', groupPath(name), change)

export const deleteGroup = (name: string) => sendJson('DELETE', groupPath(name))

/** 세션을 group(null 이면 분류 안 됨)으로 옮기고, 그 그룹의 순서를 order 대로 */
export const placeSession = (id: string, group: string | null, order: string[]) =>
  sendJson('PUT', `${sessionPath(id)}/place`, { group, order })

export function fetchContainerLogs(
  serverId: string,
  containerId: string,
  tail: number,
): Promise<ContainerLogs> {
  return getJson<ContainerLogs>(
    `/api/servers/${encodeURIComponent(serverId)}/containers/${encodeURIComponent(containerId)}/logs?tail=${tail}`,
  )
}

// --- 프로젝트 탭 ---

const project = (key: string) => `/api/projects/${encodeURIComponent(key)}`

export const fetchProjects = () => getJson<ProjectRow[]>('/api/projects')
export const fetchCandidates = () => getJson<ProjectCandidate[]>('/api/projects/candidates')
export const fetchProjectFiles = (key: string) => getJson<FilesResponse>(`${project(key)}/files`)
export const fetchProjectSessions = (key: string) =>
  getJson<ProjectSession[]>(`${project(key)}/sessions`)
export const fetchSessionChoices = (key: string) =>
  getJson<SessionChoice[]>(`${project(key)}/session-choices`)
export const fetchTasks = (key: string) => getJson<TaskRow[]>(`${project(key)}/tasks`)
/** file: 같은 번호가 둘일 때 고른 파일 이름 */
export const fetchTask = (key: string, number: number, file: string) =>
  getJson<TaskDetail>(`${project(key)}/tasks/${number}?file=${encodeURIComponent(file)}`)
export const fetchGit = (key: string) => getJson<GitHistory>(`${project(key)}/git`)
/** 작업 하나가 파일을 어떻게 바꿨나 (T-19). full: 바뀐 곳 주변만이 아니라 파일 전체 */
export const fetchTaskDiff = (key: string, number: number, path: string, full: boolean) =>
  getJson<TaskFileDiff>(
    `${project(key)}/tasks/${number}/diff?${new URLSearchParams({ path, full: String(full) })}`,
  )
export const fetchCommit = (key: string, hash: string) => getJson<CommitDetail>(`${project(key)}/commits/${hash}`)
/** old: 이름을 바꾼 파일의 옛 이름. full: 바뀐 곳 주변만이 아니라 파일 전체 */
export function fetchCommitDiff(key: string, hash: string, path: string, old: string | undefined, full: boolean) {
  const q = new URLSearchParams({ path, full: String(full) })
  if (old) q.set('old', old)
  return getJson<FileDiff>(`${project(key)}/commits/${hash}/diff?${q}`)
}

/**
 * 거절(409)은 본문에 이유가 담겨 와 예외 없이 { error } 로 돌려준다.
 * 이유가 없는 실패(422 · 500 등)도 성공으로 읽지 않게 상태 코드를 이유로 채운다.
 */
async function send<T>(method: string, path: string, body?: unknown): Promise<T & { error?: string }> {
  const response = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const parsed = (await response.json().catch(() => ({}))) as T & { error?: string }
  if (!response.ok && !parsed.error) parsed.error = `요청이 실패했습니다 (${response.status})`
  return parsed
}

export const addProject = (root: string, name?: string) =>
  send<ProjectRow>('POST', '/api/projects', { root, name: name || null })
export const pickFolder = () => send<{ path: string | null }>('POST', '/api/projects/pick-folder')
export const saveProjectSessions = (key: string, ids: string[]) =>
  send<{ ok?: true }>('PUT', `${project(key)}/sessions`, { ids })

export const fetchCommitSummary = (key: string, hash: string) =>
  getJson<CommitSummary>(`${project(key)}/commits/${hash}/summary`)
/** AI 요약을 만든다 (수십 초). 만들지 못한 이유는 error 로 */
export const makeCommitSummary = (key: string, hash: string) =>
  send<CommitSummary>('POST', `${project(key)}/commits/${hash}/summary`)

/** [갱신]: 원격의 새 커밋을 지금 받아 온다. 실패 이유는 error 로 (원격이 없을 때 포함) */
export const fetchRemote = (key: string) =>
  send<{ at: string | null; error: string | null }>('POST', `${project(key)}/git/fetch`)

export const saveProjectOrder = (keys: string[]) => send<{ ok?: true }>('PUT', '/api/projects/order', { keys })
export const renameProject = (key: string, name: string) => send<ProjectRow>('PATCH', project(key), { name })
/** 등록 해제. 기록은 남는다 (같은 폴더를 다시 추가하면 이어진다). */
export const unregisterProject = (key: string) => send<{ ok?: true }>('DELETE', project(key))
export const fetchFileHistory = (key: string, path: string) =>
  getJson<FileHistoryRow[]>(`${project(key)}/file-history?path=${encodeURIComponent(path)}`)
export const fetchFile = (key: string, path: string) =>
  getJson<FileContent>(`${project(key)}/file?path=${encodeURIComponent(path)}`)
/** 세션의 최근 질문 limit 개와 답 (세션 목록에 있는 세션만). */
export const fetchChat = (sessionId: string, limit: number) =>
  getJson<ChatResponse>(`/api/claude/sessions/${encodeURIComponent(sessionId)}/chat?limit=${limit}`)
