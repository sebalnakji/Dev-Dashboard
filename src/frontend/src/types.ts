/**
 * 백엔드 API 응답 타입.
 *
 * 화면 계약(ADR 0004)을 그대로 옮긴 것이라, 백엔드 페이로드가 바뀌면
 * 여기부터 고쳐야 컴파일이 통과한다. 그게 이 파일을 두는 이유다.
 */

export type ServerStatus = 'connecting' | 'online' | 'error'

export interface GpuSummary {
  index: number
  /** nvidia-smi 가 [N/A] 를 내놓는 환경이 있어 null 이 올 수 있다. */
  utilization_percent: number | null
  memory_percent: number | null
  /** 아래 셋은 카드에만 온다. 차트의 점마다 반복될 필요가 없다. */
  name?: string
  memory_used_bytes?: number
  memory_total_bytes?: number
}

export interface DiskSummary {
  mount: string
  percent: number | null
  /** 아래 둘은 카드에만 온다. */
  used_bytes?: number
  total_bytes?: number
}

export interface ContainerCounts {
  running: number
  stopped: number
}

export interface ServerSummary {
  id: string
  label: string
  status: ServerStatus
  /** SSH 연결이나 자원 수집이 실패한 이유. */
  error: string | null
  /** SSH 는 멀쩡한데 docker 명령만 실패한 경우. */
  docker_error: string | null
  /** 이 값이 언제 것인지(epoch 초). 아직 수집 전이면 null. */
  ts: number | null
  cpu_percent: number | null
  /** 스펙 줄에 쓴다. 수집 전이면 null. */
  cpu_model: string | null
  cpu_cores: number | null
  mem_percent: number | null
  mem_used_bytes: number | null
  mem_total_bytes: number | null
  /** 가장 많이 찬 파티션 하나. */
  disk: DiskSummary | null
  gpus: GpuSummary[]
  containers: ContainerCounts | null
  /** 초기 로드에만 담겨 온다. 이후에는 브라우저가 들고 있으면서 이어붙인다. */
  cpu_spark?: (number | null)[]
}

/** 상세 차트의 한 점. */
export interface SamplePoint {
  ts: number
  cpu_percent: number | null
  mem_percent: number | null
  mem_used_bytes: number
  disks: DiskSummary[]
  net_rx_bps: number | null
  net_tx_bps: number | null
  gpus: GpuSummary[]
}

export interface SamplesResponse {
  server_id: string
  /** 점마다 반복되지 않도록 봉투에 한 번만 담겨 온다. */
  mem_total_bytes: number | null
  samples: SamplePoint[]
}

export interface ContainerRow {
  id: string
  name: string
  image: string
  state: string
  /** "Up 3 hours (healthy)" 처럼 사람이 읽는 문구. */
  status: string
  ports: string
  cpu_percent: number | null
  mem_used_bytes: number | null
}

export interface ContainersResponse {
  server_id: string
  ts: number | null
  containers: ContainerRow[]
}

/* --- Claude 세션 칸 · PC 메모리 칸 (ADR 0005) --- */

/**
 * on       백그라운드로 켜져 있음        off      꺼짐
 * terminal 터미널에서 연 세션(끌 수 없음)  starting · stopping · deleting  바뀌는 중
 */
export type SessionState = 'on' | 'off' | 'terminal' | 'starting' | 'stopping' | 'deleting'

/** 백그라운드 세션 한 줄. id 는 claude agents 의 목록 번호 (/clear 해도 그대로). */
export interface SessionRow {
  id: string
  /** Claude 쪽 이름. 비었으면 '폴더 이름 · 번호 앞 8자리' */
  name: string
  /** Claude 쪽 이름이 있는가 */
  named: boolean
  /** Claude 목록에서 이름을 잃어(번호로 나옴) 대화 기록의 이름을 보인다 */
  lostName: boolean
  /** null 이면 "분류 안 됨" */
  group: string | null
  order: number | null
  cwd: string
  state: SessionState
  /** 컨텍스트에 든 토큰 수 (마지막 답 기준). 기록이 없으면 null */
  tokens: number | null
  /** 대화를 압축한 횟수 */
  compactions: number
  /** 실패했을 때 그 이유. 뒤늦게 알게 되므로 목록 갱신에 실려 온다. */
  error?: string
  /** 같은 실패를 한 번만 띄우려고 함께 오는 실패 시각. */
  errorAt?: number
}

