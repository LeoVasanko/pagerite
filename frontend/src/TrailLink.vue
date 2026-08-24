<script setup>
import { computed } from 'vue'
import { formatCount, formatReadTime } from './analytics/format.js'

const props = defineProps({
  step: { type: Object, required: true },
  count: { type: Number, default: 0 },
})

defineEmits(['close'])

const hasError = computed(() => props.step.status >= 400)

const title = computed(() => {
  const parts = [props.step.title]
  if (props.step.readSeconds > 0) {
    parts.push(formatReadTime(props.step.readSeconds))
  }
  if (hasError.value) {
    parts.push(`${props.step.status}`)
  }
  return parts.filter(Boolean).join(' — ')
})
</script>

<template>
  <a class="trail-link"
     :class="{ error: hasError }"
     :href="step.path"
     :title="title"
     :target="step.external ? '_blank' : undefined"
     :rel="step.external ? 'noopener' : undefined"
     @click="(e) => { if (!step.external) $emit('close') }">
    <small v-if="count > 1" class="muted">{{ formatCount(count) }}×</small>
    <span>{{ step.slug }}</span>
  </a>
</template>
