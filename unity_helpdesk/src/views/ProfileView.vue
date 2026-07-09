<template>
  <section class="page settings-page">
    <div class="settings-grid">
      <!-- ============================ MAIN COLUMN ============================ -->
      <div class="settings-main">
        <SettingsCard
          title="User Profile"
          subtitle="Your account info as it appears on tickets and replies."
          :model-value="sections.profile"
          @update:model-value="(v) => (sections.profile = v)"
        >
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
        </SettingsCard>

        <SettingsCard
          v-if="canManageUnitySettings"
          title="Unity Settings"
          subtitle="Workspace-wide preferences for every agent."
          :model-value="sections.unity"
          @update:model-value="(v) => (sections.unity = v)"
        >
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
            Chat Based shows customer messages on the right, agent replies on
            the left.
          </small>
          <button class="btn" :disabled="settingsSaving" @click="saveSettings">
            {{ settingsSaving ? "Saving..." : "Save Settings" }}
          </button>
        </SettingsCard>

        <SettingsCard
          v-if="canManageUnitySettings"
          title="Ticket Types"
          subtitle="Categories Frappe Helpdesk uses to classify tickets. Each can have a default priority."
          :model-value="sections.ticketTypes"
          @update:model-value="(v) => (sections.ticketTypes = v)"
        >
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
                  <th v-if="colorColumnAvailable" style="width: 90px">Color</th>
                  <th>
                    Keywords
                    <small class="muted" style="font-weight: normal">
                      — incoming tickets auto-assign to the type whose keyword
                      matches their subject/body.
                    </small>
                  </th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="ticketType in ticketTypes" :key="ticketType.name">
                  <td>{{ ticketType.name }}</td>
                  <td>{{ ticketType.priority || "-" }}</td>
                  <td v-if="colorColumnAvailable">
                    <template v-if="editingTicketType.name === ticketType.name">
                      <input
                        v-model="editingTicketType.colorInput"
                        type="color"
                        class="ticket-type-color-input"
                        title="Pick color for this ticket type"
                      />
                    </template>
                    <template v-else>
                      <span class="ticket-type-pill">
                        <span
                          class="ticket-type-dot"
                          :style="{
                            background: ticketType.custom_color || '#94a3b8',
                          }"
                        ></span>
                        <span v-if="!ticketType.custom_color" class="muted"
                          >none</span
                        >
                      </span>
                    </template>
                  </td>
                  <td>
                    <template v-if="editingTicketType.name === ticketType.name">
                      <input
                        v-model="editingTicketType.keywordsInput"
                        type="text"
                        placeholder="comma, separated, keywords"
                        style="min-width: 240px"
                      />
                    </template>
                    <template v-else>
                      <span
                        v-if="ticketType.keywords && ticketType.keywords.length"
                      >
                        <span
                          v-for="kw in ticketType.keywords"
                          :key="kw"
                          class="badge grey"
                          style="margin-right: 4px"
                        >
                          {{ kw }}
                        </span>
                      </span>
                      <span v-else class="muted">—</span>
                    </template>
                  </td>
                  <td class="actions-cell">
                    <template v-if="editingTicketType.name === ticketType.name">
                      <button
                        class="btn small"
                        :disabled="savingTicketType"
                        @click="saveTicketTypeEdit"
                      >
                        {{ savingTicketType ? "Saving..." : "Save" }}
                      </button>
                      <button
                        class="btn small secondary"
                        :disabled="savingTicketType"
                        @click="cancelTicketTypeEdit"
                      >
                        Cancel
                      </button>
                    </template>
                    <template v-else>
                      <button
                        class="btn small secondary"
                        @click="startTicketTypeEdit(ticketType)"
                      >
                        Edit
                      </button>
                    </template>
                  </td>
                </tr>
                <tr v-if="!ticketTypes.length">
                  <td :colspan="colorColumnAvailable ? 5 : 4" class="empty">
                    No ticket types found.
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </SettingsCard>

        <!-- ============================ CATEGORIES ============================ -->
        <SettingsCard
          v-if="canManageUnitySettings"
          title="Reply Template Categories"
          subtitle="Group saved replies (e.g. Refunds, Admissions, Fees). Agents use these to filter the Templates picker in the editor."
          :model-value="sections.categories"
          @update:model-value="(v) => (sections.categories = v)"
        >
          <p v-if="categoryError" class="error">{{ categoryError }}</p>

          <div class="inline-actions">
            <input
              v-model="newCategory.title"
              type="text"
              placeholder="New category name (e.g. Refunds)"
            />
            <input
              v-model="newCategory.color"
              type="color"
              title="Color (optional)"
              class="color-swatch-input"
            />
            <button
              class="btn"
              :disabled="creatingCategory || !newCategory.title.trim()"
              @click="handleCreateCategory"
            >
              {{ creatingCategory ? "Adding..." : "Add Category" }}
            </button>
          </div>
          <label>
            Description
            <textarea
              v-model="newCategory.description"
              rows="2"
              placeholder="Optional — what kind of replies live in this category?"
            ></textarea>
          </label>

          <div class="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Color</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="cat in categories" :key="cat.name">
                  <td>
                    <span v-if="editingCategory.name !== cat.name">{{
                      cat.title
                    }}</span>
                    <input v-else v-model="editingCategory.title" type="text" />
                  </td>
                  <td>
                    <span
                      v-if="editingCategory.name !== cat.name && cat.color"
                      class="color-swatch"
                      :style="{ backgroundColor: cat.color }"
                      :title="cat.color"
                    ></span>
                    <input
                      v-else-if="editingCategory.name === cat.name"
                      v-model="editingCategory.color"
                      type="color"
                      class="color-swatch-input"
                    />
                    <span v-else class="muted">—</span>
                  </td>
                  <td>
                    <span
                      class="badge"
                      :class="cat.is_active ? 'green' : 'grey'"
                    >
                      {{ cat.is_active ? "Active" : "Inactive" }}
                    </span>
                  </td>
                  <td class="actions-cell">
                    <template v-if="editingCategory.name === cat.name">
                      <button
                        class="btn small"
                        :disabled="savingCategory"
                        @click="saveCategoryEdit"
                      >
                        Save
                      </button>
                      <button
                        class="btn small secondary"
                        @click="cancelCategoryEdit"
                      >
                        Cancel
                      </button>
                    </template>
                    <template v-else>
                      <button
                        class="btn small secondary"
                        @click="startCategoryEdit(cat)"
                      >
                        Edit
                      </button>
                      <button
                        class="btn small danger"
                        @click="handleDeleteCategory(cat)"
                      >
                        Delete
                      </button>
                    </template>
                  </td>
                </tr>
                <tr v-if="!categories.length">
                  <td colspan="4" class="empty">No categories yet.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </SettingsCard>

        <!-- ============================ TEMPLATES ============================ -->
        <SettingsCard
          v-if="canManageUnitySettings"
          title="Reply Templates"
          subtitle="Saved static replies agents can insert from the TinyMCE editor. Body and subject are inserted as-is — agents can edit after inserting."
          :model-value="sections.templates"
          @update:model-value="(v) => (sections.templates = v)"
        >
          <p v-if="templateError" class="error">{{ templateError }}</p>

          <div class="inline-actions">
            <label class="grow">
              Filter by category
              <select v-model="templateFilterCategory" @change="loadTemplates">
                <option value="">All categories</option>
                <option
                  v-for="cat in categories"
                  :key="cat.name"
                  :value="cat.name"
                >
                  {{ cat.title }}
                </option>
              </select>
            </label>
            <button
              v-if="!templateFormOpen"
              class="btn"
              @click="openTemplateForm()"
            >
              + Add Template
            </button>
          </div>

          <div v-if="templateFormOpen" class="template-form">
            <h4 style="margin: 0">
              {{ templateForm.name ? "Edit template" : "New template" }}
            </h4>
            <label>
              Title
              <input
                v-model="templateForm.title"
                type="text"
                placeholder="e.g. Refund acknowledged"
              />
            </label>
            <div class="inline-actions">
              <label class="grow">
                Category
                <select v-model="templateForm.category">
                  <option value="">— select —</option>
                  <option
                    v-for="cat in categories"
                    :key="cat.name"
                    :value="cat.name"
                  >
                    {{ cat.title }}
                  </option>
                </select>
              </label>
              <label class="grow">
                Language
                <select v-model="templateForm.language">
                  <option>English</option>
                  <option>Hindi</option>
                  <option>Marathi</option>
                </select>
              </label>
            </div>
            <label>
              Default subject <small class="muted">(optional)</small>
              <input
                v-model="templateForm.subject_template"
                type="text"
                placeholder="e.g. Re: refund acknowledged"
              />
            </label>
            <label>
              Message body
              <TinyMceEditor
                v-model="templateForm.message"
                :min-height="220"
                placeholder="Compose the reply. HTML allowed. Inserted as-is at the cursor."
              />
            </label>
            <div class="inline-actions">
              <button
                class="btn"
                :disabled="savingTemplate || !templateFormValid"
                @click="saveTemplate"
              >
                {{
                  savingTemplate
                    ? "Saving..."
                    : templateForm.name
                    ? "Save changes"
                    : "Create template"
                }}
              </button>
              <button class="btn secondary" @click="closeTemplateForm">
                Cancel
              </button>
            </div>
          </div>

          <div class="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Category</th>
                  <th>Language</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="tpl in templates" :key="tpl.name">
                  <td>{{ tpl.title }}</td>
                  <td>{{ tpl.category || "-" }}</td>
                  <td>{{ tpl.language || "English" }}</td>
                  <td>
                    <span
                      class="badge"
                      :class="tpl.is_active ? 'green' : 'grey'"
                    >
                      {{ tpl.is_active ? "Active" : "Inactive" }}
                    </span>
                  </td>
                  <td class="actions-cell">
                    <button
                      class="btn small secondary"
                      :disabled="loadingTemplateBody === tpl.name"
                      @click="startTemplateEdit(tpl)"
                    >
                      Edit
                    </button>
                    <button
                      class="btn small danger"
                      @click="handleDeleteTemplate(tpl)"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
                <tr v-if="!templates.length">
                  <td colspan="5" class="empty">No templates yet.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </SettingsCard>
      </div>

      <!-- ============================ SIDE COLUMN: AGENTS ============================ -->
      <aside v-if="canManageAgents" class="settings-side">
        <SettingsCard
          title="Agents"
          :subtitle="`${filteredAgents.length} loaded · who can answer tickets in Unity Helpdesk.`"
          :model-value="sections.agents"
          @update:model-value="(v) => (sections.agents = v)"
        >
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
            <div class="assignee-combobox">
              <input
                v-model="candidateQuery"
                type="text"
                placeholder="Search a user to add…"
                autocomplete="off"
                @input="onCandidateInput"
                @focus="onCandidateFocus"
                @blur="onCandidateBlur"
              />
              <ul v-if="candidateOpen" class="user-suggestions">
                <li
                  v-for="candidate in candidateMatches"
                  :key="candidate.name"
                  @mousedown.prevent="pickCandidate(candidate)"
                >
                  <span>{{ candidate.full_name || candidate.name }}</span>
                  <small>{{ candidate.email || candidate.name }}</small>
                </li>
                <li v-if="!candidateMatches.length" class="disabled">
                  <small class="muted">No users match</small>
                </li>
              </ul>
            </div>
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
        </SettingsCard>
      </aside>
    </div>
  </section>
