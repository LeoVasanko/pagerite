<script setup>
import { formatCount } from './analytics/format.js'

defineProps({
  step: { type: Object, required: true },
  count: { type: Number, default: 0 },
})

defineEmits(['close'])
</script>

<template>
  <a class="trail-link"
     :href="step.path"
     :title="count > 1 ? `${step.title} (${count} hits)` : step.title"
     :target="step.external ? '_blank' : undefined"
     :rel="step.external ? 'noopener' : undefined"
     @click="(e) => { if (!step.external) $emit('close') }">
    <small v-if="count > 1" class="muted">{{ formatCount(count) }}×</small>
    <span>{{ step.slug }}</span>
  </a>
</template>
