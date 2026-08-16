/**
 * FastAPI-Vue Vite Plugin
 * auto-upgrade@fastapi-vue-setup -- remove this if you edit the plugin
 *
 * Configures Vite for FastAPI backend integration:
 * - Proxies /api/* requests to the FastAPI backend
 * - Builds to the Python module's frontend-build directory
 *
 * Options:
 *   paths - Array of paths to proxy (default: ["/api"])
 */

export default function fastapiVue({ paths = ["/api"] } = {}) {
  const backendUrl = process.env.PAGERITE_BACKEND_URL || "http://localhost:3200"

  // Build proxy configuration for each path
  const proxy = {}
  for (const path of paths) {
    proxy[path] = {
      target: backendUrl,
      changeOrigin: false,
      ws: true,
    }
  }

  return {
    name: "vite-plugin-fastapi-pagerite",
    config: () => ({
      server: { proxy },
      build: {
        outDir: "../pagerite/frontend-build",
        emptyOutDir: true,
      },
    }),
  }
}