</template>

<script setup>
import { computed, h, inject, onMounted, reactive, ref, watch } from "vue";
import {
  createAgent,
  createReplyTemplate,
  createReplyTemplateCategory,
  createTicketType,
  deleteReplyTemplate,
  deleteReplyTemplateCategory,
  getAgentCandidates,
  getAgents,
  getReplyTemplateDoc,
  getTicketTypes,
  listReplyTemplateCategoriesAdmin,
  listReplyTemplatesAdmin,
  listTicketTypesWithKeywords,
  updateReplyTemplate,
  updateReplyTemplateCategory,
  updateTicketTypeColor,
  updateTicketTypeKeywords,
  updateUnitySettings,
} from "../api";
import TinyMceEditor from "@desk/components/TinyMceEditor.vue";

// Local collapsible card — keeps the template uniform without a separate file.
const SettingsCard = {
  name: "SettingsCard",
  props: {
    title: String,
    subtitle: String,
    modelValue: { type: Boolean, default: true },
  },
  emits: ["update:modelValue"],
  setup(props, { emit, slots }) {
    return () =>
      h("section", { class: "detail-section settings-card" }, [
        h(
          "button",
          {
            class: "settings-card__header",
            type: "button",
            "aria-expanded": props.modelValue,
            onClick: () => emit("update:modelValue", !props.modelValue),
          },
          [
            h("div", { class: "settings-card__heading" }, [
              h("h3", null, props.title),
              props.subtitle
                ? h(
                    "small",
                    { class: "muted settings-card__subtitle" },
                    props.subtitle
                  )
                : null,
            ]),
            h(
              "span",
              { class: "settings-card__chevron", "aria-hidden": "true" },
              props.modelValue ? "▲" : "▼"
            ),
          ]
        ),
        props.modelValue
          ? h(
              "div",
              { class: "detail-body stack settings-card__body" },
              slots.default ? slots.default() : []
            )
          : null,
      ]);
  },
};

