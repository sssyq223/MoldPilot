<script setup lang="ts">
defineProps<{size?:number}>()
</script>

<template>
 <svg class="moldpilot-logo" :width="size||32" :height="size||32" viewBox="0 0 64 64" fill="none" role="img" aria-label="MoldPilot logo" xmlns="http://www.w3.org/2000/svg">
  <rect x="3" y="3" width="58" height="58" rx="16" fill="currentColor"/>
  <path d="M18 43V21h7l7 10 7-10h7v22" stroke="var(--logo-line,#fff)" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M25 43V32l7 9 7-9v11" stroke="var(--logo-line,#fff)" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M16 17h10M38 17h10M32 12v8" stroke="var(--logo-line,#fff)" stroke-width="4" stroke-linecap="round"/>
  <circle cx="18" cy="17" r="3.2" fill="var(--logo-line,#fff)"/>
  <circle cx="46" cy="17" r="3.2" fill="var(--logo-line,#fff)"/>
  <path d="M46 41l5-5m0 0h-8m8 0v8" stroke="var(--logo-accent,#9fd0ff)" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
 </svg>
</template>
