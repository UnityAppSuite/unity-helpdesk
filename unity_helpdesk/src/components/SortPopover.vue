<template>
  <!-- Plain position:absolute, NOT a Teleport. The Teleport used by the
       "+ Add column" menu exists only because that button sits inside
       .scroll-x (overflow-x: auto), which clips absolutely-positioned
       children. The Sort button lives in .toolbar-actions, outside every
       scroll container — same as the date-range popover, which has worked
       with plain absolute positioning all along. Don't "fix" this. -->
  <div class="sort-pop" @click.stop>
    <div v-if="!modelValue.length" class="sort-pop-empty">
      Choose a field to sort by.
    </div>

    <div
      v-for="(sort, index) in modelValue"
      :key="sort.key"
      class="sort-pop-row"
    >
      <select
        class="sort-pop-field"
        :value="sort.key"
        @change="changeField(index, $event.target.value)"
      >
        <option
          v-for="field in fieldsFor(sort.key)"
          :key="field.key"
          :value="field.key"
        >
          {{ field.label }}
        </option>
      </select>
      <select
        class="sort-pop-dir"
        :value="sort.direction"
        @change="changeDirection(index, $event.target.value)"
      >
        <option value="asc">Ascending</option>
        <option value="desc">Descending</option>
      </select>
      <button
        class="sort-pop-remove"
        type="button"
        title="Remove this sort"
        @click="removeSort(index)"
      >
        ×
      </button>
    </div>

    <div class="sort-pop-actions">
      <select
        class="sort-pop-add"
        :disabled="!unusedFields.length || modelValue.length >= max"
        :value="''"
        @change="addSort($event.target.value)"
      >
        <option value="">
          {{ modelValue.length >= max ? `Max ${max} fields` : "+ Add sort" }}
        </option>
        <option
          v-for="field in unusedFields"
          :key="field.key"
          :value="field.key"
        >
          {{ field.label }}
        </option>
      </select>
      <button
        v-if="modelValue.length"
        class="btn secondary sort-pop-clear"
        type="button"
        @click="clearSort"
      >
        Clear sort
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  // [{ key, direction }] — keys are STRINGS. Upstream's equivalent stores the
  // whole field object here, which is why its rows can't be keyed or removed
  // reliably; keeping it a string is what makes :key and splicing correct.
  modelValue: { type: Array, required: true },
  // The backend's curated registry (unitySession.sortable_fields).
  fields: { type: Array, required: true },
  max: { type: Number, default: 3 },
});
const emit = defineEmits(["update:modelValue", "close"]);

const usedKeys = computed(() => new Set(props.modelValue.map((s) => s.key)));
const unusedFields = computed(() =>
  props.fields.filter((f) => !usedKeys.value.has(f.key))
);

// A row's own field stays selectable alongside the ones nobody else is using,
// so you can't accidentally sort by the same field twice.
function fieldsFor(currentKey) {
  return props.fields.filter(
    (f) => f.key === currentKey || !usedKeys.value.has(f.key)
  );
}

function emitSorts(next) {
  emit("update:modelValue", next);
}

function changeField(index, key) {
  if (!key) return;
  // Direction is PRESERVED. Upstream resets it to ascending here, which quietly
  // undoes the user's choice every time they switch field.
  emitSorts(props.modelValue.map((s, i) => (i === index ? { ...s, key } : s)));
}

function changeDirection(index, direction) {
  emitSorts(
    props.modelValue.map((s, i) => (i === index ? { ...s, direction } : s))
  );
}

function removeSort(index) {
  // Handled in-component so no index crosses the props boundary — the parent
  // can't mismatch `event.index` against `data.index` the way upstream does.
  emitSorts(props.modelValue.filter((_, i) => i !== index));
}

function addSort(key) {
  if (!key || props.modelValue.length >= props.max) return;
  emitSorts([...props.modelValue, { key, direction: "asc" }]);
}

function clearSort() {
  // Emits [] — the parent and the backend both map "no sort" to the single
  // default (modified desc), so there's no ""-vs-default ambiguity to get wrong.
  emitSorts([]);
  emit("close");
}
</script>
