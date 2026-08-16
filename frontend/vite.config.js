import { fileURLToPath, URL } from 'node:url'
import fastapiVue from './vite-plugin-fastapi.js'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

const backendUrl = process.env.PAGERITE_BACKEND_URL || 'http://localhost:3200'

// Proxy content pages (/slug, /path/to/slug) to the FastAPI backend in dev.
// Excludes Vite internals (/@..., /src, /node_modules, /__...) and the
// backend's /_ prefix. /_/api and /_/f are handled by the fastapi-vue plugin;
// /_/admin is proxied explicitly below.
const CONTENT_PROXY = '^\\/(?!_|@|src|node_modules|__)(?:[^./?]+(?:\\/[^./?]+)*)?(?:\\?.*)?$'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    fastapiVue({ paths: ["/_/api", "/_/f"] }),
    vue(),
    vueDevTools(),
  ],
  server: {
    proxy: {
      "/_/admin": { target: backendUrl, changeOrigin: false },
      [CONTENT_PROXY]: { target: backendUrl, changeOrigin: false },
    },
  },
  build: {
    // Emit hashed assets at the root of frontend-build so the backend can
    // serve them under /_/assets/{file} without a nested /assets directory.
    manifest: true,
    assetsDir: '',
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./src/main.js', import.meta.url)),
        pagerite: fileURLToPath(new URL('./src/pagerite.js', import.meta.url)),
      },
    },
  },
})
