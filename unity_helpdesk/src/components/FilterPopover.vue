<template>
  <!-- Plain position:absolute, NOT a Teleport — same reasoning as SortPopover:
       the Teleport on "+ Add column" exists only because that button sits inside
       .scroll-x (overflow-x: auto), which clips absolutely-positioned children.
       The Filter button lives in .toolbar-actions, outside every scroll
       container. Don't "fix" this. -->
  <div class="filter-pop" @click.stop>
    <!-- An empty `fields` registry disables "+ Add filter" below, which on its own
         looks identical to a working-but-unclickable control. Say which it is. -->
    <div v-if="!fields.length" class="filter-pop-empty">
      Filter fields aren't available yet — reload the page, and contact support
      if this persists.
    </div>
    <div v-else-if="!modelValue.length" class="filter-pop-empty">
      Choose a field to filter by.
    </div>

    <div
      v-for="(row, index) in modelValue"
      :key="`${row.key}-${index}`"
      class="filter-pop-row"
    >
      <select
        class="filter-pop-field"
        :value="row.key"
        @change="changeField(index, $event.target.value)"
      >
        <option v-for="f in fieldsFor(row.key)" :key="f.key" :value="f.key">
          {{ f.label }}
        </option>
      </select>

      <select
        class="filter-pop-op"
        :value="row.operator"
        @change="changeOperator(index, $event.target.value)"
      >
        <option v-for="op in operatorsFor(row.key)" :key="op" :value="op">
          {{ op }}
        </option>
      </select>

      <!-- Value control. Shape is driven by the field's `type` from the backend
           registry plus the operator's arity, so the two can never disagree. -->
      <span class="filter-pop-value">
        <!-- is set / is not set take no value at all -->
        <span
          v-if="arityFor(row.operator) === 'none'"
          class="filter-pop-novalue"
          >&mdash;</span
        >

        <!-- between: two bounds -->
        <template v-else-if="arityFor(row.operator) === 'two'">
          <input
            type="date"
            :value="pairValue(row.value, 0)"
            @change="changePair(index, 0, $event.target.value)"
          />
          <input
            type="date"
            :value="pairValue(row.value, 1)"
            :min="pairValue(row.value, 0)"
            @change="changePair(index, 1, $event.target.value)"
          />
        </template>

        <!-- in / not in: multi-select when we know the options, else a
             comma-separated text box so the filter is still usable. -->
        <template v-else-if="arityFor(row.operator) === 'many'">
          <select
            v-if="optionsFor(row.key).length"
            multiple
            :size="Math.min(optionsFor(row.key).length, 4)"
            class="filter-pop-multi"
            @change="changeMulti(index, $event.target)"
          >
            <option
              v-for="opt in optionsFor(row.key)"
              :key="opt.value"
              :value="opt.value"
              :selected="asList(row.value).includes(opt.value)"
            >
              {{ opt.label }}
            </option>
          </select>
          <input
            v-else
            type="text"
            placeholder="value, value"
            :value="asList(row.value).join(', ')"
            @change="changeCsv(index, $event.target.value)"
          />
        </template>

        <!-- single value -->
        <select
          v-else-if="optionsFor(row.key).length"
          :value="scalar(row.value)"
          @change="changeValue(index, $event.target.value)"
        >
          <option value="">Select…</option>
          <option
            v-for="opt in optionsFor(row.key)"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </option>
        </select>
        <select
          v-else-if="typeFor(row.key) === 'check'"
          :value="scalar(row.value)"
          @change="changeValue(index, $event.target.value)"
        >
          <option value="1">Yes</option>
          <option value="0">No</option>
        </select>
        <input
          v-else-if="
            typeFor(row.key) === 'date' || typeFor(row.key) === 'datetime'
          "
          type="date"
          :value="scalar(row.value)"
          @change="changeValue(index, $event.target.value)"
        />
        <input
          v-else-if="typeFor(row.key) === 'int'"
          type="number"
          :value="scalar(row.value)"
          @change="changeValue(index, $event.target.value)"
        />
        <input
          v-else
          type="text"
          :value="scalar(row.value)"
          @change="changeValue(index, $event.target.value)"
        />
      </span>

      <button
        class="filter-pop-remove"
        type="button"
        title="Remove this filter"
        @click="removeFilter(index)"
      >
        ×
      </button>
    </div>

    <div class="filter-pop-actions">
      <select
        class="filter-pop-add"
        :disabled="!unusedFields.length || modelValue.length >= max"
        :value="''"
        @change="addFilter($event.target.value)"
      >
        <option value="">
          {{ modelValue.length >= max ? `Max ${max} filters` : "+ Add filter" }}
        </option>
        <option v-for="f in unusedFields" :key="f.key" :value="f.key">
          {{ f.label }}
        </option>
      </select>
      <button
        v-if="modelValue.length"
        class="btn secondary filter-pop-clear"
        type="button"
        @click="clearFilters"
      >
        Clear filters
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  // [{ key, operator, value }] — `key` is a STRING, matching SortPopover. The
  // parent owns persistence; this component holds no state of its own.
  modelValue: { type: Array, required: true },
  // The backend's curated registry (unitySession.filterable_fields). Each entry
  // carries { key, label, field, type, operators, options?, doctype? }.
  fields: { type: Array, required: true },
  // fieldKey -> [{ value, label }] for Link fields the parent has already
  // loaded (agents, ticket types, teams, priorities). A field with no entry
  // falls back to a free-text control rather than an empty dropdown.
  optionsByKey: { type: Object, default: () => ({}) },
  max: { type: Number, default: 8 },
});
const emit = defineEmits(["update:modelValue", "close"]);

