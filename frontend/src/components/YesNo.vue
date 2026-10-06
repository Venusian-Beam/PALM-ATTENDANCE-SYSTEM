<script setup>
defineProps({
  question: { type: String, required: true },
})
defineEmits(['answer'])
</script>

<template>
  <div class="yesno">
    <div class="question">{{ question }}</div>
    <div class="buttons">
      <button class="btn btn-outlined big" @click="$emit('answer', 'no')">No</button>
      <button class="btn btn-filled big" @click="$emit('answer', 'yes')">Yes</button>
    </div>
    <div class="hint">or nod / shake your head</div>
  </div>
</template>

<style scoped>
.yesno {
  text-align: center;
}
.question {
  font-family: var(--font-display);
  font-size: clamp(22px, 2.4vw, 30px);
  line-height: 1.3;
  margin-bottom: 20px;
}
.buttons {
  display: flex;
  justify-content: center;
  gap: 16px;
}
.big {
  height: 64px;
  min-width: 180px;
  border-radius: 32px;
  font-size: 22px;
}
.hint {
  margin-top: 12px;
  font-size: 14px;
  color: var(--text-2);
}
</style>
