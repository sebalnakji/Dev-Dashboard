import { useEffect, useState } from 'react'

import { fetchAbilities } from '../api/client'
import type { PcAbilities } from '../types'

/** 이 OS 에서 되는 것. 알기 전에는 된다고 본다 (윈도우에서 버튼이 깜빡이지 않게) */
export function useAbilities(): PcAbilities {
  const [value, setValue] = useState<PcAbilities>({ terminal: true, pickFolder: true })
  useEffect(() => {
    let alive = true
    void fetchAbilities().then((v) => alive && setValue(v))
    return () => {
      alive = false
    }
  }, [])
  return value
}
