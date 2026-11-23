import { createContext, useContext } from 'react'

/**
 * 이 칸이 든 탭이 지금 보이는가. 탭을 바꿀 때 지우지 않고 숨기기만 하므로(다시 그리고 다시 받지
 * 않게), 숨겨진 탭의 칸은 이것을 보고 주기 새로고침을 멈춘다. 다시 보이면 한 번 새로 받는다.
 */
export const TabActive = createContext(true)

export function useTabActive(): boolean {
  return useContext(TabActive)
}
