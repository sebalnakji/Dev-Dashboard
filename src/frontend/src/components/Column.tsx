import type { ReactNode } from 'react'

/**
 * 대시보드의 한 칸. 제목 줄 + 내용.
 *
 * 세 칸의 제목 줄 높이를 똑같이(64px) 두어, 그 아래 선이 화면을 가로질러 일자로 잇게 한다.
 * 제목 좌우 여백(42px)은 내용 여백(36px)에 목록 줄 안쪽 여백(6px)을 더한 값이라,
 * 제목 글자가 아래 목록 글자와 세로로 맞는다.
 */
export function Column({
  title,
  side,
  children,
  className = '',
}: {
  title?: ReactNode
  /** 제목 줄 오른쪽 (버튼 · 상태 표시 등) */
  side?: ReactNode
  children?: ReactNode
  className?: string
}) {
  return (
    <section className={`min-w-0 ${className}`}>
      <header className="flex h-16 items-center justify-between gap-3 border-b border-divider px-4 min-[900px]:px-[42px]">
        <div className="flex min-w-0 items-baseline gap-2">{title}</div>
        {side}
      </header>
      <div className="px-2.5 py-[18px] min-[900px]:px-9">{children}</div>
    </section>
  )
}

export function ColumnTitle({ children }: { children: ReactNode }) {
  return <h2 className="text-[15px] font-semibold text-gray-100">{children}</h2>
}
