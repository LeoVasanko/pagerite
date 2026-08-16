import { fileURLToPath, URL } from 'node:url'
import fastapiVue from './vite-plugin-fastapi.js'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

const backendUrl = process.env.PAGERITE_BACKEND_URL || 'http://localhost:3200'

// Proxy content pages (/slug, /path/to/slug) to the FastAPI backend in dev.
// Excludes Vite internals (/@..., /src, /node_modules, /__...) and the
// backend's /_ prefix. /_api and /_f are handled by the fastapi-vue plugin;
// /_admin is proxied explicitly below.
const CONTENT_PROXY = '^\\/(?!_|@|src|node_modules|__)(?:[^./?]+(?:\\/[^./?]+)*)?(?:\\?.*)?$'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    fastapiVue({ paths: ["/_api", "/_f"] }),
    vue(),
    vueDevTools(),
  ],
  server: {
    proxy: {
      "/_admin": { target: backendUrl, changeOrigin: false },
      [CONTENT_PROXY]: { target: backendUrl, changeOrigin: false },
    },
  },
  build: {
    // Mirror the URL space in the build output: hashed files land under
    // frontend-build/_assets/ and the Frontend serves the build directory
    // at the site root (frontend/public/favicon.ico -> /favicon.ico).
    manifest: true,
    assetsDir: '_assets',
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./src/main.js', import.meta.url)),
        pagerite: fileURLToPath(new URL('./src/pagerite.js', import.meta.url)),
        pagerite_base: fileURLToPath(new URL('./src/assets/pagerite.css', import.meta.url)),
        pagerite_theme: fileURLToPath(new URL('./src/assets/themes/purple/theme.css', import.meta.url)),
      },
    },
  },
})