const emit = defineEmits(["title"]);
const unitySession = inject("unitySession", {
  capabilities: {},
  settings: {},
});
const refreshUnitySession = inject("refreshUnitySession", () =>
  Promise.resolve()
);

// All sections collapsed by default — admin clicks to expand the one they need.
const sections = reactive({
  profile: false,
  unity: false,
  ticketTypes: false,
  categories: false,
  templates: false,
  agents: false,
});

// --- Existing settings state ---
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
const agentSearch = ref("");
const selectedUser = ref("");
const creatingAgent = ref(false);
// Searchable "Add Agent" candidate combobox.
const candidateQuery = ref("");
const candidateOpen = ref(false);

const ticketTypes = ref([]);
const ticketTypeError = ref("");
const creatingTicketType = ref(false);
// True when the backend's loaded ticket-type list includes a custom_color
// key on at least one row. The backend strips that key when the
// HD Ticket Type table doesn't have the custom_color column yet (e.g.
// the schema patch hasn't applied), so this flag also gates whether we
// expose the Color column in the UI — no half-working pickers, no
// developer-facing "run bench migrate" prompts.
const colorColumnAvailable = computed(() =>
  ticketTypes.value.some((t) =>
    Object.prototype.hasOwnProperty.call(t, "custom_color")
  )
);
const savingTicketType = ref(false);
const newTicketType = reactive({ name: "", description: "", priority: "" });
const editingTicketType = reactive({
  name: "",
  keywordsInput: "",
  colorInput: "#94a3b8",
  originalColor: "",
});