// Operators whose value control differs. Kept in step with FILTER_OPERATORS in
// unity_helpdesk.py — the backend rejects any pairing this UI shouldn't offer,
// so a drift here is a visible error, never a silently wrong query.
const NO_VALUE_OPS = ["is set", "is not set"];
const MULTI_OPS = ["in", "not in"];
const RANGE_OPS = ["between"];

const fieldMap = computed(() =>
  Object.fromEntries(props.fields.map((f) => [f.key, f]))
);
const usedKeys = computed(() => new Set(props.modelValue.map((r) => r.key)));
const unusedFields = computed(() =>
  props.fields.filter((f) => !usedKeys.value.has(f.key))
);

// A row's own field stays selectable alongside the unused ones, so you can't
// filter on the same field twice (which would AND two conditions on one column
// and usually mean "no results").
function fieldsFor(currentKey) {
  return props.fields.filter(
    (f) => f.key === currentKey || !usedKeys.value.has(f.key)
  );
}

function typeFor(key) {
  return fieldMap.value[key]?.type || "text";
}
function operatorsFor(key) {
  return fieldMap.value[key]?.operators || [];
}
function optionsFor(key) {
  const spec = fieldMap.value[key];
  if (!spec) return [];
  // Fixed Select choices ship with the registry; Link choices come from the
  // parent, which already loads agents/types/teams for other controls.
  if (spec.options?.length) {
    return spec.options.map((o) => ({ value: o, label: o }));
  }
  return props.optionsByKey[key] || [];
}
function arityFor(operator) {
  if (NO_VALUE_OPS.includes(operator)) return "none";
  if (MULTI_OPS.includes(operator)) return "many";
  if (RANGE_OPS.includes(operator)) return "two";
  return "one";
}

function asList(value) {
  if (Array.isArray(value)) return value.map((v) => String(v));
  return String(value ?? "").trim() ? [String(value)] : [];
}
function scalar(value) {
  return Array.isArray(value) ? String(value[0] ?? "") : String(value ?? "");
}
function pairValue(value, index) {
  return Array.isArray(value) ? String(value[index] ?? "") : "";
}

// A fresh value of the right SHAPE for the operator. Switching operator without
// this leaves e.g. an array behind on a scalar operator, which the backend then
// rejects — an error the user can't act on because the UI looks fine.
function blankValue(operator) {
  const arity = arityFor(operator);
  if (arity === "many") return [];
  if (arity === "two") return ["", ""];
  return "";
}

function emitRows(next) {
  emit("update:modelValue", next);
}
function patch(index, changes) {
  emitRows(
    props.modelValue.map((r, i) => (i === index ? { ...r, ...changes } : r))
  );
}

function changeField(index, key) {
  if (!key) return;
  const operators = operatorsFor(key);
  const current = props.modelValue[index]?.operator;
  // Keep the operator when the new field still supports it, so switching
  // "Subject like X" to "Raised By" doesn't silently reset to equals.
  const operator = operators.includes(current) ? current : operators[0];
  patch(index, { key, operator, value: blankValue(operator) });
}

function changeOperator(index, operator) {
  patch(index, { operator, value: blankValue(operator) });
}

function changeValue(index, value) {
  patch(index, { value });
}

function changePair(index, slot, value) {
  const pair = [...(props.modelValue[index]?.value || ["", ""])];
  pair[slot] = value;
  patch(index, { value: pair });
}

function changeMulti(index, select) {
  patch(index, {
    value: Array.from(select.selectedOptions).map((o) => o.value),
  });
}

function changeCsv(index, text) {
  patch(index, {
    value: text
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean),
  });
}

function removeFilter(index) {
  // Handled in-component so no index crosses the props boundary — same reason
  // as SortPopover.removeSort.
  emitRows(props.modelValue.filter((_, i) => i !== index));
}

function addFilter(key) {
  if (!key || props.modelValue.length >= props.max) return;
  const operator = operatorsFor(key)[0];
  if (!operator) return;
  emitRows([
    ...props.modelValue,
    { key, operator, value: blankValue(operator) },
  ]);
}

function clearFilters() {
  emitRows([]);
  emit("close");
}
</script>
