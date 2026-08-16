import { fileURLToPath, URL } from 'node:url'
import fastapiVue from './vite-plugin-fastapi.js'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    fastapiVue(),
    vue(),
    vueDevTools(),
  ],
  build: {
    // JS entry only: no index.html in the build (it would shadow our /),
    // and a manifest so the backend can resolve hashed asset names.
    manifest: true,
    rollupOptions: {
      input: fileURLToPath(new URL('./src/main.js', import.meta.url)),
    },
  },
})
