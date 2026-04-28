<template>
  <section class="page">
    <div class="toolbar">
      <button class="btn" @click="handleAddAgent">Add Agent</button>
      <input
        v-model="search"
        class="search"
        type="search"
        placeholder="Search agents by name or email"
      />
      <button class="btn secondary" @click="load">Refresh</button>
    </div>
    <div class="table-shell">
      <div class="table-header">
        <strong>Agents</strong>
        <span>{{ filteredAgents.length }} agents</span>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-else-if="loading" class="empty">Loading agents...</p>
      <div v-else class="scroll-x">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>User / Email</th>
              <th>Role</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="agent in filteredAgents" :key="agent.name">
              <td>
                <span class="avatar">
                  {{ initials(agent.full_name || agent.user || agent.name) }}
                </span>
                {{ agent.full_name || agent.user || agent.name }}
              </td>
              <td>{{ agent.user || agent.name }}</td>
              <td>Agent</td>
              <td>
                <span class="badge" :class="agent.is_active ? 'green' : 'grey'">
                  {{ agent.is_active ? "Active" : "Inactive" }}
                </span>
              </td>
            </tr>
            <tr v-if="!loading && !filteredAgents.length">
              <td colspan="4" class="empty">No agents found.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Add Agent modal -->
    <div v-if="openCreate" class="modal-backdrop" @click.self="closeCreate">
      <section class="modal-card modal-card-sm">
        <div class="modal-header">
          <div>
            <strong>Add Agent</strong>
            <span
              >Select a user and create the HD Agent entry directly here.</span
            >
          </div>
          <button class="btn secondary" @click="closeCreate">Close</button>
        </div>
        <div class="modal-body stack">
          <p v-if="createError" class="error">{{ createError }}</p>
          <label>
            User
            <div class="input-with-action">
              <select v-model="selectedUser">
                <option value="">— Select user —</option>
                <option
                  v-for="candidate in candidates"
                  :key="candidate.name"
                  :value="candidate.name"
                >
                  {{ candidate.full_name || candidate.name }} ({{
                    candidate.email || candidate.name
                  }})
                </option>
              </select>
              <a
                href="/app/user/new-user-1"
                target="_blank"
                class="btn secondary input-action-btn"
                title="Create a new user in Frappe"
              >
                + New User
              </a>
            </div>
            <small v-if="candidatesLoading" class="muted"
              >Loading users...</small
            >
          </label>
        </div>
        <div class="modal-footer">
          <button class="btn secondary" @click="closeCreate">Cancel</button>
          <button
            class="btn"
            :disabled="creating || !selectedUser"
            @click="createAgent"
          >
            {{ creating ? "Creating..." : "Create Agent" }}
          </button>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import {
  createAgent as createHelpdeskAgent,
  getAgentCandidates,
  getAgents,
  initials,
} from "../api";

const emit = defineEmits(["title"]);
const agents = ref([]);
const candidates = ref([]);
const search = ref("");
const loading = ref(false);
const candidatesLoading = ref(false);
const error = ref("");
const openCreate = ref(false);
const selectedUser = ref("");
const creating = ref(false);
const createError = ref("");

const filteredAgents = computed(() => {
  const term = search.value.toLowerCase();
  if (!term) return agents.value;
  return agents.value.filter((agent) =>
    [agent.name, agent.user, agent.full_name, agent.user_image].some((value) =>
      (value || "").toLowerCase().includes(term)
    )
  );
});

onMounted(() => {
  emit("title", "Agents", "Manage active helpdesk agents");
  load();
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    agents.value = await getAgents();
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

async function loadCandidates() {
  candidatesLoading.value = true;
  try {
    candidates.value = await getAgentCandidates();
  } catch {
    candidates.value = [];
  } finally {
    candidatesLoading.value = false;
  }
}

function closeCreate() {
  openCreate.value = false;
  selectedUser.value = "";
  createError.value = "";
  creating.value = false;
}

async function handleAddAgent() {
  openCreate.value = true;
  if (!candidates.value.length) {
    await loadCandidates();
  }
}

async function createAgent() {
  if (!selectedUser.value) return;
  creating.value = true;
  createError.value = "";
  try {
    await createHelpdeskAgent(selectedUser.value);
    closeCreate();
    await load();
  } catch (err) {
    // If agent already exists, Frappe throws a DuplicateEntryError — treat as success
    if (err.message && err.message.toLowerCase().includes("duplicate")) {
      closeCreate();
      await load();
    } else {
      createError.value = err.message;
    }
  } finally {
    creating.value = false;
  }
}
</script>