// --- Reply template state ---
const categories = ref([]);
const categoryError = ref("");
const creatingCategory = ref(false);
const savingCategory = ref(false);
const newCategory = reactive({ title: "", color: "", description: "" });
const editingCategory = reactive({
  name: "",
  title: "",
  color: "",
  description: "",
});

const templates = ref([]);
const templateError = ref("");
const templateFilterCategory = ref("");
const templateFormOpen = ref(false);
const savingTemplate = ref(false);
const loadingTemplateBody = ref("");
const templateForm = reactive({
  name: "",
  title: "",
  category: "",
  language: "English",
  subject_template: "",
  message: "",
});

const profile = computed(() => unitySession);
const canManageUnitySettings = computed(
  () => !!unitySession.capabilities?.can_manage_unity_settings
);
const canManageAgents = computed(
  () => !!unitySession.capabilities?.can_manage_agents
);
const _agentsAsc = computed(() =>
  [...agents.value].sort((a, b) =>
    (a.full_name || a.user || a.name || "").localeCompare(
      b.full_name || b.user || b.name || ""
    )
  )
);
const filteredAgents = computed(() => {
  const term = agentSearch.value.trim().toLowerCase();
  if (!term) return _agentsAsc.value;
  return _agentsAsc.value.filter((agent) =>
    [agent.name, agent.user, agent.full_name].some((v) =>
      String(v || "")
        .toLowerCase()
        .includes(term)
    )
  );
});

// --- Searchable "Add Agent" candidate combobox ---
const _candidatesAsc = computed(() =>
  [...candidates.value].sort((a, b) =>
    (a.full_name || a.name || "").localeCompare(b.full_name || b.name || "")
  )
);
const candidateMatches = computed(() => {
  const q = candidateQuery.value.trim().toLowerCase();
  if (!q) return _candidatesAsc.value;
  return _candidatesAsc.value.filter((c) =>
    [c.name, c.full_name, c.email].some((v) =>
      String(v || "")
        .toLowerCase()
        .includes(q)
    )
  );
});
function candidateLabel(name) {
  if (!name) return "";
  const c = candidates.value.find((x) => x.name === name);
  return c ? `${c.full_name || c.name} (${c.email || c.name})` : name;
}
function onCandidateInput() {
  candidateOpen.value = true;
}
function onCandidateFocus() {
  candidateOpen.value = true;
}
function onCandidateBlur() {
  setTimeout(() => {
    candidateOpen.value = false;
    candidateQuery.value = candidateLabel(selectedUser.value);
  }, 120);
}
function pickCandidate(candidate) {
  selectedUser.value = candidate.name;
  candidateQuery.value = `${candidate.full_name || candidate.name} (${
    candidate.email || candidate.name
  })`;
  candidateOpen.value = false;
}

