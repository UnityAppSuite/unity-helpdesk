<template>
  <section class="page">
    <div class="settings-grid">
      <div class="settings-main">
        <div class="detail-section">
          <h3>User Profile</h3>
          <div class="detail-body">
            <div class="field-grid">
              <div class="field">
                <label>Name</label>
                <strong>{{ profile.full_name || profile.name }}</strong>
              </div>
              <div class="field">
                <label>Username</label>
                <strong>{{ profile.username || profile.name }}</strong>
              </div>
              <div class="field">
                <label>Email</label>
                <strong>{{ profile.email || "-" }}</strong>
              </div>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <h3>Account</h3>
          <div class="detail-body stack">
            <p class="muted">
              For security, your current password is never shown here.
            </p>
            <a class="btn" href="/app/user-profile">Change Password</a>
          </div>
        </div>

        <div v-if="canManageUnitySettings" class="detail-section">
          <h3>Unity Settings</h3>
          <div class="detail-body stack">
            <p v-if="settingsError" class="error">{{ settingsError }}</p>
            <p v-if="settingsSuccess" class="success-text">
              {{ settingsSuccess }}
            </p>
            <label>
              Email Thread Layout
              <select v-model="threadLayout">
                <option>Classic</option>
                <option>Chat Based</option>
              </select>
            </label>
            <small class="muted">
              Chat Based layout shows customer messages on the right and agent
              replies on the left.
            </small>
            <button
              class="btn"
              :disabled="settingsSaving"
              @click="saveSettings"
            >
              {{ settingsSaving ? "Saving..." : "Save Settings" }}
            </button>
          </div>
        </div>

        <div v-if="canManageUnitySettings" class="detail-section">
          <h3>Ticket Types</h3>
          <div class="detail-body stack">
            <p v-if="ticketTypeError" class="error">{{ ticketTypeError }}</p>
            <div class="inline-actions">
              <input
                v-model="newTicketType.name"
                type="text"
                placeholder="New ticket type name"
              />
              <select v-model="newTicketType.priority">
                <option value="">Default priority</option>
                <option>High</option>
                <option>Medium</option>
                <option>Low</option>
              </select>
            </div>
            <label>
              Description
              <textarea
                v-model="newTicketType.description"
                rows="3"
                placeholder="Optional description"
              ></textarea>
            </label>
            <button
              class="btn"
              :disabled="creatingTicketType || !newTicketType.name.trim()"
              @click="handleCreateTicketType"
            >
              {{ creatingTicketType ? "Adding..." : "Add Ticket Type" }}
            </button>

            <div class="scroll-x">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Priority</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="ticketType in ticketTypes" :key="ticketType.name">
                    <td>{{ ticketType.name }}</td>
                    <td>{{ ticketType.priority || "-" }}</td>
                  </tr>
                  <tr v-if="!ticketTypes.length">
                    <td colspan="2" class="empty">No ticket types found.</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      <aside v-if="canManageAgents" class="settings-side">
        <div class="detail-section">
          <button
            class="section-toggle settings-toggle"
            type="button"
            @click="agentsOpen = !agentsOpen"
          >
            <span>Agents</span>
            <small>{{ filteredAgents.length }} loaded</small>
            <strong>{{ agentsOpen ? "Hide" : "Show" }}</strong>
          </button>
          <div v-if="agentsOpen" class="detail-body stack">
            <div class="inline-actions">
              <input
                v-model="agentSearch"
                class="search"
                type="search"
                placeholder="Search agents"
              />
              <button
                class="btn secondary"
                :disabled="agentsLoading"
                @click="loadAgents"
              >
                Refresh
              </button>
            </div>

            <p v-if="agentsError" class="error">{{ agentsError }}</p>

            <label>
              Add Agent
              <select v-model="selectedUser">
                <option value="">Select a user</option>
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
            </label>
            <div class="inline-actions">
              <a
                class="btn secondary"
                href="/app/user/new-user-1"
                target="_blank"
              >
                New User
              </a>
              <button
                class="btn"
                :disabled="creatingAgent || !selectedUser"
                @click="handleCreateAgent"
              >
                {{ creatingAgent ? "Adding..." : "Add Agent" }}
              </button>
            </div>

            <div class="scroll-x">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>User / Email</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="agent in filteredAgents" :key="agent.name">
                    <td>{{ agent.full_name || agent.user || agent.name }}</td>
                    <td>{{ agent.user || agent.name }}</td>
                    <td>
                      <span
                        class="badge"
                        :class="agent.is_active ? 'green' : 'grey'"
                      >
                        {{ agent.is_active ? "Active" : "Inactive" }}
                      </span>
                    </td>
                  </tr>
                  <tr v-if="!agentsLoading && !filteredAgents.length">
                    <td colspan="3" class="empty">No agents found.</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </aside>
    </div>
  </section>
