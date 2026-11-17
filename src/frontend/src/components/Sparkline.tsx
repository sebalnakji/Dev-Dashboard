/**
 * 카드에 들어가는 작은 추세선.
 *
 * 차트 라이브러리를 쓰지 않는다. 축도 눈금도 범례도 없는 선 하나라,
 * 인라인 SVG 가 더 가볍고 카드 크기에 맞추기도 쉽다.
 * 축과 눈금이 필요한 상세 뷰에서는 Recharts 를 쓴다.
 */
export function Sparkline({
  values,
  className = '',
}: {
  values: (number | null)[]
  className?: string
}) {
  const points = values.filter((v): v is number => v != null)
  if (points.length < 2) {
    return <div className={`h-6 ${className}`} />
  }

  const width = 100
  const height = 24
  // 항상 0부터 그린다. 최솟값에 맞춰 늘리면 1% 흔들림이 큰 파도처럼 보인다.
  const top = Math.max(...points, 10)
  const step = width / (points.length - 1)
  const path = points
    .map((value, i) => `${(i * step).toFixed(1)},${(height - (value / top) * height).toFixed(1)}`)
    .join(' ')

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={`h-6 w-full ${className}`}
      aria-hidden
    >
      <polyline points={path} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}
