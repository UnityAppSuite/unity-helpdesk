<template>
  <!-- Deliberately NOT a native <select multiple>: there a plain click
       REPLACES the whole selection, which silently wiped a team's members.
       Every click here toggles exactly one person. Don't "simplify" this. -->
  <div class="member-picker">
    <div v-if="modelValue.length" class="member-chips">
      <span v-for="user in modelValue" :key="user" class="member-chip">
        {{ labelFor(user) }}
        <button
          type="button"
          class="member-chip-remove"
          :title="`Remove ${labelFor(user)}`"
          @click="toggle(user)"
        >
          ×
        </button>
      </span>
    </div>
    <input
      v-model="search"
      type="text"
      class="member-search"
      :placeholder="placeholder"
    />
    <ul class="member-options">
      <li
        v-for="agent in candidates"
        :key="agent.name"
        class="member-option"
        :class="{ selected: modelValue.includes(agent.name) }"
        @click="toggle(agent.name)"
      >
        <input
          type="checkbox"
          :checked="modelValue.includes(agent.name)"
          tabindex="-1"
        />
        <span>{{ agent.full_name || agent.name }}</span>
      </li>
      <!-- "no agents at all" and "no agents matching the search" look
           identical otherwise, and the first one means the agent list never
           loaded (loadAgents() early-returns without can_manage_agents). -->
      <li v-if="!agents.length" class="member-option disabled">
        No agents loaded yet.
      </li>
      <li v-else-if="!candidates.length" class="member-option disabled">
        No match
      </li>
    </ul>
    <small class="muted">
      {{ modelValue.length }} selected, click to add or remove.
    </small>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";

const props = defineProps({
  // Selected USER ids, in click order. HD Agent.name IS the user id, which is
  // why the options list keys off agent.name. Never mutated in place: every
  // change emits a fresh array (vue/no-mutating-props).
  modelValue: { type: Array, required: true },
  // Rows from getAgents(): { name, full_name, ... }.
  agents: { type: Array, default: () => [] },
  placeholder: { type: String, default: "Search agents to add..." },
});
const emit = defineEmits(["update:modelValue"]);

// Instance-local: the edit row and the create form each get their own search
// box, and both reset for free, because each lives inside a v-if and closing
// it unmounts the component.
const search = ref("");

const candidates = computed(() => {
  const q = search.value.trim().toLowerCase();
  const list = props.agents || [];
  if (!q) return list;
  return list.filter(
    (a) =>
      String(a.full_name || "")
        .toLowerCase()
        .includes(q) ||
      String(a.name || "")
        .toLowerCase()
        .includes(q)
  );
});

function labelFor(user) {
  return props.agents.find((a) => a.name === user)?.full_name || user;
}

function toggle(user) {
  const next = [...props.modelValue];
  const i = next.indexOf(user);
  if (i === -1) next.push(user);
  else next.splice(i, 1);
  emit("update:modelValue", next);
}
</script>
