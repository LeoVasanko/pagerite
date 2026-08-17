import { fileURLToPath, URL } from 'node:url'
import { readdirSync } from 'node:fs'
import fastapiVue from './vite-plugin-fastapi.js'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

const backendUrl = process.env.PAGERITE_BACKEND_URL || 'http://localhost:3200'

// Every theme directory ships its theme.css as a separate build entry, so
// the backend can link base and theme stylesheets independently.
const themesDir = fileURLToPath(new URL('./src/assets/themes', import.meta.url))
const themeInputs = Object.fromEntries(
  readdirSync(themesDir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => [`theme_${d.name}`, `${themesDir}/${d.name}/theme.css`]),
)

// Proxy content pages (/slug, /path/to/slug) to the FastAPI backend in dev.
// Excludes Vite internals (/@..., /src, /node_modules, /__...) and the
// backend's /_ prefix. /_api and /_f are handled by the fastapi-vue plugin.
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
      [CONTENT_PROXY]: { target: backendUrl, changeOrigin: false },
    },
  },
  appType: 'mpa', // no SPA fallback; every HTML page is served by FastAPI
  build: {
    // Mirror the URL space in the build output: hashed files land under
    // frontend-build/_assets/ and the Frontend serves the build directory
    // at the site root (frontend/public/favicon.ico -> /favicon.ico).
    manifest: true,
    assetsDir: '_assets',
    rollupOptions: {
      // main.js is dynamic-imported by the public page (pagerite.js) for
      // its openEditor/closeEditor exports — keep them in the bundle
      // (app builds strip unused entry exports by default).
      preserveEntrySignatures: 'exports-only',
      input: {
        main: fileURLToPath(new URL('./src/main.js', import.meta.url)),
        pagerite: fileURLToPath(new URL('./src/pagerite.js', import.meta.url)),
        pagerite_base: fileURLToPath(new URL('./src/assets/pagerite.css', import.meta.url)),
        ...themeInputs,
      },
    },
  },
})
