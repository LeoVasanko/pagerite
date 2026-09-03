<script setup>
// Connection-state note for the WebSocket-backed panels (page/banner
// editors, analytics view): while the socket is connecting or waiting to
// reconnect the panel cannot load or save, and this says so. An empty
// text hides the note.
defineProps({ text: { type: String, default: '' } })
</script>

<template>
  <div v-if="text" class="conn-note" role="status">{{ text }}</div>
</template>

<style scoped>
.conn-note {
  padding: 0.2rem 1rem;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
  color: var(--muted);
  font-size: 0.8rem;
}
</style>
