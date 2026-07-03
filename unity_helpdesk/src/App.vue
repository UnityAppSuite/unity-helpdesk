<template>
  <div class="app-shell" :class="threadLayoutClass">
    <transition name="global-notice-fade">
      <div
        v-if="globalNotice"
        class="global-notice"
        :class="`global-notice--${globalNotice.type}`"
        role="status"
        @click="dismissGlobalNotice"
      >
        {{ globalNotice.text }}
      </div>
    </transition>
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
          <button
            v-if="!route.params.ticketId"
            class="btn"
            @click="openComposer = true"
          >
            New Ticket
          </button>
          <button
            v-if="canViewAllTickets && !route.params.ticketId"
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
            Subject <span class="required-asterisk">*</span>
            <input
              v-model="composer.subject"
              type="text"
              placeholder="Enter ticket subject"
              required
            />
          </label>
          <label>
            Ticket Type <span class="required-asterisk">*</span>
            <select v-model="composer.ticket_type" required>
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
              :enable-email-template="true"
              :enable-attach="true"
              @attach="composerAttachmentInput?.click()"
              @template-subject="applyTemplateSubjectToComposer"
              @email-template-selected="applyEmailTemplateToCreateTicket"
            />
            <input
              ref="composerAttachmentInput"
              type="file"
              class="hidden-file-input"
              multiple
              @change="handleComposerAttachments"
            />
            <span v-if="composerUploading" class="muted">Uploading…</span>
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
              >Send a personalised email to each student (and their guardians).
              One ticket is created per student.</span
            >
          </div>
          <button class="btn secondary" @click="closeBulkEmail">Close</button>
        </div>
        <div class="modal-body stack">
          <p v-if="bulkEmailError" class="error">{{ bulkEmailError }}</p>
          <p v-else-if="bulkEmailWarning" class="warning-banner">
            {{ bulkEmailWarning }}
          </p>

          <!-- Recipient input mode -->
          <div class="bulk-mode-toggle" role="tablist">
            <button
              type="button"
              class="bulk-mode-btn"
              :class="{ active: bulkEmail.mode === 'reference' }"
              role="tab"
              :aria-selected="bulkEmail.mode === 'reference'"
              @click="setBulkMode('reference')"
            >
              Enter reference numbers
            </button>
            <button
              type="button"
              class="bulk-mode-btn"
              :class="{ active: bulkEmail.mode === 'csv' }"
              role="tab"
              :aria-selected="bulkEmail.mode === 'csv'"
              @click="setBulkMode('csv')"
            >
              Import CSV
            </button>
          </div>

          <!-- Reference-number mode -->
          <template v-if="bulkEmail.mode === 'reference'">
            <label>
              Reference numbers / students
              <div class="recipient-multiselect" @click="focusBccInput">
                <span
                  v-for="s in bulkEmail.students"
                  :key="s.key"
                  class="recipient-chip"
                  :class="{
                    'recipient-chip-warn':
                      s.status === 'notfound' || s.status === 'noemail',
                  }"
                >
                  <span class="recipient-chip-label" :title="chipTitle(s)">{{
                    s.name
                  }}</span>
                  <button
                    type="button"
                    class="recipient-chip-remove"
                    @click.stop="removeStudent(s.key)"
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
                    placeholder="Type reference number, student name or email…"
                    autocomplete="off"
                    @input="onBccSearch"
                    @keydown.enter.prevent="addStudentFromInput"
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
                      @mousedown.prevent="selectStudent(r)"
                    >
                      <span class="rd-name">{{ r.name }}</span>
                      <span class="rd-email">{{ r.email }}</span>
                    </button>
                  </div>
                </div>
              </div>
              <div class="composer-attachment-actions" style="margin-top: 6px">
                <span v-if="bulkResolving" class="muted">resolving…</span>
                <span class="muted" style="margin-left: auto">
                  {{ bulkEmailStudentCount }} student{{
                    bulkEmailStudentCount === 1 ? "" : "s"
                  }}
                </span>
              </div>
            </label>
          </template>

          <!-- CSV mode -->
          <template v-else>
            <label>
              Import students from CSV
              <div class="composer-attachment-actions" style="margin-top: 6px">
                <button
                  type="button"
                  class="btn secondary"
                  :disabled="bulkEmailUploading"
                  @click="bulkEmailCsvInput?.click()"
                >
                  {{ bulkEmailUploading ? "Importing..." : "Import CSV" }}
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
                  {{ bulkEmailStudentCount }} student{{
                    bulkEmailStudentCount === 1 ? "" : "s"
                  }}
                </span>
              </div>
              <div
                v-if="bulkEmail.students.length"
                class="bulk-email-chip-list"
              >
                <span
                  v-for="s in bulkEmail.students"
                  :key="s.key"
                  class="recipient-chip"
                  :class="{ 'recipient-chip-warn': s.status === 'noemail' }"
                >
                  <span class="recipient-chip-label" :title="chipTitle(s)">{{
                    s.name
                  }}</span>
                  <button
                    type="button"
                    class="recipient-chip-remove"
                    @click.stop="removeStudent(s.key)"
                  >
                    ×
                  </button>
                </span>
              </div>
            </label>
          </template>

          <!-- Recipient options (both modes): choose students and/or guardians -->
          <div v-if="bulkEmail.students.length" class="bulk-recipient-options">
            <label class="bulk-email-guardian-toggle">
              <input v-model="includeGuardians" type="checkbox" />
              Include guardian emails
              <span
                v-if="guardianCountLabel"
                class="muted"
                style="margin-left: 6px"
                >{{ guardianCountLabel }}</span
              >
            </label>
            <label class="bulk-email-guardian-toggle">
              <input v-model="excludeStudent" type="checkbox" />
              Exclude student email (send to guardians only)
            </label>
          </div>
          <label v-if="recipientsPreview">
            Recipients
            <textarea
              class="recipients-preview"
              :value="recipientsPreview"
              rows="4"
              readonly
            ></textarea>
          </label>

          <!-- Concise hint: any Student field works as a merge token. -->
          <div class="merge-fields-hint">
            <span class="muted">
              Tip: type
              <code class="merge-field-chip">{{
                mergeFieldToken("field_name")
              }}</code>
              in the subject or body to auto-fill any student detail (e.g.
              <code class="merge-field-chip">{{
                mergeFieldToken("first_name")
              }}</code
              >,
              <code class="merge-field-chip">{{
                mergeFieldToken("last_name")
              }}</code
              >). It's filled per student and left blank if the student has no
              value.
            </span>
          </div>
          <!-- What the composed email actually uses, with unknown-token warning -->
          <div v-if="templateTokens.length" class="merge-fields-hint">
            This email uses:
            <code
              v-for="t in templateTokens"
              :key="t.token"
              class="merge-field-chip"
              :class="{ 'merge-field-chip-bad': !t.recognised }"
              :title="
                t.recognised
                  ? 'Auto-filled per student'
                  : 'Not a known field — will be blank'
              "
              >{{ mergeFieldToken(t.token) }}</code
            >
            <span v-if="unknownTokens.length" class="bulk-token-warn"
              >⚠ {{ unknownTokens.join(", ") }} won't auto-fill — check the
              field name.</span
            >
          </div>

          <label>
            Subject
            <input
              v-model="bulkEmail.subject"
              type="text"
              placeholder="Email subject (auto-filled when you pick an Email Template)"
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
            Message
            <TinyMceEditor
              v-model="bulkEmail.message"
              :min-height="240"
              placeholder="Compose the email message"
              :enable-email-template="true"
              :enable-attach="true"
              @attach="bulkEmailAttachmentInput?.click()"
              @template-subject="applyTemplateSubjectToBulkEmail"
              @email-template-selected="applyEmailTemplateToBulkEmail"
            />
            <input
              ref="bulkEmailAttachmentInput"
              type="file"
              class="hidden-file-input"
              multiple
              @change="handleBulkEmailAttachments"
            />
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
            :disabled="bulkEmailSending || !bulkEmailStudentCount"
            @click="sendBulkEmail"
          >
            {{
              bulkEmailSending
                ? "Sending..."
                : `Send to ${bulkEmailStudentCount} student${
                    bulkEmailStudentCount === 1 ? "" : "s"
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
const bulkResolving = ref(false);
const bulkEmail = reactive({
  mode: "reference", // "reference" (type ref numbers) | "csv" (import file)
  subject: "",
  ticket_type: "",
  message: "",
  cc: [],
  // Reference-mode raw inputs (reference numbers / student names / emails).
  tokens: [],
  // Resolved recipients — one per student (or free email). Each becomes ONE
  // ticket + ONE email to [student + guardians]:
  //   { key, token, student, name, email, guardian_emails: [], data: {}, status }
  students: [],
  attachments: [],
  mergeFields: [], // student fields usable as {{field}}
  csvImported: false,
});
const includeGuardians = ref(false);
// Recipient toggles (both modes): exclude the student's own email (send to
// guardians only). includeGuardians defaults on in CSV mode (see setBulkMode).
const excludeStudent = ref(false);
// All Student doctype fields, fetched once when the bulk modal opens — used to
// show the full merge-field list and to flag {{tokens}} that won't auto-fill.
const studentMergeFields = ref([]);
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

// Cross-view "tickets changed" signal — TicketsView injects it and reloads when it
// bumps, so a non-blocking send is reflected in the list once the request finishes.
const ticketsRefreshSignal = ref(0);
provide("unityTicketsRefresh", ticketsRefreshSignal);
function signalTicketsRefresh() {
  ticketsRefreshSignal.value += 1;
}

// Global, non-blocking notice (toast) shown above everything. Lets the send
// composers close immediately and report progress/outcome out-of-band, so the UI
// never hangs on a slow send.
const globalNotice = ref(null); // { text, type: 'info' | 'success' | 'error' }
let globalNoticeTimer = null;
function showGlobalNotice(text, type = "info", autoDismissMs = 0) {
  if (globalNoticeTimer) {
    clearTimeout(globalNoticeTimer);
    globalNoticeTimer = null;
  }
  globalNotice.value = { text, type };
  if (autoDismissMs > 0) {
    globalNoticeTimer = setTimeout(() => {
      globalNotice.value = null;
    }, autoDismissMs);
  }
}
function dismissGlobalNotice() {
  if (globalNoticeTimer) clearTimeout(globalNoticeTimer);
  globalNotice.value = null;
}

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

// Build a "{{field}}" label without putting literal }} in the template (Vue's
// mustache parser would close the interpolation at the first }}).
function mergeFieldToken(field) {
  return "{{" + field + "}}";
}

// Email Template is the primary source — replace BOTH subject and body. The
// editor already swapped its content; we sync v-model + the subject field here.
function applyEmailTemplateToBulkEmail(payload) {
  const subject = payload && payload.subject;
  const body = payload && payload.body;
  if (typeof subject === "string" && subject.trim()) {
    bulkEmail.subject = subject;
  }
  if (typeof body === "string") {
    bulkEmail.message = body;
  }
}

// Create-Ticket composer: same as bulk — Email Template drives subject + body.
// The single send does NOT render Jinja, so any {{placeholders}} load as-is for
// the agent to fill before sending (matches the canned-response button).
function applyEmailTemplateToCreateTicket(payload) {
  const subject = payload && payload.subject;
  const body = payload && payload.body;
  if (typeof subject === "string" && subject.trim()) {
    composer.subject = subject;
  }
  if (typeof body === "string") {
    composer.message = body;
  }
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
  composerError.value = "";
  composerWarning.value = "";
  if (!composer.subject || !composer.subject.trim()) {
    composerError.value = "Subject is required.";
    return;
  }
  if (!composer.ticket_type) {
    composerError.value = "Ticket Type is required.";
    return;
  }
  // Snapshot the payload BEFORE closing (closeComposer resets the form). Then close
  // the composer immediately and report progress out-of-band — the request runs in
  // the background so the UI never hangs on a slow send. The new ticket appears in
  // the list once the request finishes (or sooner — it's committed early server-side).
  const payload = {
    subject: composer.subject,
    raised_by: composer.raised_by,
    message: composer.message,
    priority: composer.priority,
    ticket_type: composer.ticket_type,
    assignee: composer.assignee,
    attachments: composer.attachments.map((attachment) => attachment.name),
  };
  closeComposer();
  showGlobalNotice(
    "Sending email… the new ticket will appear in your list shortly.",
    "info"
  );
  try {
    const result = await call(
      "helpdesk.api.unity_helpdesk_ext.create_ticket",
      payload
    );
    signalTicketsRefresh();
    if (result?.warning) {
      showGlobalNotice(result.warning, "error", 9000);
    } else {
      showGlobalNotice("Ticket created and email sent.", "success", 5000);
    }
  } catch (err) {
    showGlobalNotice(
      "Ticket creation failed: " + (err?.message || err),
      "error",
      10000
    );
  }
}

// --- Bulk email ---
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Deliverable emails for one student: the student + (optionally) their guardians,
// deduped WITHIN the student. Siblings sharing a guardian still each get their own
// personalised ticket that mails that guardian.
function studentGroupEmails(s, includeStudent, includeGuardians) {
  const out = [];
  const seen = new Set();
  const studentEmail = (s.email || "").toLowerCase().trim();
  const push = (e) => {
    const x = (e || "").toLowerCase().trim();
    if (x && EMAIL_REGEX.test(x) && !seen.has(x)) {
      seen.add(x);
      out.push(x);
    }
  };
  if (includeStudent && studentEmail) push(studentEmail);
  if (includeGuardians) {
    for (const g of s.guardian_emails || []) {
      // Never re-introduce the student's own email via the guardian list, so
      // "Exclude student email" truly excludes it even if the data lists it twice.
      if ((g || "").toLowerCase().trim() === studentEmail) continue;
      push(g);
    }
  }
  return out;
}

// Current recipient selection (applies to both reference and CSV modes).
const recipientFlags = computed(() => ({
  includeStudent: !excludeStudent.value,
  includeGuardians: includeGuardians.value,
}));

// One send group per resolved student (or free email) -> one ticket + one email.
const bulkEmailGroups = computed(() => {
  const { includeStudent, includeGuardians: incG } = recipientFlags.value;
  const groups = [];
  for (const s of bulkEmail.students) {
    if (s.status === "notfound") continue;
    const emails = studentGroupEmails(s, includeStudent, incG);
    if (!emails.length) continue;
    groups.push({ student: s.student || null, emails, data: s.data || {} });
  }
  return groups;
});
const bulkEmailStudentCount = computed(() => bulkEmailGroups.value.length);
const guardianCountLabel = computed(() => {
  if (!includeGuardians.value) return "";
  const total = bulkEmail.students.reduce(
    (n, s) => n + (s.guardian_emails || []).length,
    0
  );
  return total ? `${total} guardian email(s) will be included` : "";
});

// Read-only preview of exactly who gets emailed — one line per student.
const recipientsPreview = computed(() => {
  const { includeStudent, includeGuardians: incG } = recipientFlags.value;
  return bulkEmail.students
    .filter((s) => s.status !== "notfound")
    .map((s) => {
      const emails = studentGroupEmails(s, includeStudent, incG);
      return emails.length ? `${s.name}: ${emails.join(", ")}` : null;
    })
    .filter(Boolean)
    .join("\n");
});

// Tokens ({{x}}) the composed subject/body uses, each flagged recognised or not —
// recognised = a Student fieldname, a CSV column, or a resolved student's data key.
const templateTokens = computed(() => {
  const known = new Set();
  studentMergeFields.value.forEach((f) => known.add(f.fieldname));
  (bulkEmail.mergeFields || []).forEach((f) => known.add(f));
  bulkEmail.students.forEach((s) =>
    Object.keys(s.data || {}).forEach((k) => known.add(k))
  );
  const text = `${bulkEmail.subject || ""} ${bulkEmail.message || ""}`;
  const seen = new Set();
  const out = [];
  // Local regex (matchAll) — no shared lastIndex mutation, so the computed stays
  // side-effect-free.
  for (const match of text.matchAll(/\{\{\s*([\w. ]+?)\s*\}\}/g)) {
    const token = (match[1] || "").trim();
    if (!token || seen.has(token)) continue;
    seen.add(token);
    out.push({ token, recognised: known.has(token) });
  }
  return out;
});
const unknownTokens = computed(() =>
  templateTokens.value.filter((t) => !t.recognised).map((t) => t.token)
);

function setBulkMode(mode) {
  if (bulkEmail.mode === mode) return;
  bulkEmail.mode = mode;
  bulkEmail.tokens = [];
  bulkEmail.students = [];
  bulkEmail.mergeFields = [];
  bulkEmail.csvImported = false;
  bccSearchQuery.value = "";
  bccResults.value = [];
  bulkEmailWarning.value = "";
  bulkEmailError.value = "";
  // CSV imports historically include guardians by default; reference entry starts
  // students-only. The agent can change both via the recipient toggles.
  includeGuardians.value = mode === "csv";
  excludeStudent.value = false;
}

function chipTitle(s) {
  const parts = [s.email || "(no email on file)"];
  if ((s.guardian_emails || []).length) {
    parts.push(`guardians: ${s.guardian_emails.join(", ")}`);
  }
  return parts.join(" · ");
}

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

// Add a reference number / student name / email the agent typed or picked. The
// backend resolve_bulk_email_students turns each token into a student (with
// guardians + merge data) or a free email.
function addStudentToken(token) {
  const t = (token || "").trim();
  if (!t) return;
  if (bulkEmail.tokens.some((x) => x.toLowerCase() === t.toLowerCase())) return;
  bulkEmail.tokens.push(t);
  resolveStudents();
}

function selectStudent(r) {
  addStudentToken(r.email || r.name || "");
  bccSearchQuery.value = "";
  bccResults.value = [];
  bccInputRef.value?.focus();
}

function addStudentFromInput() {
  const val = bccSearchQuery.value.trim();
  if (!val) return;
  addStudentToken(val);
  bccSearchQuery.value = "";
  bccResults.value = [];
}

function removeStudent(key) {
  const s = bulkEmail.students.find((x) => x.key === key);
  if (bulkEmail.mode === "reference") {
    if (s && s.token) {
      bulkEmail.tokens = bulkEmail.tokens.filter(
        (t) => t.toLowerCase() !== s.token.toLowerCase()
      );
    }
    resolveStudents();
  } else {
    bulkEmail.students = bulkEmail.students.filter((x) => x.key !== key);
  }
}

function onBccBackspace() {
  if (!bccSearchQuery.value && bulkEmail.students.length) {
    removeStudent(bulkEmail.students[bulkEmail.students.length - 1].key);
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

// Resolve the typed reference numbers / names / emails into students (with their
// guardians + merge data) via the backend. Rebuilds bulkEmail.students from the
// raw token list so add/remove stays in sync.
async function resolveStudents() {
  const tokens = bulkEmail.tokens.slice();
  if (!tokens.length) {
    bulkEmail.students = [];
    bulkEmail.mergeFields = [];
    bulkEmailWarning.value = "";
    return;
  }
  bulkResolving.value = true;
  try {
    const result = await call(
      "helpdesk.api.unity_helpdesk.resolve_bulk_email_students",
      { refs: JSON.stringify(tokens) }
    );
    const students = result?.students || [];
    const out = [];
    const seenStudents = new Set();
    const seenFree = new Set();
    for (const token of tokens) {
      const t = token.toLowerCase();
      const st = students.find(
        (s) =>
          String(s.student || "").toLowerCase() === t ||
          String((s.data && s.data.reference_number) || "").toLowerCase() ===
            t ||
          String(s.email || "").toLowerCase() === t
      );
      if (st) {
        if (seenStudents.has(st.student)) continue;
        seenStudents.add(st.student);
        out.push({
          key: `s:${st.student}`,
          token,
          student: st.student,
          name: st.student_name || st.student,
          email: st.email || "",
          guardian_emails: st.guardian_emails || [],
          data: st.data || {},
          status: st.has_email ? "student" : "noemail",
        });
      } else if (EMAIL_REGEX.test(token)) {
        if (seenFree.has(t)) continue;
        seenFree.add(t);
        out.push({
          key: `f:${t}`,
          token,
          student: null,
          name: token,
          email: t,
          guardian_emails: [],
          data: {},
          status: "free",
        });
      } else {
        out.push({
          key: `n:${t}`,
          token,
          student: null,
          name: token,
          email: "",
          guardian_emails: [],
          data: {},
          status: "notfound",
        });
      }
    }
    bulkEmail.students = out;
    bulkEmail.mergeFields = result?.merge_fields || [];
    const notFound = out
      .filter((s) => s.status === "notfound")
      .map((s) => s.token);
    const noEmail = out
      .filter((s) => s.status === "noemail")
      .map((s) => s.name);
    let warn = "";
    if (notFound.length)
      warn += `Couldn't find ${notFound.length} reference(s): ${notFound
        .slice(0, 5)
        .join(", ")}${notFound.length > 5 ? ", …" : ""}. `;
    if (noEmail.length)
      warn += `${noEmail.length} student(s) have no email on file${
        includeGuardians.value ? " — guardians will still be emailed" : ""
      }.`;
    bulkEmailWarning.value = warn.trim();
  } catch (err) {
    if (err instanceof AuthRedirectError || err?.code === "AUTH_REDIRECT")
      return;
    bulkEmailError.value = err?.message || "Couldn't resolve students.";
  } finally {
    bulkResolving.value = false;
  }
}

function openBulkEmailModal() {
  resetBulkEmail();
  openBulkEmail.value = true;
  loadStudentMergeFields();
}

// Fetch the full Student field list once (cached for the session) so the composer
// can show all available {{fields}} and flag unknown template tokens.
async function loadStudentMergeFields() {
  if (studentMergeFields.value.length) return;
  try {
    const fields = await call(
      "helpdesk.api.unity_helpdesk.get_student_merge_fields"
    );
    studentMergeFields.value = Array.isArray(fields) ? fields : [];
  } catch {
    studentMergeFields.value = [];
  }
}

function resetBulkEmail() {
  bulkEmailSending.value = false;
  bulkEmailUploading.value = false;
  bulkResolving.value = false;
  bulkEmailError.value = "";
  bulkEmailWarning.value = "";
  bulkEmail.mode = "reference";
  bulkEmail.subject = "";
  bulkEmail.ticket_type = "";
  bulkEmail.message = "";
  bulkEmail.cc = [];
  bulkEmail.tokens = [];
  bulkEmail.students = [];
  bulkEmail.attachments = [];
  bulkEmail.mergeFields = [];
  bulkEmail.csvImported = false;
  ccInputQuery.value = "";
  bccSearchQuery.value = "";
  bccResults.value = [];
  includeGuardians.value = false;
  excludeStudent.value = false;
}

function closeBulkEmail() {
  openBulkEmail.value = false;
  resetBulkEmail();
}

async function handleBulkEmailCsv(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  bulkEmailError.value = "";
  bulkEmailWarning.value = "";
  bulkEmailUploading.value = true;
  try {
    const text = await file.text();
    // Parse on the backend (robust to quoted commas) — returns headers + per-row
    // data so we get the full mail-merge context, not just the email column.
    const result = await call(
      "helpdesk.api.unity_helpdesk_ext.parse_bulk_email_csv",
      { content: text }
    );
    const rows = result?.rows || [];
    if (!rows.length) {
      bulkEmailError.value =
        "No students resolved from the CSV. Include an 'id' (student) column — the email, details and guardians are looked up from it.";
      return;
    }
    // Reconstruct one entry per student: the student row (name) + its guardian
    // rows ("Name (guardian)"), linked by data._student. Each becomes one ticket.
    const byStudent = {};
    const order = [];
    for (const row of rows) {
      const sid = (row.data && row.data._student) || "";
      const email = (row.email || "").toLowerCase().trim();
      const key = sid ? `s:${sid}` : `f:${email}`;
      if (!byStudent[key]) {
        byStudent[key] = {
          key,
          token: "",
          student: sid || null,
          name: "",
          email: "",
          guardian_emails: [],
          data: {},
          status: sid ? "student" : "free",
        };
        order.push(key);
      }
      const g = byStudent[key];
      const isGuardian = /\(guardian\)\s*$/i.test(row.name || "");
      if (isGuardian) {
        if (email) g.guardian_emails.push(email);
      } else {
        g.email = email;
        g.name = row.name || email;
        const data = { ...(row.data || {}) };
        delete data._student;
        g.data = data;
        if (!email) g.status = "noemail";
      }
      if (!g.name) g.name = row.name || email;
    }
    bulkEmail.students = order.map((k) => byStudent[k]);
    bulkEmail.mergeFields = result.merge_fields || [];
    bulkEmail.csvImported = true;
    let note = `${result.student_count || 0} student${
      (result.student_count || 0) === 1 ? "" : "s"
    } loaded`;
    if (result.guardian_count)
      note += ` + ${result.guardian_count} guardian(s)`;
    note += ".";
    if (result.unmatched_count) note += ` ${result.unmatched_count} not found.`;
    if (result.school_mismatch_count)
      note += ` ${result.school_mismatch_count} wrong-school skipped.`;
    if (result.no_email_count)
      note += ` ${result.no_email_count} without an email.`;
    if (result.duplicate_count)
      note += ` ${result.duplicate_count} duplicate(s) skipped.`;
    if (result.truncated)
      note += ` Only the first ${rows.length} recipients were kept — split the CSV to send the rest.`;
    bulkEmailWarning.value = note;
  } catch (err) {
    bulkEmailError.value = err.message || "CSV import failed.";
  } finally {
    bulkEmailUploading.value = false;
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
  // One group per student (or free email): the student and/or their guardians,
  // per the recipient toggles.
  const groups = bulkEmailGroups.value;
  if (!groups.length) {
    const hasStudents = bulkEmail.students.some((s) => s.status !== "notfound");
    if (hasStudents && excludeStudent.value && !includeGuardians.value) {
      bulkEmailError.value =
        "Recipient options exclude everyone — enable “Include guardian emails” or uncheck “Exclude student email”.";
    } else if (hasStudents) {
      bulkEmailError.value =
        "No deliverable email for the selected recipients — check Include guardians / Exclude student.";
    } else {
      bulkEmailError.value =
        bulkEmail.mode === "csv"
          ? "Import a CSV with at least one student that has an email."
          : "Add at least one student (reference number) or recipient before sending.";
    }
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
  // Build the payload BEFORE closing the composer (closeBulkEmail resets the form).
  const ccEmails = bulkEmail.cc.map((c) => c.email);
  const payload = {
    subject: bulkEmail.subject,
    message: bulkEmail.message,
    ticket_type: bulkEmail.ticket_type,
    groups: JSON.stringify(groups),
    cc: ccEmails.length ? JSON.stringify(ccEmails) : null,
    attachments: JSON.stringify(
      bulkEmail.attachments.map((attachment) => attachment.name)
    ),
  };
  const studentCount = groups.length;

  // Non-blocking: close the composer immediately and report progress out-of-band so
  // the UI never hangs on a slow send. The per-student tickets appear in the list
  // as they are created (each is committed early server-side).
  closeBulkEmail();
  showGlobalNotice(
    "Sending email… your tickets will appear in the list shortly.",
    "info"
  );
  try {
    const result = await call(
      "helpdesk.api.unity_helpdesk_ext.bulk_send_email",
      payload
    );
    signalTicketsRefresh();
    if (result?.warning) {
      showGlobalNotice(result.warning, "error", 9000);
    } else {
      const n = result?.ticket_count || result?.student_count || studentCount;
      const noun = n === 1 ? "ticket" : "tickets";
      let message =
        result?.instant === false
          ? `Bulk email queued — ${n} ${noun} created, sending shortly.`
          : `Bulk email sent — ${n} ${noun} created.`;
      if (result?.invalid_count) {
        message += ` ${result.invalid_count} invalid address(es) skipped.`;
      }
      showGlobalNotice(message, "success", 6000);
    }
  } catch (err) {
    showGlobalNotice(
      "Bulk email failed: " + (err?.message || err),
      "error",
      10000
    );
  }
}
</script>
