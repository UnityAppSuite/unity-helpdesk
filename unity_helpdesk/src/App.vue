<template>
  <div class="app-shell" :class="threadLayoutClass">
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <RouterLink class="brand" to="/tickets/my" @click="sidebarOpen = false">
        <span class="brand-mark">
          <img :src="brandLogo" alt="Unity Helpdesk" />
        </span>
        <span class="brand-copy">
          <strong>Unity Helpdesk</strong>
          <small>Fast support workspace</small>
        </span>
      </RouterLink>

      <nav>
        <p>Tickets</p>
        <RouterLink
          v-if="canViewMyTickets"
          to="/tickets/my"
          @click="sidebarOpen = false"
        >
          My Tickets
        </RouterLink>
        <RouterLink
          v-if="canViewAllTickets"
          to="/tickets/all"
          @click="sidebarOpen = false"
        >
          All Tickets
        </RouterLink>
        <RouterLink
          v-if="canViewMyTickets"
          to="/dashboard"
          @click="sidebarOpen = false"
        >
          Dashboard
        </RouterLink>
        <template v-if="canManageUnitySettings">
          <p>Management</p>
          <RouterLink
            v-if="canManageUnitySettings"
            to="/settings"
            @click="sidebarOpen = false"
          >
            Settings
          </RouterLink>
        </template>
      </nav>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="topbar-main">
          <button class="menu-btn" @click="sidebarOpen = !sidebarOpen">
            Menu
          </button>
          <div>
            <strong>{{ pageTitle }}</strong>
            <span>{{ pageSubtitle }}</span>
          </div>
        </div>
        <div class="topbar-actions">
          <button class="btn" @click="openComposer = true">New Ticket</button>
          <button
            v-if="canViewAllTickets"
            class="btn secondary"
            @click="openBulkEmailModal"
          >
            Bulk Email
          </button>

          <!-- Avatar with dropdown -->
          <div class="avatar-wrap" @click="profileMenuOpen = !profileMenuOpen">
            <img
              v-if="profile.user_image"
              class="top-avatar-image"
              :src="profile.user_image"
              :alt="profile.full_name || profile.name || 'User'"
            />
            <span v-else class="avatar avatar-lg top-avatar-fallback">
              {{ initials(profile.full_name || profile.name || "") || "U" }}
            </span>
            <div v-if="profileMenuOpen" class="profile-dropdown" @click.stop>
              <div class="profile-dropdown-header">
                <strong>{{
                  profile.full_name || profile.name || "User"
                }}</strong>
                <small>{{ profile.email || "" }}</small>
              </div>
              <RouterLink
                v-if="canManageUnitySettings"
                class="profile-dropdown-item"
                to="/settings"
                @click="profileMenuOpen = false"
              >
                Settings
              </RouterLink>
              <a class="profile-dropdown-item" href="/app" target="_top">
                Switch to Desk
              </a>
            </div>
          </div>
        </div>
      </header>

      <!-- Click-away overlay for profile menu -->
      <div
        v-if="profileMenuOpen"
        class="profile-overlay"
        @click="profileMenuOpen = false"
      ></div>

      <RouterView @title="setTitle" />
    </main>

    <!-- Create Ticket modal -->
    <div v-if="openComposer" class="modal-backdrop" @click.self="closeComposer">
      <section class="modal-card">
        <div class="modal-header">
          <div>
            <strong>Create Ticket</strong>
            <span
              >Create a ticket and send the first email to the customer.</span
            >
          </div>
          <button class="btn secondary" @click="closeComposer">Close</button>
        </div>
        <div class="modal-body stack">
          <p v-if="composerError" class="error">{{ composerError }}</p>
          <p v-else-if="composerWarning" class="warning-banner">
            {{ composerWarning }}
          </p>

          <!-- Customer Email with user search -->
          <label>
            Customer Email
            <div class="input-with-action">
              <input
                v-model="composer.raised_by"
                type="email"
                placeholder="customer@example.com"
                autocomplete="off"
                @input="onEmailInput"
                @focus="onEmailInput"
              />
              <a
                href="/app/user/new-user-1"
                target="_blank"
                class="btn secondary input-action-btn"
                title="Add new user"
              >
                + Add User
              </a>
            </div>
            <!-- User suggestions -->
            <ul v-if="userSuggestions.length" class="user-suggestions">
              <li
                v-for="u in userSuggestions"
                :key="u.name"
                @mousedown.prevent="selectUser(u)"
              >
                <span
                  class="avatar"
                  style="width: 20px; height: 20px; font-size: 9px"
                >
                  {{ initials(u.full_name || u.name) }}
                </span>
                <span>{{ u.full_name || u.name }}</span>
                <small>{{ u.email || u.name }}</small>
              </li>
            </ul>
          </label>

          <label>
            Subject
            <input
              v-model="composer.subject"
              type="text"
              placeholder="Enter ticket subject"
            />
          </label>
          <label>
            Ticket Type
            <select v-model="composer.ticket_type">
              <option value="">Not set</option>
              <option
                v-for="ticketType in ticketTypes"
                :key="ticketType.name"
                :value="ticketType.name"
              >
                {{ ticketType.name }}
              </option>
            </select>
          </label>
          <label>
            Priority
            <select v-model="composer.priority">
              <option value="">Not set</option>
              <option>High</option>
              <option>Medium</option>
              <option>Low</option>
            </select>
          </label>
          <label>
            Assign To
            <select v-model="composer.assignee">
              <option value="">Unassigned</option>
              <option
                v-for="agent in agents"
                :key="agent.name"
                :value="agent.name"
              >
                {{ agent.full_name || agent.name }}
              </option>
            </select>
          </label>
          <label>
            Email Message
            <TinyMceEditor
              v-model="composer.message"
              :min-height="260"
              placeholder="Write the email that should be sent to the customer"
              @template-subject="applyTemplateSubjectToComposer"
            />
          </label>
          <label>
            Attachments
            <div class="composer-attachment-actions">
              <button
                type="button"
                class="btn secondary"
                :disabled="composerUploading"
                @click="composerAttachmentInput?.click()"
              >
                {{ composerUploading ? "Uploading..." : "Add Attachments" }}
              </button>
              <input
                ref="composerAttachmentInput"
                type="file"
                class="hidden-file-input"
                multiple
                @change="handleComposerAttachments"
              />
            </div>
            <div
              v-if="composer.attachments.length"
              class="attachment-list attachment-list-modal"
            >
              <div
                v-for="attachment in composer.attachments"
                :key="attachment.name"
                class="attachment-item"
              >
                <a
                  :href="attachment.file_url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ attachment.file_name || attachment.name }}
                </a>
                <button
                  type="button"
                  class="link-btn danger-link"
                  @click="removeComposerAttachment(attachment.name)"
                >
                  Remove
                </button>
              </div>
            </div>
          </label>
        </div>
        <div class="modal-footer">
          <button class="btn secondary" @click="closeComposer">Cancel</button>
          <button class="btn" :disabled="composerSaving" @click="createTicket">
            {{ composerSaving ? "Sending..." : "Create & Send Email" }}
          </button>
        </div>
      </section>
    </div>

    <!-- Bulk Email modal -->
    <div
      v-if="openBulkEmail"
      class="modal-backdrop"
      @click.self="closeBulkEmail"
    >
      <section class="modal-card">
        <div class="modal-header">
          <div>
            <strong>Send Bulk Email</strong>
            <span
              >Compose one email and BCC many recipients. A single audit ticket
              is created.</span
            >
          </div>
          <button class="btn secondary" @click="closeBulkEmail">Close</button>
        </div>
        <div class="modal-body stack">
          <p v-if="bulkEmailError" class="error">{{ bulkEmailError }}</p>
          <p v-else-if="bulkEmailWarning" class="warning-banner">
            {{ bulkEmailWarning }}
          </p>

          <label>
            Recipients
            <div class="recipient-multiselect recipient-multiselect--locked">
              <span class="recipient-chip recipient-chip--locked">
                <span class="recipient-chip-label">{{ FEEDBACK_EMAIL }}</span>
              </span>
            </div>
          </label>

          <label>
            Subject
            <input
              v-model="bulkEmail.subject"
              type="text"
              placeholder="Email subject"
            />
          </label>
          <label>
            Ticket Type <span class="required-asterisk">*</span>
            <select v-model="bulkEmail.ticket_type" required>
              <option value="" disabled>Select ticket type…</option>
              <option
                v-for="ticketType in ticketTypes"
                :key="ticketType.name"
                :value="ticketType.name"
              >
                {{ ticketType.name }}
              </option>
            </select>
          </label>
          <label>
            CC (optional)
            <div class="recipient-multiselect" @click="focusCcInput">
              <span
                v-for="c in bulkEmail.cc"
                :key="c.email"
                class="recipient-chip"
              >
                <span class="recipient-chip-label" :title="c.email">{{
                  c.email
                }}</span>
                <button
                  type="button"
                  class="recipient-chip-remove"
                  @click.stop="removeCc(c.email)"
                >
                  ×
                </button>
              </span>
              <div class="recipient-input-wrap">
                <input
                  ref="ccInputRef"
                  v-model="ccInputQuery"
                  type="text"
                  class="recipient-input"
                  placeholder="cc1@example.com"
                  autocomplete="off"
                  @keydown.enter.prevent="addCcFromInput"
                  @keydown="onCcKeydown"
                />
              </div>
            </div>
          </label>
          <label>
            BCC (students)
            <div class="recipient-multiselect" @click="focusBccInput">
              <span
                v-for="b in bulkEmail.bcc"
                :key="b.email"
                class="recipient-chip"
              >
                <span class="recipient-chip-label" :title="b.email">{{
                  b.label || b.email
                }}</span>
                <button
                  type="button"
                  class="recipient-chip-remove"
                  @click.stop="removeBcc(b.email)"
                >
                  ×
                </button>
              </span>
              <div class="recipient-input-wrap">
                <input
                  ref="bccInputRef"
                  v-model="bccSearchQuery"
                  type="text"
                  class="recipient-input"
                  placeholder="Type student name or email…"
                  autocomplete="off"
                  @input="onBccSearch"
                  @keydown.enter.prevent="addBccFromInput"
                  @keydown.backspace="onBccBackspace"
                  @keydown.escape="bccResults = []"
                  @focus="onBccSearch"
                />
                <div v-if="bccResults.length" class="recipient-dropdown">
                  <button
                    v-for="r in bccResults"
                    :key="r.email"
                    type="button"
                    class="recipient-dropdown-item"
                    @mousedown.prevent="selectBcc(r)"
                  >
                    <span class="rd-name">{{ r.name }}</span>
                    <span class="rd-email">{{ r.email }}</span>
                  </button>
                </div>
              </div>
            </div>
            <div class="composer-attachment-actions" style="margin-top: 6px">
              <button
                type="button"
                class="btn secondary"
                :disabled="bulkEmailUploading"
                @click="bulkEmailCsvInput?.click()"
              >
                Import CSV
              </button>
              <input
                ref="bulkEmailCsvInput"
                type="file"
                accept=".csv,text/csv"
                class="hidden-file-input"
                @change="handleBulkEmailCsv"
              />
              <a
                href="/api/method/helpdesk.api.unity_helpdesk_ext.get_bulk_email_sample_csv"
                target="_blank"
                rel="noopener noreferrer"
                class="btn secondary"
              >
                Sample CSV
              </a>
              <span class="muted" style="margin-left: auto">
                {{ bulkEmail.bcc.length }} student{{
                  bulkEmail.bcc.length === 1 ? "" : "s"
                }}
              </span>
            </div>
          </label>
          <label class="bulk-email-guardian-toggle">
            <input
              v-model="includeGuardians"
              type="checkbox"
              @change="onIncludeGuardiansChange"
            />
            Include guardian emails
            <span v-if="guardiansLoading" class="muted" style="margin-left: 6px"
              >fetching guardians…</span
            >
          </label>
          <label>
            Guardian Emails
            <textarea
              v-model="guardianEmails"
              rows="3"
              placeholder="Check the box above to auto-fill guardian emails for the students added in BCC"
            ></textarea>
          </label>
          <label>
            Message
            <TinyMceEditor
              v-model="bulkEmail.message"
              :min-height="240"
              placeholder="Compose the email message"
              @template-subject="applyTemplateSubjectToBulkEmail"
            />
          </label>
          <label>
            Attachments
            <div class="composer-attachment-actions">
              <button
                type="button"
                class="btn secondary"
                :disabled="bulkEmailUploading"
                @click="bulkEmailAttachmentInput?.click()"
              >
                {{ bulkEmailUploading ? "Uploading..." : "Add Attachments" }}
              </button>
              <input
                ref="bulkEmailAttachmentInput"
                type="file"
                class="hidden-file-input"
                multiple
                @change="handleBulkEmailAttachments"
              />
            </div>
            <div
              v-if="bulkEmail.attachments.length"
              class="attachment-list attachment-list-modal"
            >
              <div
                v-for="attachment in bulkEmail.attachments"
                :key="attachment.name"
                class="attachment-item"
              >
                <a
                  :href="attachment.file_url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ attachment.file_name || attachment.name }}
                </a>
                <button
                  type="button"
                  class="link-btn danger-link"
                  @click="removeBulkEmailAttachment(attachment.name)"
                >
                  Remove
                </button>
              </div>
            </div>
          </label>
        </div>
        <div class="modal-footer">
          <button class="btn secondary" @click="closeBulkEmail">Cancel</button>
          <button
            class="btn"
            :disabled="bulkEmailSending || !bulkEmailBccTotal"
            @click="sendBulkEmail"
          >
            {{
              bulkEmailSending
                ? "Sending..."
                : `Send to ${bulkEmailBccTotal} recipient${
                    bulkEmailBccTotal === 1 ? "" : "s"
                  }`
            }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, provide, reactive, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import TinyMceEditor from "@desk/components/TinyMceEditor.vue";
import {
  AuthRedirectError,
  call,
  initials,
  getAgents,
  getTicketTypes,
  getUnityProfile,
  redirectToLogin,
  searchUsers,
  uploadAttachment,
} from "./api";

const TICKET_NOTICE_KEY = "unity_helpdesk_ticket_notice";
const router = useRouter();
const route = useRoute();
const sidebarOpen = ref(false);
const pageTitle = ref("My Tickets");
const pageSubtitle = ref("Fast support workspace");
const profile = ref({});
const profileMenuOpen = ref(false);
const brandLogo = "/assets/helpdesk/unity_helpdesk/favicon.svg";
const agents = ref([]);
const ticketTypes = ref([]);
const openComposer = ref(false);
const composerSaving = ref(false);
const composerUploading = ref(false);
const composerError = ref("");
const composerWarning = ref("");
const composerAttachmentInput = ref(null);
const userSuggestions = ref([]);
const session = reactive({
  name: "",
  full_name: "",
  email: "",
  username: "",
  user_image: "",
  roles: [],
  capabilities: {},
  settings: {
    unity_email_thread_layout: "Classic",
    column_preferences: [],
  },
  available_columns: [],
});
let suggestTimeout = null;
const composer = reactive({
  raised_by: "",
  subject: "",
  message: "",
  priority: "",
  ticket_type: "",
  assignee: "",
  attachments: [],
});

// --- Bulk email state ---
const openBulkEmail = ref(false);
const bulkEmailSending = ref(false);
const bulkEmailUploading = ref(false);
const bulkEmailError = ref("");
const bulkEmailWarning = ref("");
const bulkEmailCsvInput = ref(null);
const bulkEmailAttachmentInput = ref(null);
const FEEDBACK_EMAIL = "feedback@example.com";
const bulkEmail = reactive({
  recipients: [
    { email: FEEDBACK_EMAIL, name: FEEDBACK_EMAIL, label: FEEDBACK_EMAIL },
  ],
  subject: "",
  ticket_type: "",
  message: "",
  cc: [],
  bcc: [], // [{email, name?, label?}] — students
  attachments: [],
});
const includeGuardians = ref(false);
const guardianEmails = ref("");
const guardiansLoading = ref(false);
const ccInputRef = ref(null);
const ccInputQuery = ref("");
const bccInputRef = ref(null);
const bccSearchQuery = ref("");
const bccResults = ref([]);
let _bccSearchTimer = null;

provide("unitySession", session);
provide("refreshUnitySession", loadSession);
// Lookups loaded at app level so child views (TicketsView, TicketDetailView)
// can reuse them via inject instead of re-fetching on every navigation.
provide("unityAgents", agents);
provide("unityTicketTypes", ticketTypes);

const capabilities = computed(() => session.capabilities || {});
const canViewMyTickets = computed(
  () => !!capabilities.value.can_view_my_tickets
);
const canViewAllTickets = computed(
  () => !!capabilities.value.can_view_all_tickets
);
const canManageAgents = computed(() => !!capabilities.value.can_manage_agents);
const canManageUnitySettings = computed(
  () => !!capabilities.value.can_manage_unity_settings
);
const threadLayout = computed(
  () => session.settings?.unity_email_thread_layout || "Classic"
);
const threadLayoutClass = computed(
  () =>
    `thread-layout-${String(threadLayout.value || "classic")
      .toLowerCase()
      .replace(/\s+/g, "-")}`
);

onMounted(async () => {
  await Promise.allSettled([loadSession(), loadLookups()]);
});

watch(
  () => route.fullPath,
  () => {
    enforceRouteAccess();
  }
);

function setTitle(title, subtitle = "Fast support workspace") {
  pageTitle.value = title;
  pageSubtitle.value = subtitle;
}

async function loadSession() {
  try {
    const data = (await getUnityProfile()) || {};
    profile.value = data || {};
    session.name = data.name || "";
    session.full_name = data.full_name || "";
    session.email = data.email || "";
    session.username = data.username || "";
    session.user_image = data.user_image || "";
    session.roles = data.roles || [];
    session.capabilities = data.capabilities || {};
    session.settings = {
      unity_email_thread_layout:
        data.settings?.unity_email_thread_layout || "Classic",
      column_preferences: Array.isArray(data.settings?.column_preferences)
        ? data.settings.column_preferences
        : [],
    };
    session.available_columns = Array.isArray(data.available_columns)
      ? data.available_columns
      : [];
    enforceRouteAccess();
  } catch (err) {
    if (err instanceof AuthRedirectError) {
      // api.js already kicked off the redirect; nothing else to do.
      return;
    }
    // If the error didn't carry the standard auth markers but the profile
    // came back empty (no email, no roles), the session is effectively gone.
    // Fall back to a login redirect so we don't render the SPA shell as Guest.
    if (!session.email && !session.roles?.length) {
      redirectToLogin();
      return;
    }
    composerError.value = err.message;
  }
}

async function loadLookups() {
  const [agentRows, typeRows] = await Promise.allSettled([
    getAgents(),
    getTicketTypes(),
  ]);
  agents.value = agentRows.status === "fulfilled" ? agentRows.value || [] : [];
  ticketTypes.value =
    typeRows.status === "fulfilled" ? typeRows.value || [] : [];
}

function enforceRouteAccess() {
  if (!canViewMyTickets.value && route.path !== "/") {
    return;
  }
  if (route.path === "/tickets/all" && !canViewAllTickets.value) {
    router.replace("/tickets/my");
    return;
  }
  if (route.path === "/agents" && !canManageAgents.value) {
    router.replace(canManageUnitySettings.value ? "/settings" : "/tickets/my");
    return;
  }
  if (route.path === "/settings" && !canManageUnitySettings.value) {
    router.replace("/tickets/my");
  }
}

function closeComposer() {
  openComposer.value = false;
  composerSaving.value = false;
  composerUploading.value = false;
  composerError.value = "";
  composerWarning.value = "";
  userSuggestions.value = [];
  composer.raised_by = "";
  composer.subject = "";
  composer.message = "";
  composer.priority = "";
  composer.ticket_type = "";
  composer.assignee = "";
  composer.attachments = [];
}

function applyTemplateSubjectToComposer(subject) {
  if (!subject) return;
  if (
    composer.subject &&
    composer.subject.trim() &&
    !window.confirm("Replace the current subject with the template's subject?")
  ) {
    return;
  }
  composer.subject = subject;
}

function applyTemplateSubjectToBulkEmail(subject) {
  if (!subject) return;
  if (
    bulkEmail.subject &&
    bulkEmail.subject.trim() &&
    !window.confirm("Replace the current subject with the template's subject?")
  ) {
    return;
  }
  bulkEmail.subject = subject;
}

function onEmailInput() {
  clearTimeout(suggestTimeout);
  const query = composer.raised_by;
  if (!query || query.length < 2) {
    userSuggestions.value = [];
    return;
  }
  suggestTimeout = setTimeout(async () => {
    try {
      userSuggestions.value = await searchUsers(query);
    } catch {
      userSuggestions.value = [];
    }
  }, 300);
}

function selectUser(user) {
  composer.raised_by = user.email || user.name;
  userSuggestions.value = [];
}

async function handleComposerAttachments(event) {
  const files = Array.from(event.target.files || []);
  if (!files.length) return;
  composerUploading.value = true;
  composerError.value = "";
  try {
    for (const file of files) {
      const uploaded = await uploadAttachment(file);
      composer.attachments.push(uploaded);
    }
  } catch (err) {
    composerError.value = err.message;
  } finally {
    composerUploading.value = false;
    if (composerAttachmentInput.value) {
      composerAttachmentInput.value.value = "";
    }
  }
}

function removeComposerAttachment(name) {
  composer.attachments = composer.attachments.filter(
    (attachment) => attachment.name !== name
  );
}

async function createTicket() {
  composerSaving.value = true;
  composerError.value = "";
  composerWarning.value = "";
  try {
    const result = await call("helpdesk.api.unity_helpdesk_ext.create_ticket", {
      subject: composer.subject,
      raised_by: composer.raised_by,
      message: composer.message,
      priority: composer.priority,
      ticket_type: composer.ticket_type,
      assignee: composer.assignee,
      attachments: composer.attachments.map((attachment) => attachment.name),
    });
    const ticket = result?.ticket || {};
    if (result?.warning) {
      sessionStorage.setItem(TICKET_NOTICE_KEY, result.warning);
      composerWarning.value = result.warning;
    }
    closeComposer();
    if (ticket?.name) {
      router.push(`/tickets/${ticket.name}`);
    }
  } catch (err) {
    composerError.value = err.message;
  } finally {
    composerSaving.value = false;
  }
}

// --- Bulk email ---
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function parseBulkRecipients(raw) {
  return (raw || "")
    .split(/[\s,;]+/)
    .map((value) => value.trim())
    .filter((value) => EMAIL_REGEX.test(value));
}

const parsedGuardianEmails = computed(() =>
  parseBulkRecipients(guardianEmails.value)
);
const bulkEmailBccTotal = computed(() => {
  const seen = new Set();
  for (const b of bulkEmail.bcc) {
    if (b.email) seen.add(b.email.toLowerCase());
  }
  for (const g of parsedGuardianEmails.value) seen.add(g);
  return seen.size;
});

function focusCcInput() {
  ccInputRef.value?.focus();
}

function addCcFromInput() {
  const val = ccInputQuery.value.trim().toLowerCase();
  if (!EMAIL_REGEX.test(val)) return;
  if (bulkEmail.cc.find((c) => c.email === val)) {
    ccInputQuery.value = "";
    return;
  }
  bulkEmail.cc.push({ email: val });
  ccInputQuery.value = "";
}

function removeCc(email) {
  bulkEmail.cc = bulkEmail.cc.filter((c) => c.email !== email);
}

function onCcKeydown(e) {
  if (e.key === ",") {
    e.preventDefault();
    addCcFromInput();
  } else if (
    e.key === "Backspace" &&
    !ccInputQuery.value &&
    bulkEmail.cc.length
  ) {
    bulkEmail.cc.pop();
  }
}

function focusBccInput() {
  bccInputRef.value?.focus();
}

function selectBcc(r) {
  const email = (r.email || "").toLowerCase().trim();
  if (!email || bulkEmail.bcc.find((x) => x.email === email)) return;
  bulkEmail.bcc.push({
    email,
    name: r.name || email,
    label: r.name ? `${r.name}` : email,
  });
  bccSearchQuery.value = "";
  bccResults.value = [];
  bccInputRef.value?.focus();
  if (includeGuardians.value) refreshGuardianEmails();
}

function addBccFromInput() {
  const val = bccSearchQuery.value.trim();
  if (EMAIL_REGEX.test(val)) {
    selectBcc({ email: val, name: val });
  }
}

function removeBcc(email) {
  bulkEmail.bcc = bulkEmail.bcc.filter((b) => b.email !== email);
  if (includeGuardians.value) refreshGuardianEmails();
}

function onBccBackspace() {
  if (!bccSearchQuery.value && bulkEmail.bcc.length) {
    bulkEmail.bcc.pop();
    if (includeGuardians.value) refreshGuardianEmails();
  }
}

function onBccSearch() {
  clearTimeout(_bccSearchTimer);
  const q = bccSearchQuery.value.trim();
  if (q.length < 2) {
    bccResults.value = [];
    return;
  }
  _bccSearchTimer = window.setTimeout(async () => {
    try {
      const results = await call(
        "helpdesk.api.unity_helpdesk.search_contacts",
        { query: q }
      );
      bccResults.value = results || [];
    } catch {
      bccResults.value = [];
    }
  }, 280);
}

async function onIncludeGuardiansChange() {
  if (includeGuardians.value) {
    await refreshGuardianEmails();
  } else {
    guardianEmails.value = "";
  }
}

async function refreshGuardianEmails() {
  const studentEmails = bulkEmail.bcc
    .map((b) => (b.email || "").toLowerCase().trim())
    .filter((e) => e && EMAIL_REGEX.test(e));
  if (!studentEmails.length) {
    guardianEmails.value = "";
    return;
  }
  guardiansLoading.value = true;
  try {
    const response = await call(
      "helpdesk.api.unity_helpdesk.get_student_guardian_emails",
      { student_emails: JSON.stringify(studentEmails) }
    );
    // Backend now returns { mapping, diagnostic }. Old shape was a bare
    // { email: [guardians] } dict — keep a defensive fallback so the SPA
    // still renders if it talks to an older backend during a deploy.
    const mapping =
      response && response.mapping ? response.mapping : response || {};
    const diagnostic = (response && response.diagnostic) || null;

    const seen = new Set();
    for (const studentEmail of studentEmails) {
      const guardians = mapping[studentEmail] || [];
      for (const g of guardians) {
        const email = (g || "").toLowerCase().trim();
        if (email && EMAIL_REGEX.test(email)) seen.add(email);
      }
    }
    guardianEmails.value = [...seen].join(", ");

    // Surface a non-blocking warning when the lookup yielded nothing.
    // Replaces the previous silent catch — the user used to think the
    // checkbox was broken when actually their Student records simply
    // didn't have student_email_id set on this site.
    if (diagnostic) {
      if (diagnostic.input_count > 0 && diagnostic.students_matched === 0) {
        bulkEmailWarning.value =
          `Couldn't find guardians for any of the ${diagnostic.input_count} student email(s) ` +
          `in BCC — verify each address is set on a Student.student_email_id ` +
          `record. Run \`bench --site <site> execute helpdesk.api.unity_perf.diagnose_guardian_lookup ` +
          `--kwargs '{"emails":[...]}'\` to see exactly which step fails.`;
      } else if (
        diagnostic.input_count > 0 &&
        diagnostic.students_with_guardians === 0
      ) {
        bulkEmailWarning.value =
          `Matched ${diagnostic.students_matched} student(s), but none have guardian ` +
          `emails on file. Check the Student.guardians child table.`;
      } else if (
        diagnostic.unmatched_emails &&
        diagnostic.unmatched_emails.length
      ) {
        // Partial match — let the user know which addresses didn't resolve.
        bulkEmailWarning.value =
          `${diagnostic.unmatched_emails.length} of ${diagnostic.input_count} BCC ` +
          `address(es) had no matching Student record: ` +
          diagnostic.unmatched_emails.slice(0, 5).join(", ") +
          (diagnostic.unmatched_emails.length > 5 ? ", ..." : "");
      } else {
        // Clear any stale warning from a previous attempt.
        bulkEmailWarning.value = "";
      }
    }
  } catch (err) {
    // Don't blank the textarea if a network/auth error hits — keep the
    // last known guardian list, but surface a hint so the user isn't
    // left wondering why the checkbox seemed to do nothing.
    if (err instanceof AuthRedirectError || err?.code === "AUTH_REDIRECT") {
      return;
    }
    bulkEmailWarning.value =
      "Couldn't load guardian emails — keeping previous list. " +
      (err?.message || "Retry by toggling the checkbox.");
  } finally {
    guardiansLoading.value = false;
  }
}

function openBulkEmailModal() {
  resetBulkEmail();
  openBulkEmail.value = true;
}

function resetBulkEmail() {
  bulkEmailSending.value = false;
  bulkEmailUploading.value = false;
  bulkEmailError.value = "";
  bulkEmailWarning.value = "";
  bulkEmail.recipients = [
    { email: FEEDBACK_EMAIL, name: FEEDBACK_EMAIL, label: FEEDBACK_EMAIL },
  ];
  bulkEmail.subject = "";
  bulkEmail.ticket_type = "";
  bulkEmail.message = "";
  bulkEmail.cc = [];
  bulkEmail.bcc = [];
  bulkEmail.attachments = [];
  ccInputQuery.value = "";
  bccSearchQuery.value = "";
  bccResults.value = [];
  includeGuardians.value = false;
  guardianEmails.value = "";
  guardiansLoading.value = false;
}

function closeBulkEmail() {
  openBulkEmail.value = false;
  resetBulkEmail();
}

async function handleBulkEmailCsv(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  bulkEmailError.value = "";
  try {
    const text = await file.text();
    const lines = text.split(/\r?\n/);
    let startIdx = 0;
    if (lines.length && /\bemail\b/i.test(lines[0])) startIdx = 1;
    const existing = new Set(bulkEmail.bcc.map((b) => b.email));
    let added = 0;
    for (let i = startIdx; i < lines.length; i += 1) {
      const cell = (lines[i] || "").split(",")[0].trim();
      if (cell && EMAIL_REGEX.test(cell) && !existing.has(cell.toLowerCase())) {
        const lower = cell.toLowerCase();
        bulkEmail.bcc.push({ email: lower, name: cell, label: cell });
        existing.add(lower);
        added += 1;
      }
    }
    if (!added && !bulkEmail.bcc.length) {
      bulkEmailError.value =
        "No valid emails found in CSV. Use a single 'email' column.";
    } else if (added && includeGuardians.value) {
      refreshGuardianEmails();
    }
  } finally {
    if (bulkEmailCsvInput.value) bulkEmailCsvInput.value.value = "";
  }
}

async function handleBulkEmailAttachments(event) {
  const files = Array.from(event.target.files || []);
  if (!files.length) return;
  bulkEmailUploading.value = true;
  bulkEmailError.value = "";
  try {
    for (const file of files) {
      const uploaded = await uploadAttachment(file);
      bulkEmail.attachments.push(uploaded);
    }
  } catch (err) {
    bulkEmailError.value = err.message;
  } finally {
    bulkEmailUploading.value = false;
    if (bulkEmailAttachmentInput.value) {
      bulkEmailAttachmentInput.value.value = "";
    }
  }
}

function removeBulkEmailAttachment(name) {
  bulkEmail.attachments = bulkEmail.attachments.filter(
    (attachment) => attachment.name !== name
  );
}

async function sendBulkEmail() {
  bulkEmailError.value = "";
  bulkEmailWarning.value = "";
  const recipients = bulkEmail.recipients.map((r) => r.email);
  const bccSeen = new Set();
  for (const b of bulkEmail.bcc) {
    const email = (b.email || "").toLowerCase().trim();
    if (email && EMAIL_REGEX.test(email)) bccSeen.add(email);
  }
  for (const g of parsedGuardianEmails.value) bccSeen.add(g);
  const bccEmails = [...bccSeen];
  if (!bccEmails.length) {
    bulkEmailError.value =
      "Add at least one student in BCC (and optionally include guardians).";
    return;
  }
  if (!bulkEmail.subject.trim()) {
    bulkEmailError.value = "Subject is required.";
    return;
  }
  if (!bulkEmail.ticket_type) {
    bulkEmailError.value = "Ticket Type is required.";
    return;
  }
  if (!bulkEmail.message.trim()) {
    bulkEmailError.value = "Message is required.";
    return;
  }
  bulkEmailSending.value = true;
  try {
    const ccEmails = bulkEmail.cc.map((c) => c.email);
    const result = await call(
      "helpdesk.api.unity_helpdesk_ext.bulk_send_email",
      {
        subject: bulkEmail.subject,
        message: bulkEmail.message,
        ticket_type: bulkEmail.ticket_type,
        recipients: JSON.stringify(recipients),
        cc: ccEmails.length ? JSON.stringify(ccEmails) : null,
        bcc: bccEmails.length ? JSON.stringify(bccEmails) : null,
        attachments: JSON.stringify(
          bulkEmail.attachments.map((attachment) => attachment.name)
        ),
      }
    );
    if (result?.warning) {
      bulkEmailWarning.value = result.warning;
      return;
    }
    const message = result?.invalid_count
      ? `Sent to ${result.queued} recipients. ${result.invalid_count} invalid email(s) were skipped.`
      : `Sent to ${result?.queued || 0} recipients.`;
    if (result?.ticket) {
      sessionStorage.setItem(TICKET_NOTICE_KEY, message);
    }
    closeBulkEmail();
    if (result?.ticket) {
      router.push(`/tickets/${result.ticket}`);
    }
  } catch (err) {
    bulkEmailError.value = err.message;
  } finally {
    bulkEmailSending.value = false;
  }
}
</script>