const templateFormValid = computed(
  () =>
    templateForm.title.trim() &&
    templateForm.category &&
    (templateForm.message || "").replace(/<[^>]*>/g, "").trim()
);

onMounted(async () => {
  emit("title", "Settings", "Super admin controls");
  // Ticket types are public to all Unity users, no capability gate needed.
  loadTicketTypes();
  // The remaining loaders are gated on capabilities that may arrive
  // asynchronously from the parent App.vue. The watchers below pick them up
  // the moment capabilities flip to true, so an initial mount before the
  // session is ready doesn't leave the section blank forever.
  if (canManageUnitySettings.value) {
    loadCategories();
    loadTemplates();
  }
  if (canManageAgents.value) {
    loadAgents();
    loadCandidates();
  }
});

watch(
  () => unitySession.settings?.unity_email_thread_layout,
  (value) => {
    threadLayout.value = value || "Classic";
  }
);

// Re-fetch admin data when capabilities arrive after mount (or change later).
watch(canManageUnitySettings, (value) => {
  if (value) {
    loadCategories();
    loadTemplates();
  }
});

watch(canManageAgents, (value) => {
  if (value) {
    loadAgents();
    loadCandidates();
  }
});

// --- Existing load/save helpers ---

async function loadAgents() {
  if (!canManageAgents.value) return;
  agentsLoading.value = true;
  agentsError.value = "";
  try {
    const result = await getAgents();
    agents.value = Array.isArray(result)
      ? result
      : result?.data || result || [];
  } catch (err) {
    agentsError.value = err.message || "Failed to load agents";
    console.error("[settings] loadAgents failed:", err);
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
    if (canManageUnitySettings.value) {
      // Admin view — also fetches keywords so the row can be edited inline.
      ticketTypes.value = await listTicketTypesWithKeywords();
    } else {
      ticketTypes.value = await getTicketTypes();
    }
  } catch {
    ticketTypes.value = [];
  }
}

function startTicketTypeEdit(type) {
  editingTicketType.name = type.name;
  editingTicketType.keywordsInput = Array.isArray(type.keywords)
    ? type.keywords.join(", ")
    : "";
  // <input type="color"> requires a valid hex string — fall back to a
  // neutral default when the type has no color set yet.
  editingTicketType.colorInput = type.custom_color || "#94a3b8";
  editingTicketType.originalColor = type.custom_color || "";
  ticketTypeError.value = "";
}

function cancelTicketTypeEdit() {
  editingTicketType.name = "";
  editingTicketType.keywordsInput = "";
  editingTicketType.colorInput = "#94a3b8";
  editingTicketType.originalColor = "";
}

