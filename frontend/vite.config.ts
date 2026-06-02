import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    // .ts.net を含めることで、Tailscale のドメイン（xxxx.ts.net）からのアクセスをすべて許可します。
    // コマンドを実行しないため、Vite の起動が高速になり、Tailscale の接続順序に関わらず確実に動作します。
    allowedHosts: ['.ts.net', 'localhost', '127.0.0.1'],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      }
    }
  },
  // @ts-ignore
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
})
