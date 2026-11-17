import { useEffect, type ReactNode } from 'react'

/** 화면 가운데 창. 바깥을 누르거나 Esc 로 닫는다. */
export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-10 flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="flex max-h-[85vh] w-full max-w-[520px] flex-col rounded-lg border border-edge bg-panel">
        <header className="flex items-center justify-between border-b border-edge px-5 py-3">
          <h3 className="text-[15px] font-semibold text-gray-100">{title}</h3>
          <button type="button" onClick={onClose} className="cursor-pointer text-gray-400 hover:text-gray-100">
            ✕
          </button>
        </header>
        <div className="min-h-0 overflow-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-edge px-5 py-3">{footer}</footer>}
      </div>
    </div>
  )
}
