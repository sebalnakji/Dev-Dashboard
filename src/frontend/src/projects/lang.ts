/** 확장자 → Shiki 언어 이름 (CodeView 가 색을 고를 때). 없으면 글자 그대로 보인다. */
const EXT_LANG: Record<string, string> = {
  ts: 'ts', tsx: 'tsx', js: 'js', jsx: 'jsx', mjs: 'js', cjs: 'js',
  py: 'python', json: 'json', jsonc: 'jsonc', yaml: 'yaml', yml: 'yaml', toml: 'toml',
  md: 'markdown', css: 'css', html: 'html', xml: 'xml', svg: 'xml', sql: 'sql', ini: 'ini',
  sh: 'bash', bash: 'bash', ps1: 'powershell', psm1: 'powershell', bat: 'bat', cmd: 'bat',
  go: 'go', rs: 'rust', java: 'java', kt: 'kotlin', c: 'c', h: 'c', cpp: 'cpp', cs: 'csharp',
  vue: 'vue',
}

export function langOfPath(path: string): string {
  const name = path.split('/').pop()!.toLowerCase()
  if (name === 'dockerfile') return 'docker'
  return EXT_LANG[name.split('.').pop() ?? ''] ?? 'text'
}