async function saveTicketTypeEdit() {
  if (!editingTicketType.name) return;
  savingTicketType.value = true;
  ticketTypeError.value = "";
  try {
    const keywords = editingTicketType.keywordsInput
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean);

    // Fire both saves in parallel. The color endpoint only writes when the
    // value changed — saves a needless round-trip when the admin just
    // tweaked keywords.
    const promises = [
      updateTicketTypeKeywords(editingTicketType.name, keywords),
    ];
    const newColor = editingTicketType.colorInput || "";
    if (newColor !== editingTicketType.originalColor) {
      promises.push(updateTicketTypeColor(editingTicketType.name, newColor));
    }
    await Promise.all(promises);

    cancelTicketTypeEdit();
    await loadTicketTypes();
  } catch (err) {
    ticketTypeError.value = err.message;
  } finally {
    savingTicketType.value = false;
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
    candidateQuery.value = "";
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

// --- Reply template categories ---

async function loadCategories() {
  if (!canManageUnitySettings.value) return;
  try {
    const result = await listReplyTemplateCategoriesAdmin();
    categories.value = Array.isArray(result) ? result : [];
  } catch (err) {
    categoryError.value = err.message || "Failed to load categories";
    console.error("[settings] loadCategories failed:", err);
  }
}

async function handleCreateCategory() {
  const title = newCategory.title.trim();
  if (!title) return;
  creatingCategory.value = true;
  categoryError.value = "";
  try {
    await createReplyTemplateCategory({
      title,
      color: newCategory.color || undefined,
      description: newCategory.description || undefined,
    });
    newCategory.title = "";
    newCategory.color = "";
    newCategory.description = "";
    await loadCategories();
  } catch (err) {
    categoryError.value = err.message;
  } finally {
    creatingCategory.value = false;
  }
}

function startCategoryEdit(cat) {
  editingCategory.name = cat.name;
  editingCategory.title = cat.title;
  editingCategory.color = cat.color || "";
  editingCategory.description = cat.description || "";
}

function cancelCategoryEdit() {
  editingCategory.name = "";
}

async function saveCategoryEdit() {
  if (!editingCategory.name) return;
  savingCategory.value = true;
  categoryError.value = "";
  try {
    await updateReplyTemplateCategory({
      name: editingCategory.name,
      title: editingCategory.title.trim(),
      color: editingCategory.color || "",
      description: editingCategory.description || "",
    });
    cancelCategoryEdit();
    await loadCategories();
  } catch (err) {
    categoryError.value = err.message;
  } finally {
    savingCategory.value = false;
  }
}

async function handleDeleteCategory(cat) {
  if (!window.confirm(`Delete category "${cat.title}"? This cannot be undone.`))
    return;
  categoryError.value = "";
  try {
    await deleteReplyTemplateCategory(cat.name);
    await Promise.allSettled([loadCategories(), loadTemplates()]);
  } catch (err) {
    categoryError.value = err.message;
  }
}

// --- Reply templates ---

async function loadTemplates() {
  if (!canManageUnitySettings.value) return;
  try {
    const result = await listReplyTemplatesAdmin({
      category: templateFilterCategory.value || undefined,
    });
    templates.value = Array.isArray(result) ? result : [];
  } catch (err) {
    templateError.value = err.message || "Failed to load templates";
    console.error("[settings] loadTemplates failed:", err);
  }
}

function openTemplateForm() {
  templateForm.name = "";
  templateForm.title = "";
  templateForm.category = templateFilterCategory.value || "";
  templateForm.language = "English";
  templateForm.subject_template = "";
  templateForm.message = "";
  templateFormOpen.value = true;
}

function closeTemplateForm() {
  templateFormOpen.value = false;
}

async function startTemplateEdit(tpl) {
  loadingTemplateBody.value = tpl.name;
  templateError.value = "";
  try {
    const fullDoc = await getReplyTemplateDoc(tpl.name);
    templateForm.name = fullDoc.name;
    templateForm.title = fullDoc.title || "";
    templateForm.category = fullDoc.category || "";
    templateForm.language = fullDoc.language || "English";
    templateForm.subject_template = fullDoc.subject_template || "";
    templateForm.message = fullDoc.message || "";
    templateFormOpen.value = true;
  } catch (err) {
    templateError.value = err.message;
  } finally {
    loadingTemplateBody.value = "";
  }
}

async function saveTemplate() {
  if (!templateFormValid.value) return;
  savingTemplate.value = true;
  templateError.value = "";
  try {
    const payload = {
      title: templateForm.title.trim(),
      category: templateForm.category,
      language: templateForm.language,
      subject_template: templateForm.subject_template || "",
      message: templateForm.message,
    };
    if (templateForm.name) {
      await updateReplyTemplate({ name: templateForm.name, ...payload });
    } else {
      await createReplyTemplate(payload);
    }
    closeTemplateForm();
    await loadTemplates();
  } catch (err) {
    templateError.value = err.message;
  } finally {
    savingTemplate.value = false;
  }
}

async function handleDeleteTemplate(tpl) {
  if (!window.confirm(`Delete template "${tpl.title}"? This cannot be undone.`))
    return;
  templateError.value = "";
  try {
    await deleteReplyTemplate(tpl.name);
    if (templateForm.name === tpl.name) closeTemplateForm();
    await loadTemplates();
  } catch (err) {
    templateError.value = err.message;
  }
}
</script>

<style scoped>
.settings-page {
  padding-bottom: 40px;
}

.grow {
  flex: 1;
}
</style>
