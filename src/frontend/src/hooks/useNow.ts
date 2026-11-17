import { useEffect, useState } from 'react'

/** "N초 전" 표시가 흐르도록 1초마다 현재 시각을 갱신한다. */
export function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return now
}
