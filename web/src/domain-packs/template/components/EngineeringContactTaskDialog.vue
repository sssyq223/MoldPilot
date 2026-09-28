<script setup lang="ts">
defineProps<{ stepId: string; proposal: unknown; archived?: boolean }>()
defineEmits<{ confirmed: []; dismissed: []; error: [message: string] }>()
</script>

<template>
  <!-- The template pack has no engineering-contact workflow. -->
  <span v-if="false" aria-hidden="true"></span>
</template>