export interface SessionGroup {
  name: string
  collapsed: boolean
}

/** 터미널에서 연 세션. 목록 번호가 없어 보이기만 한다. */
export interface InteractiveSession {
  sessionId: string
  name: string
  cwd: string
  state: 'terminal'
  tokens: number | null
  compactions: number
}

/** [삭제]한 세션의 복원 정보 (30일). */
export interface DeletedSession {
  id: string
  name: string
  group: string | null
  cwd: string
  sessionId: string
  /** 초 (유닉스 시각) */
  deletedAt: number
  /** 대화 기록 파일이 남아 있어 복원할 수 있는가 */
  restorable: boolean
  restoring: boolean
}

export interface SessionsView {
  groups: SessionGroup[]
  sessions: SessionRow[]
  interactive: InteractiveSession[]
  deleted: DeletedSession[]
  /** 줄이 없는 실패 (복원 실패 등) */
  failures: { error: string; errorAt: number }[]
}

/** Claude Code 호환 시험 (devdash check-claude) */
export interface CompatStatus {
  /** 지금 깔린 Claude Code 버전. 못 읽으면 null */
  version: string | null
  /** 마지막으로 모든 항목을 통과한 버전 */
  passedVersion: string | null
  lastRun: { version: string | null; at: string; passed: boolean } | null
}

/** 새 세션의 폴더 후보. project 는 프로젝트 탭에 등록된 폴더 */
export interface FolderChoice {
  path: string
  project: boolean
}

/** 버튼 동작의 응답. 확인이 필요하면 confirm 의 말을 물어보고 force 로 다시 보낸다. */
export interface ActionResult {
  ok?: true
  error?: string
  confirm?: string
}

export interface PcMemory {
  total: number
  avail: number
  /** 메모리를 많이 쓰는 앱 순. 같은 앱의 프로세스는 합쳐져 있다. */
  apps: { name: string; bytes: number }[]
}

export interface ContainerLogs {
  server_id: string
  container_id: string
  name: string
  tail: number
  text: string
}

// --- 프로젝트 탭 (ADR 0007 · 0008) — 백엔드 devdash/records.py 와 1:1 ---

export type TaskStatus = 'todo' | 'doing' | 'done'

export interface ProjectRow {
  key: string
  name: string
  root: string
  /** 진행 중 할 일 수 (프로젝트 이름 옆에 보인다) */
  doing: number
}

export interface ProjectCandidate {
  root: string
  name: string
  /** 그 폴더에서 일하는 세션 이름들 */
  sessions: string[]
}

export interface FileRow {
  path: string
  /** 훅의 변경 기록에서만 — devdash 이전에 생긴 파일은 없다 */
  createdTask?: string | null
  lastTask?: string | null
  changes?: number
  /** git 에 올리지 않는 파일 (.env · CLAUDE.local.md …). 트리에 흐리게 'git 제외'로 보인다 (T-14) */
  ignored?: boolean
}

export interface FilesResponse {
  /** git 이 아닌 큰 폴더를 훑다가 상한에서 멈췄으면 true */
  truncated: boolean
  files: FileRow[]
}

export interface ProjectSession {
  id: string
  name: string
  group: string
  state: SessionState
  /** 켜진 세션이 지금 하는 일 (claude agents 의 state). 꺼졌으면 null */
  activity: 'working' | 'blocked' | 'done' | null
  /** 컨텍스트 토큰 수 · 압축 횟수 (T-21, 모니터링 탭의 세션 칸과 같은 값) */
  tokens: number | null
  compactions: number
  /** 최근 질문 20개에 대한 답이 끝난 시각들 (오래된 것부터). 새 답 수를 셀 때 쓴다 */
  replies: string[]
  lastAt: string | null
}

