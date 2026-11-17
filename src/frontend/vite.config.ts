import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 개발 중에는 화면(5173)과 백엔드(8770)가 따로 뜬다.
    // /api 요청만 백엔드로 넘겨 브라우저에서는 같은 출처처럼 보이게 한다.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8770',
        changeOrigin: true,
      },
    },
  },
})
