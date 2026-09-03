// Shared reconnect policy for the WebSockets (page/banner editors,
// analytics view, the activity channel). Two things trip a browser's
// WebSocket throttling, after which every socket to the host sits
// "pending" (never opens, never closes) for minutes:
//
//  1. A burst of simultaneous attempts — page load opens Vite's HMR
//     socket plus several of ours at the same moment, and every refresh
//     repeats the burst. socketSlot() spaces new sockets out.
//  2. Too-frequent retries — so failed attempts back off exponentially
//     (a few seconds, doubling to half a minute), reset only after a
//     connection stayed open long enough to count as healthy. A socket
//     that closes right after opening must NOT reset the backoff.
export function reconnectPolicy({ min = 2000, max = 30000, healthyAfter = 30000 } = {}) {
  let delay = min
  let openedAt = 0
  return {
    // Stamp a socket that just opened.
    opened() {
      openedAt = Date.now()
    },
    // The socket closed: the wait before the next attempt (up to 50%
    // jitter; the base doubles per failure). A healthy streak resets it.
    closed() {
      if (openedAt && Date.now() - openedAt >= healthyAfter) delay = min
      openedAt = 0
      const wait = Math.round(delay * (1 + Math.random() * 0.5))
      delay = Math.min(delay * 2, max)
      return wait
    },
  }
}

// Sockets created at the same moment (page load: Vite's HMR socket plus
// ours) read as one burst to the browser's throttling. Space new sockets
// out: each call reserves a slot a beat after the previous one.
let nextSlot = 0
export function socketSlot() {
  const now = Date.now()
  const wait = Math.max(0, nextSlot - now)
  nextSlot = Math.max(now, nextSlot) + 300
  return wait
}

// A socket still CONNECTING after this long counts as a failed attempt:
// browser throttling leaves sockets "pending" (no open, no close) for
// minutes, and without a watchdog the app would wait on one forever (the
// recurring empty editor). Closing it fires onclose, which reschedules
// through the policy's backoff — it never reconnects aggressively itself.
export function watchConnecting(ws, label) {
  return setTimeout(() => {
    if (ws.readyState === WebSocket.CONNECTING) {
      console.warn(`[pagerite] ${label} socket stuck connecting — closing it, retrying with backoff`)
      ws.close()
    }
  }, 10_000)
}