export interface SessionChoice {
  id: string
  name: string
  group: string
  member: boolean
  /** 다른 프로젝트 소속 (사람이 정했거나 작업 폴더가 그쪽). 고르면 이쪽으로 옮겨진다 */
  elsewhere: boolean
  /** 작업 폴더가 이 프로젝트라 자동으로 소속되는 세션 */
  auto: boolean
}

export interface TaskRow {
  number: number
  /** 같은 번호가 둘일 수 있어 화면은 파일 이름으로 가른다 */
  file: string
  title: string
  status: TaskStatus | ''
  session: string
  problems: string[]
}

export interface TaskDetail {
  number: number
  file: string
  title: string
  status: TaskStatus | ''
  session: string
  created: string
  sections: Record<'목표' | '한 일' | '남은 일', string>
  problems: string[]
  files: { path: string; created: boolean; changes: number }[]
}

export interface GitCommit {
  /** 전체 번호 (40자). 화면에는 앞 7자만 */
  hash: string
  at: string
  author: string
  subject: string
  tasks: number[]
  /** 원격에는 있고 아직 pull 하지 않은 커밋 (동료가 올린 것 등) */
  incoming: boolean
}

export interface GitHistory {
  git: boolean
  commits: GitCommit[]
  changes: { status: string; path: string }[]
  /** 내 가지가 따라가는 원격 가지 (예: origin/main). 없으면 null. git 이 아니면 오지 않는다 */
  upstream?: string | null
  /** 마지막 fetch 결과. 원격이 없으면 null (화면이 [갱신]을 감춘다) */
  fetch?: { at: string | null; error: string | null } | null
}

/** 커밋을 누르면 (T-10). status: A 추가 · M 수정 · D 삭제 · R 이름 바꿈 … */
export interface CommitFile {
  status: string
  path: string
  /** 이름을 바꾼 파일의 옛 이름 */
  old?: string
  /** 글이 아닌 파일은 null */
  added: number | null
  deleted: number | null
}

export interface CommitDetail {
  hash: string
  author: string
  at: string
  subject: string
  body: string
  tasks: number[]
  files: CommitFile[]
}

/** 커밋 AI 요약 (T-11). 아직 만들지 않았으면 text 가 null */
export interface CommitSummary {
  text: string | null
  model: string | null
  at: string | null
}

/** 파일 하나의 변경 덩어리. lines 는 [종류, 글] — ' ' 양쪽 같음 · '-' 이전에만 · '+' 이후에만 */
export interface DiffHunk {
  oldStart: number
  oldLines: number
  newStart: number
  newLines: number
  lines: [' ' | '-' | '+', string][]
}

export interface FileDiff {
  binary: boolean
  tooLarge: boolean
  hunks: DiffHunk[]
}

/** 작업 문서의 바뀐 파일 비교 (T-19). 무엇을 합친 비교인지: [T-n] 커밋들(짧은 번호, 오래된 것부터) + 커밋 안 된 변경.
 * 둘 다 없으면 비교할 것이 없다 — 화면이 [파일] 보기로 연다 */
export interface TaskFileDiff extends FileDiff {
  commits: string[]
  uncommitted: boolean
}

/** [파일] 보기: 고른 파일을 바꾼 할 일 (최근 순). 할 일 없이 고친 변경은 task 가 null. */
export interface FileHistoryRow {
  task: string | null
  changes: number
  created: boolean
  last: string | null
}

/** [파일] 보기에서 고른 문서의 내용 (등록된 프로젝트 폴더 안의 1MB 이하 UTF-8 글 파일). */
export interface FileContent {
  path: string
  text: string
}

/** [세션] 보기의 대화 한 칸 (T-8). 도구 사용은 숨기고 답마다 이름별 횟수만 온다. */
export interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  at: string | null
  tools?: Record<string, number>
}

export interface ChatResponse {
  messages: ChatMessage[]
  /** 더 이전 질문이 있나 ([더 보기]) */
  more: boolean
}