</template>

<script setup>
import { computed, inject, onMounted, reactive, ref, watch } from "vue";
import {
  createAgent,
  createTicketType,
  getAgentCandidates,
  getAgents,
  getTicketTypes,
  updateUnitySettings,
} from "../api";

const emit = defineEmits(["title"]);
const unitySession = inject("unitySession", {
  capabilities: {},
  settings: {},
});
const refreshUnitySession = inject("refreshUnitySession", () =>
  Promise.resolve()
);

const settingsSaving = ref(false);
const settingsError = ref("");
const settingsSuccess = ref("");
const threadLayout = ref(
  unitySession.settings?.unity_email_thread_layout || "Classic"
);

const agents = ref([]);
const candidates = ref([]);
const agentsLoading = ref(false);
const agentsError = ref("");
const agentsOpen = ref(false);
const agentSearch = ref("");
const selectedUser = ref("");
const creatingAgent = ref(false);

const ticketTypes = ref([]);
const ticketTypeError = ref("");
const creatingTicketType = ref(false);
const newTicketType = reactive({
  name: "",
  description: "",
  priority: "",
});

const profile = computed(() => unitySession);
const canManageUnitySettings = computed(
  () => !!unitySession.capabilities?.can_manage_unity_settings
);
const canManageAgents = computed(
  () => !!unitySession.capabilities?.can_manage_agents
);
const filteredAgents = computed(() => {
  const term = agentSearch.value.trim().toLowerCase();
  if (!term) return agents.value;
  return agents.value.filter((agent) =>
    [agent.name, agent.user, agent.full_name].some((value) =>
      String(value || "")
        .toLowerCase()
        .includes(term)
    )
  );
});

onMounted(async () => {
  emit("title", "Settings", "Super admin controls");
  await Promise.allSettled([loadAgents(), loadCandidates(), loadTicketTypes()]);
});

watch(
  () => unitySession.settings?.unity_email_thread_layout,
  (value) => {
    threadLayout.value = value || "Classic";
  }
);

async function loadAgents() {
  if (!canManageAgents.value) return;
  agentsLoading.value = true;
  agentsError.value = "";
  try {
    agents.value = await getAgents();
  } catch (err) {
    agentsError.value = err.message;
  } finally {
    agentsLoading.value = false;
  }
}

async function loadCandidates() {
  if (!canManageAgents.value) return;
  try {
    candidates.value = await getAgentCandidates();
  } catch {
    candidates.value = [];
  }
}

async function loadTicketTypes() {
  try {
    ticketTypes.value = await getTicketTypes();
  } catch {
    ticketTypes.value = [];
  }
}

async function saveSettings() {
  settingsSaving.value = true;
  settingsError.value = "";
  settingsSuccess.value = "";
  try {
    const updated = await updateUnitySettings({
      unity_email_thread_layout: threadLayout.value,
    });
    unitySession.settings = {
      ...unitySession.settings,
      unity_email_thread_layout:
        updated.unity_email_thread_layout || threadLayout.value,
    };
    await refreshUnitySession();
    settingsSuccess.value = "Unity Helpdesk settings updated.";
  } catch (err) {
    settingsError.value = err.message;
  } finally {
    settingsSaving.value = false;
  }
}

async function handleCreateAgent() {
  if (!selectedUser.value) return;
  creatingAgent.value = true;
  agentsError.value = "";
  try {
    await createAgent(selectedUser.value);
    selectedUser.value = "";
    await Promise.allSettled([loadAgents(), loadCandidates()]);
  } catch (err) {
    agentsError.value = err.message;
  } finally {
    creatingAgent.value = false;
  }
}

async function handleCreateTicketType() {
  if (!newTicketType.name.trim()) return;
  creatingTicketType.value = true;
  ticketTypeError.value = "";
  try {
    await createTicketType({
      name: newTicketType.name.trim(),
      description: newTicketType.description,
      priority: newTicketType.priority,
    });
    newTicketType.name = "";
    newTicketType.description = "";
    newTicketType.priority = "";
    await loadTicketTypes();
  } catch (err) {
    ticketTypeError.value = err.message;
  } finally {
    creatingTicketType.value = false;
  }
}
</script>
