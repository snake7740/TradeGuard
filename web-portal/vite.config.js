import { readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// R-37：本地 dev 代理同样注入 bearer 令牌（与生产 nginx 反代同语义）。
// 令牌从仓库根 .env 读取（gitignore 排除，start_all 自动生成）；缺失时开发直通回退。
function devApiToken() {
  const envPath = resolve(__dirname, '../.env')
  if (!existsSync(envPath)) return ''
  const m = readFileSync(envPath, 'utf-8').match(/^TG_API_TOKEN=(.+)$/m)
  const token = m ? m[1].trim() : ''
  return token && token !== 'CHANGE_ME' ? token : ''
}

const token = devApiToken()
const proxyHeaders = token ? { Authorization: `Bearer ${token}` } : {}

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8200', changeOrigin: true, headers: proxyHeaders }  // 本地开发代理 web-api
    }
  }
})
