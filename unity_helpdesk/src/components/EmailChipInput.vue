<template>
  <div
    class="recipient-multiselect email-chip-input"
    :class="{ 'email-chip-input--compact': compact }"
    @click="focusInput"
  >
    <span
      v-for="email in modelValue"
      :key="email"
      class="recipient-chip"
      :class="{ 'recipient-chip-warn': !isValidEmail(email) }"
      :title="isValidEmail(email) ? email : `${email} — not a valid address`"
    >
      <span class="recipient-chip-label">{{ email }}</span>
      <button
        type="button"
        class="recipient-chip-remove"
        :aria-label="`Remove ${email}`"
        @click.stop="remove(email)"
      >
        ×
      </button>
    </span>
    <div class="recipient-input-wrap">
      <input
        ref="inputRef"
        v-model="draft"
        type="text"
        class="recipient-input"
        :placeholder="modelValue.length ? '' : placeholder"
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
        @keydown="onKeydown"
        @paste="onPaste"
        @blur="commitDraft"
      />
    </div>
  </div>
</template>

<script setup>
// Multi-address input rendered as chips. Emits a plain array of lowercased,
// de-duplicated strings. Addresses are committed on Enter, comma, semicolon,
// Tab, blur, or paste (which may carry a whole list). Backspace on an empty
// draft pops the last chip, mail-client style. Invalid entries are KEPT and
// highlighted rather than dropped, so a typo is visible and fixable instead of
// silently vanishing — the backend rejects the send until it's corrected.
import { ref } from "vue";

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  placeholder: { type: String, default: "" },
  compact: { type: Boolean, default: false },
});
const emit = defineEmits(["update:modelValue"]);

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const inputRef = ref(null);
const draft = ref("");

function isValidEmail(value) {
  return EMAIL_REGEX.test(value);
}

function focusInput() {
  inputRef.value?.focus();
}

function splitAddresses(text) {
  return String(text || "")
    .split(/[,;\s]+/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

function add(addresses) {
  const next = [...props.modelValue];
  for (const email of addresses) {
    if (!next.includes(email)) next.push(email);
  }
  if (next.length !== props.modelValue.length) emit("update:modelValue", next);
}

function remove(email) {
  emit(
    "update:modelValue",
    props.modelValue.filter((e) => e !== email)
  );
}

function commitDraft() {
  const parts = splitAddresses(draft.value);
  draft.value = "";
  if (parts.length) add(parts);
}

function onKeydown(e) {
  if (e.key === "Enter" || e.key === "," || e.key === ";") {
    e.preventDefault();
    commitDraft();
    return;
  }
  if (e.key === "Tab" && draft.value.trim()) {
    // Commit but let focus move on naturally.
    commitDraft();
    return;
  }
  if (e.key === "Backspace" && !draft.value && props.modelValue.length) {
    e.preventDefault();
    emit("update:modelValue", props.modelValue.slice(0, -1));
  }
}

function onPaste(e) {
  const text = e.clipboardData?.getData("text") || "";
  if (!/[,;\s]/.test(text.trim())) return; // single token: let it type normally
  e.preventDefault();
  add(splitAddresses(`${draft.value} ${text}`));
  draft.value = "";
}

defineExpose({ focus: focusInput, commit: commitDraft });
</script>
