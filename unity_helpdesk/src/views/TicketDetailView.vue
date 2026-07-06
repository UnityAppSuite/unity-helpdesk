<template>
  <section class="page">
    <div class="toolbar">
      <button class="btn secondary" @click="goBackToList">
        Back to Tickets
      </button>
      <button
        v-if="prevTicketId"
        class="btn secondary nav-btn"
        title="Previous ticket"
        @click="goToPrevTicket"
      >
        ← Prev
      </button>
      <button
        v-if="nextTicketId"
        class="btn secondary nav-btn"
        title="Next ticket"
        @click="goToNextTicket"
      >
        Next →
      </button>
      <strong v-if="ticket.name">#{{ ticket.name }}</strong>
      <span
        v-if="ticket.status_indicator"
        class="badge"
        :class="ticket.status_indicator.color"
      >
        {{ ticket.status_indicator.label }}
      </span>
      <button
        v-if="ticket.status !== 'Closed'"
        class="btn"
        :disabled="saving"
        @click="markClosed"
      >
        Mark Closed
      </button>
    </div>

    <div v-if="reloading" class="reloading-indicator">
      <span class="reload-spinner" aria-hidden="true"></span>
      <span>Reloading…</span>
    </div>
    <div v-if="reloadPrompt" class="reload-prompt">
      <span>Couldn't load this ticket.</span>
      <button type="button" class="btn secondary" @click="loadTicket()">
        Retry
      </button>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="notice" class="warning-banner">{{ notice }}</p>
    <div v-else-if="loading" class="detail-skeleton" aria-hidden="true">
      <div class="skeleton-block skeleton-hero"></div>
      <div class="detail-skeleton-grid">
        <div class="skeleton-stack">
          <div class="skeleton-block skeleton-card"></div>
          <div class="skeleton-block skeleton-card skeleton-card-lg"></div>
          <div class="skeleton-block skeleton-card"></div>
        </div>
        <div class="skeleton-stack">
          <div class="skeleton-block skeleton-side"></div>
          <div class="skeleton-block skeleton-side"></div>
          <div class="skeleton-block skeleton-side skeleton-side-lg"></div>
        </div>
      </div>
    </div>

    <div v-else class="detail-grid">
      <div class="detail-main">
        <div v-if="ticket.custom_replied_to_ticket" class="reply-origin-banner">
          <span class="reply-origin-banner__label"
            >Reply to previous ticket</span
          >
          <button
            type="button"
            class="link-btn"
            @click="openSpaTicket(ticket.custom_replied_to_ticket)"
          >
            #{{ ticket.custom_replied_to_ticket }}
          </button>
          <span v-if="repliedToSummary" class="reply-origin-banner__meta">
            · {{ repliedToSummary }}
          </span>
        </div>
        <div class="email-hero">
          <h2>{{ ticket.subject || "No subject" }}</h2>
          <p>
            {{ ticket.raised_by || "Unknown sender" }} ·
            {{ formatDateTime(ticket.creation) }}
          </p>
          <span class="priority" :class="priorityClass(ticket.priority)">
            {{ ticket.priority || "No priority" }}
          </span>
        </div>

        <section
          v-if="
            structuredStudents.length > 0 ||
            (!ticket.custom_is_bulk_email &&
              (shouldRenderStructuredStudentContext ||
                showLegacyStudentSection))
          "
          class="detail-section"
        >
          <button
            class="section-toggle section-toggle--right"
            type="button"
            :aria-expanded="studentDetailsOpen"
            @click="studentDetailsOpen = !studentDetailsOpen"
          >
            <h3>Student Details</h3>
            <span class="section-toggle__chevron" aria-hidden="true">
              {{ studentDetailsOpen ? "▲" : "▼" }}
            </span>
          </button>
          <div
            v-if="studentDetailsOpen && shouldRenderStructuredStudentContext"
            class="detail-body stack"
          >
            <div
              class="student-context-banner"
              :class="studentContextBannerClass(studentContext)"
            >
              <strong>{{ studentContextBanner(studentContext) }}</strong>
              <p v-if="studentContext.message">{{ studentContext.message }}</p>
            </div>

            <div v-if="structuredStudents.length" class="scroll-x">
              <table class="compact-info-table student-context-table">
                <thead>
                  <tr>
                    <th>Detail</th>
                    <th
                      v-for="student in structuredStudentColumns"
                      :key="student.key"
                    >
                      <div class="student-context-table__heading">
                        <strong>{{ student.name }}</strong>
                        <small>
                          <a
                            :href="`/app/student/${student.id}`"
                            target="_blank"
                            rel="noopener noreferrer"
                            class="student-id-link"
                            >{{ student.id }}</a
                          >
                          <span v-if="student.academicYear" class="student-ay"
                            >- {{ student.academicYear }}</span
                          >
                        </small>
                        <span v-if="student.role" class="student-context-pill">
                          {{ student.role }}
                        </span>
                      </div>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in structuredStudentRows" :key="row.field">
                    <th class="row-heading">{{ row.field }}</th>
                    <td
                      v-for="value in row.values"
                      :key="`${row.field}-${value.key}`"
                      v-html="sanitize(value.html || '-')"
                    ></td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div
              v-if="showGuardianTable && guardianRows.length"
              class="scroll-x guardian-context"
            >
              <h4 class="guardian-context__title">Guardian Details</h4>
              <table class="compact-info-table guardian-context-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Mobile</th>
                    <th>Email</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="g in guardianRows" :key="g.key">
                    <td>{{ g.name || "-" }}</td>
                    <td>
                      <a v-if="g.mobile" :href="`tel:${g.mobile}`">{{
                        g.mobile
                      }}</a>
                      <span v-else>-</span>
                      <small
                        v-if="g.alternate_mobile"
                        class="guardian-context-table__alt"
                      >
                        alt:
                        <a :href="`tel:${g.alternate_mobile}`">{{
                          g.alternate_mobile
                        }}</a>
                      </small>
                    </td>
                    <td>
                      <a v-if="g.email" :href="`mailto:${g.email}`">{{
                        g.email
                      }}</a>
                      <span v-else>-</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div
            v-else-if="studentDetailsOpen && studentRows.length"
            class="detail-body compact-body"
          >
            <div class="scroll-x">
              <table class="compact-info-table">
                <thead>
                  <tr>
                    <th>Detail</th>
                    <th
                      v-for="(student, index) in studentColumns"
                      :key="student.key"
                    >
                      {{ student.label || `Student ${index + 1}` }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in studentTransposedRows" :key="row.field">
                    <th class="row-heading">{{ row.field }}</th>
                    <td
                      v-for="value in row.values"
                      :key="`${row.field}-${value.key}`"
                      v-html="sanitize(value.html || '-')"
                    ></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div
            v-else-if="studentDetailsOpen"
            class="detail-body safe-html compact-html"
            v-html="sanitize(ticket.custom_list_of_student)"
          ></div>
        </section>

        <section v-if="showLegacyFeeSection" class="detail-section">
          <h3>Fees Details</h3>
          <div v-if="feeRows.length" class="detail-body compact-body">
            <div class="scroll-x">
              <table class="compact-info-table">
                <thead>
                  <tr>
                    <th v-for="field in feeFields" :key="field">{{ field }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(fee, index) in feeRows" :key="fee.__ref || index">
                    <td
                      v-for="field in feeFields"
                      :key="field"
                      v-html="sanitize(fee[field] || '-')"
                    ></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <template v-else>
            <div
              v-if="ticket.custom_all_fees_details_of_students"
              class="detail-body safe-html compact-html"
              v-html="sanitize(ticket.custom_all_fees_details_of_students)"
            ></div>
            <div
              v-if="ticket.custom_payment_schedule"
              class="detail-body safe-html compact-html"
              v-html="sanitize(ticket.custom_payment_schedule)"
            ></div>
          </template>
        </section>

        <section v-if="hasAdditionalDetails" class="detail-section">
          <button
            class="section-toggle section-toggle--right"
            type="button"
            :aria-expanded="additionalOpen"
            @click="additionalOpen = !additionalOpen"
          >
            <h3>Previous Ticket Details</h3>
            <small>
              {{ visiblePreviousTickets.length }} of
              {{ previousTicketRows.length }} previous tickets
            </small>
            <span class="section-toggle__chevron" aria-hidden="true">
              {{ additionalOpen ? "▲" : "▼" }}
            </span>
          </button>
          <div
            v-if="additionalOpen"
            class="detail-body stack previous-tickets-body"
          >
            <div class="thread-filters thread-filters-compact">
              <label>
                Created From
                <input v-model="additionalFilters.from" type="date" />
              </label>
              <label>
                Created To
                <input v-model="additionalFilters.to" type="date" />
              </label>
              <button
                class="btn secondary"
                type="button"
                @click="clearAdditionalFilters"
              >
                Clear
              </button>
            </div>
            <div class="stack">
              <div
                v-if="ticket.custom_student_remark"
                class="safe-html compact-html"
                v-html="sanitize(ticket.custom_student_remark)"
              ></div>
              <div
                v-if="previousTicketRows.length"
                class="scroll-x previous-ticket-scroll"
              >
                <table class="compact-info-table previous-ticket-table">
                  <thead>
                    <tr>
                      <th>Ticket No</th>
                      <th>Subject</th>
                      <th>Ticket Type</th>
                      <th>Created On</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in visiblePreviousTickets"
                      :key="row.name"
                      :class="previousTicketRowClass(row)"
                    >
                      <td>
                        <button
                          class="link-btn"
                          type="button"
                          @click="openSpaTicket(row.name)"
                        >
                          {{ row.name }}
                        </button>
                        <span
                          v-if="isOutgoingTicket(row)"
                          class="ticket-origin-tag ticket-origin-tag--outgoing"
                          :title="
                            row.custom_is_bulk_email
                              ? 'Bulk email we sent'
                              : 'Ticket sent from portal'
                          "
                        >
                          Sent
                        </span>
                        <span
                          v-else-if="row.custom_replied_to_ticket"
                          class="ticket-origin-tag ticket-origin-tag--reply"
                          :title="`Reply to ${row.custom_replied_to_ticket}`"
                        >
                          Reply
                        </span>
                      </td>
                      <td>{{ row.subject || "-" }}</td>
                      <td>
                        <span v-if="row.ticket_type" class="ticket-type-pill">
                          <span
                            class="ticket-type-dot"
                            :style="{
                              background: ticketTypeColor(row.ticket_type),
                            }"
                          ></span>
                          {{ row.ticket_type }}
                        </span>
                        <span v-else>-</span>
                      </td>
                      <td>{{ formatDate(row.creation) }}</td>
                      <td>{{ row.status || "-" }}</td>
                    </tr>
                  </tbody>
                </table>
                <p
                  v-if="!visiblePreviousTickets.length"
                  class="muted inline-empty"
                >
                  No previous tickets found in this date range.
                </p>
                <button
                  v-if="filteredPreviousTickets.length > 5"
                  class="btn secondary"
                  type="button"
                  @click="showAllPreviousTickets = !showAllPreviousTickets"
                >
                  {{
                    showAllPreviousTickets
                      ? "Show less"
                      : `Show all ${filteredPreviousTickets.length}`
                  }}
                </button>
              </div>
              <div
                v-else-if="previousTicketsHtml"
                class="safe-html compact-html"
                v-html="sanitize(previousTicketsHtml)"
              ></div>
              <div
                v-if="ticket.custom_previous_ticket_details"
                class="safe-html compact-html"
                v-html="sanitize(ticket.custom_previous_ticket_details)"
              ></div>
            </div>
          </div>
        </section>

        <!-- Bulk email: always show collapsible recipients + message summary -->
        <section
          v-if="ticket.custom_is_bulk_email && ticket.description"
          class="detail-section bulk-audit-section"
        >
          <h3>Bulk Email — Recipients &amp; Message</h3>
          <div
            class="detail-body safe-html"
            v-html="sanitize(ticket.description)"
          ></div>
        </section>

        <section v-if="timeline.length" class="detail-section">
          <h3>Email Thread</h3>
          <div class="chat-thread">
            <div
              v-for="item in timeline"
              :key="item.name"
              class="chat-msg"
              :class="{
                'chat-msg--agent':
                  item._type === 'comm' && item.sent_or_received === 'Sent',
                'chat-msg--customer':
                  item._type === 'comm' && item.sent_or_received === 'Received',
                'chat-msg--comment': item._type === 'comment',
              }"
            >
              <div class="chat-msg-meta">
                <span class="chat-msg-label">
                  <template v-if="item._type === 'comment'">Note</template>
                  <template v-else-if="item.sent_or_received === 'Sent'"
                    >Agent</template
                  >
                  <template v-else>Customer</template>
                </span>
                <span v-if="threadAuthor(item)" class="chat-msg-author">{{
                  threadAuthor(item)
                }}</span>
                <span class="chat-msg-time">{{
                  formatDateTime(item.creation)
                }}</span>
              </div>
              <div
                class="chat-msg-body safe-html"
                v-html="sanitize(threadContent(item))"
              ></div>
              <div
                v-if="item.attachments?.length"
                class="attachment-list attachment-list-thread"
              >
                <a
                  v-for="attachment in item.attachments"
                  :key="attachment.name"
                  class="attachment-chip"
                  :href="attachment.file_url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ attachment.file_name || attachment.name }}
                </a>
              </div>
            </div>
          </div>
        </section>

        <section class="detail-section">
          <div class="compose-tabs">
            <button
              class="compose-tab"
              :class="{ active: composeMode === 'reply' }"
              type="button"
              @click="
                composeMode = 'reply';
                actionError = '';
              "
            >
              Reply
            </button>
            <button
              class="compose-tab compose-tab-comment"
              :class="{ active: composeMode === 'comment' }"
              type="button"
              @click="
                composeMode = 'comment';
                actionError = '';
              "
            >
              Internal Note
            </button>
          </div>
          <div class="detail-body stack">
            <p v-if="actionError" class="error">{{ actionError }}</p>
            <TinyMceEditor
              ref="editorRef"
              v-model="composerHtml"
              :min-height="260"
              :ticket-name="props.ticketId"
              :enable-email-template="composeMode === 'reply'"
              :enable-attach="true"
              :placeholder="
                composeMode === 'reply'
                  ? 'Type your reply to the customer...'
                  : 'Add an internal note (not sent to customer)...'
              "
              @attach="triggerAttachmentPicker"
            />
            <input
              ref="attachmentInput"
              type="file"
              class="hidden-file-input"
              multiple
              @change="handleAttachmentPick"
            />
            <span v-if="uploadingAttachment" class="muted">Uploading…</span>
            <div v-if="composerAttachments.length" class="attachment-list">
              <div
                v-for="attachment in composerAttachments"
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
            <button
              v-if="composeMode === 'reply'"
              class="btn"
              :disabled="saving || composerIsEmpty"
              @click="sendReply"
            >
              {{ saving ? "Sending..." : "Send Reply" }}
            </button>
            <button
              v-else
              class="btn"
              style="background: #f59e0b; border-color: #f59e0b"
              :disabled="saving || composerIsEmpty"
              @click="sendComment"
            >
              {{ saving ? "Saving..." : "Add Note" }}
            </button>
          </div>
        </section>
      </div>

      <aside class="detail-side">
        <section class="detail-section">
          <h3>Update Ticket</h3>
          <div class="detail-body stack">
            <label>
              Assigned To
              <select v-model="form.assignee">
                <option value="">Unassigned</option>
                <option
                  v-for="user in agentUsers"
                  :key="user.name"
                  :value="user.name"
                >
                  {{ user.full_name || user.name }}
                </option>
              </select>
            </label>
            <label>
              Status
              <select v-model="form.status">
                <option>On Hold</option>
                <option>Open</option>
                <option>Replied</option>
                <option>Resolved</option>
                <option>Closed</option>
              </select>
            </label>
            <label>
              Ticket Type
              <select v-model="form.ticket_type">
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
              <select v-model="form.priority">
                <option value="">Not set</option>
                <option>High</option>
                <option>Medium</option>
                <option>Low</option>
              </select>
            </label>
            <label>
              <input v-model="form.is_on_hold" type="checkbox" />
              Put On Hold
            </label>
            <label>
              Hold From
              <input
                v-model="form.hold_from"
                type="date"
                :disabled="!form.is_on_hold"
              />
            </label>
            <label>
              Hold To
              <input
                v-model="form.hold_to"
                type="date"
                :disabled="!form.is_on_hold"
              />
            </label>
            <label>
              Reason Of Hold
              <textarea
                v-model="form.hold_reason"
                rows="3"
                :disabled="!form.is_on_hold"
              ></textarea>
            </label>
            <p v-if="actionError" class="error" style="font-size: 12px">
              {{ actionError }}
            </p>
            <button class="btn" :disabled="saving" @click="saveTicket">
              {{ saving ? "Saving..." : "Update Ticket" }}
            </button>
          </div>
        </section>

        <section class="detail-section">
          <div class="detail-section-heading">
            <h3>Assignment History</h3>
            <button
              type="button"
              class="link-btn"
              title="Open HD Ticket in Desk"
              @click="openDeskTicket(ticket.name)"
            >
              Open in Desk ↗
            </button>
          </div>
          <div class="detail-body history">
            <p v-if="!assignmentHistory.length" class="muted">
              No assignment history yet.
            </p>
            <div
              v-for="item in assignmentHistory"
              :key="item.name"
              class="history-item history-item-assignment"
            >
              <strong>
                {{
                  item.assigned_by_full_name ||
                  item.assigned_by ||
                  "Unknown user"
                }}
                assigned this ticket to
                {{
                  item.allocated_to_full_name ||
                  item.allocated_to ||
                  "Unknown agent"
                }}
              </strong>
              <br />
              <small class="muted">
                {{ formatDateTime(item.assigned_at || item.creation) }}
              </small>
            </div>
          </div>
        </section>

        <section class="detail-section">
          <h3>Issue History</h3>
          <div class="detail-body history">
            <p v-if="!history.length" class="muted">No history yet.</p>
            <div v-for="item in history" :key="item.name" class="history-item">
              <strong>{{ item.action }}</strong>
              <br />
              <small class="muted">
                {{ item.owner }} · {{ formatDateTime(item.creation) }}
              </small>
            </div>
          </div>
        </section>
      </aside>
    </div>
  </section>
</template>

<script setup>
import { computed, inject, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import TinyMceEditor from "@desk/components/TinyMceEditor.vue";
import {
  AuthRedirectError,
  call,
  callWithRetry,
  formatDate,
  formatDateTime,
  getAgents,
  getTicketTypes,
  openDeskPath,
  sanitize,
  uploadAttachment,
} from "../api";

const TICKET_NOTICE_KEY = "unity_helpdesk_ticket_notice";
const props = defineProps({ ticketId: { type: String, required: true } });
const emit = defineEmits(["title"]);
const route = useRoute();
const router = useRouter();

const ticket = ref({});
const agents = ref([]);
const ticketTypes = ref([]);
const communications = ref([]);
const comments = ref([]);
const loading = ref(false);
const reloading = ref(false);
const reloadPrompt = ref(false);
const saving = ref(false);
const error = ref(""); // page-load errors only
const notice = ref("");
const actionError = ref(""); // reply / comment / save errors
const composeMode = ref("reply"); // 'reply' | 'comment'
// Separate drafts per mode so switching Reply <-> Internal Note never loses text.
// Persisted to sessionStorage (per ticket + mode) so they also survive a reload,
// and cleared only on a successful send.
const composerDrafts = reactive({ reply: "", comment: "" });
function draftKey(mode) {
  return `unity:draft:${props.ticketId || "new"}:${mode}`;
}
const composerHtml = computed({
  get: () => composerDrafts[composeMode.value] || "",
  set: (val) => {
    const value = val || "";
    composerDrafts[composeMode.value] = value;
    try {
      const k = draftKey(composeMode.value);
      if (value.trim()) sessionStorage.setItem(k, value);
      else sessionStorage.removeItem(k);
    } catch {
      /* sessionStorage unavailable — in-memory draft still works */
    }
  },
});
const composerAttachments = ref([]);
const editorRef = ref(null);
const attachmentInput = ref(null);
const uploadingAttachment = ref(false);
const additionalOpen = ref(true);
const studentDetailsOpen = ref(true);
const showAllPreviousTickets = ref(false);
const previousTicketRows = ref([]);
const repliedToSummary = ref("");
let activeTicketRequestId = 0;
const parsedDescription = ref({
  students: [],
  fees: [],
  previousTicketsHtml: "",
  remainingHtml: "",
});
const additionalFilters = reactive({
  from: "",
  to: "",
});
const form = reactive({
  assignee: "",
  status: "Open",
  priority: "",
  ticket_type: "",
  is_on_hold: false,
  hold_from: "",
  hold_to: "",
  hold_reason: "",
});

const history = computed(() => ticket.value.history || []);
const assignmentHistory = computed(() => ticket.value.assignment_history || []);
const timeline = computed(() => {
  let items;
  if (Array.isArray(ticket.value.thread) && ticket.value.thread.length) {
    items = [...ticket.value.thread];
  } else {
    const comms = communications.value.map((c) => ({
      ...c,
      _type: "comm",
    }));
    const notes = comments.value.map((c) => ({
      ...c,
      _type: "comment",
      sender: c.commented_by,
    }));
    items = [...comms, ...notes];
  }
  // Hide the auto-generated "STUDENT DETAILS" intake (already shown in the side
  // panel). Everything else — the customer's email, every agent reply, internal
  // notes — renders in chronological order.
  return items
    .filter((item) => !isStudentDetailsIntake(item))
    .sort((a, b) => new Date(a.creation) - new Date(b.creation));
});
const filteredPreviousTickets = computed(() =>
  previousTicketRows.value.filter((item) =>
    isInsideAdditionalRange(item.creation)
  )
);
const visiblePreviousTickets = computed(() =>
  showAllPreviousTickets.value
    ? filteredPreviousTickets.value
    : filteredPreviousTickets.value.slice(0, 5)
);
const agentUsers = computed(() => agents.value);
const studentContext = computed(() => ticket.value.student_context || {});
const structuredStudents = computed(() => studentContext.value.students || []);
const hasStructuredStudentContext = computed(
  () =>
    !!studentContext.value.match_type ||
    !!studentContext.value.message ||
    structuredStudents.value.length > 0
);
const shouldRenderStructuredStudentContext = computed(
  () =>
    structuredStudents.value.length > 0 ||
    (!ticket.value.custom_list_of_student && hasStructuredStudentContext.value)
);
const showLegacyStudentSection = computed(
  () =>
    !shouldRenderStructuredStudentContext.value &&
    !!(studentRows.value.length || ticket.value.custom_list_of_student)
);
const showLegacyFeeSection = computed(
  () =>
    !shouldRenderStructuredStudentContext.value &&
    !!(
      feeRows.value.length ||
      ticket.value.custom_all_fees_details_of_students ||
      ticket.value.custom_payment_schedule
    )
);
const structuredStudentColumns = computed(() =>
  structuredStudents.value.map((student, index) => ({
    key: student.student_id || `student-${index}`,
    id: student.student_id || `Student ${index + 1}`,
    name: student.student_name || student.student_id || `Student ${index + 1}`,
    role: studentRoleLabel(student),
    // The student's OWN academic year (current year for active students, else
    // their latest enrolled year for Alumni/left/Cancelled) — shown next to the
    // ref so a mixed sibling family isn't all labelled the global current year.
    academicYear:
      student.academic_year || student.enrollment?.academic_year || "",
  }))
);
const structuredStudentRows = computed(() => {
  const rows = [
    ["Class", (student) => displayClassCell(student)],
    ["Status", (student) => displayValue(student.student_status)],
    ["Confirm for Next Year", (student) => displayConfirmNextYear(student)],
    ["Payment Plan", (student) => displayPaymentPlan(student)],
  ];

  return rows.map(([field, formatter]) => ({
    field,
    values: structuredStudents.value.map((student, index) => ({
      key: student.student_id || `student-${index}`,
      html: formatter(student),
    })),
  }));
});
const showGuardianTable = computed(
  () =>
    !studentContext.value.siblings_present &&
    structuredStudents.value.length > 0
);
const guardianRows = computed(() => {
  // No-sibling case: collect distinct guardians across the (single) primary student.
  if (!showGuardianTable.value) return [];
  const seen = new Map();
  for (const student of structuredStudents.value) {
    const guardians = student.guardians || [];
    for (const g of guardians) {
      const dedupeKey =
        (g.id || "") + "|" + (g.email || "") + "|" + (g.mobile || "");
      if (seen.has(dedupeKey)) continue;
      seen.set(dedupeKey, {
        key: dedupeKey,
        name: g.name || "",
        mobile: g.mobile || "",
        alternate_mobile: g.alternate_mobile || "",
        email: g.email || "",
      });
    }
  }
  return [...seen.values()];
});
const studentRows = computed(() => parsedDescription.value.students);
const feeRows = computed(() => parsedDescription.value.fees);
const previousTicketsHtml = computed(
  () => parsedDescription.value.previousTicketsHtml
);
const studentFields = computed(() =>
  orderedFields(studentRows.value, [
    "ID",
    "Name",
    "School",
    "Class",
    "Division",
    "Status",
    "Contact",
    "Bus",
  ])
);
const studentColumns = computed(() =>
  studentRows.value.map((student, index) => ({
    key: student.__ref || student.ID || `student-${index}`,
    label: student.__ref || student.ID || `Student ${index + 1}`,
  }))
);
const studentTransposedRows = computed(() =>
  studentFields.value.map((field) => ({
    field,
    values: studentRows.value.map((student, index) => ({
      key: student.__ref || student.ID || `student-${index}`,
      html: student[field] || "-",
    })),
  }))
);
const feeFields = computed(() =>
  orderedFields(feeRows.value, [
    "Student",
    "Payment Plan",
    "Total Fee",
    "Fees Paid",
    "Fee Link",
  ])
);
const hasAdditionalDetails = computed(
  () =>
    previousTicketsHtml.value ||
    previousTicketRows.value.length ||
    ticket.value.custom_student_remark ||
    ticket.value.custom_previous_ticket_details
);
const composerIsEmpty = computed(() => !hasMeaningfulHtml(composerHtml.value));

watch(
  () => props.ticketId,
  async () => {
    emit("title", "Ticket Detail", `#${props.ticketId}`);
    comments.value = [];
    previousTicketRows.value = [];
    repliedToSummary.value = "";
    restoreDrafts();
    await Promise.all([loadTicket(), loadLookups()]);
  },
  { immediate: true }
);

function priorityClass(priority = "") {
  return priority.toLowerCase();
}

function studentContextBanner(context) {
  if (context?.match_type === "student") return "Matched via Student";
  if (context?.match_type === "guardian") return "Matched via Guardian";
  return "This email is not in our records";
}

function studentContextBannerClass(context) {
  if (context?.match_type === "student")
    return "student-context-banner--success";
  if (context?.match_type === "guardian") return "student-context-banner--info";
  return "student-context-banner--warning";
}

function studentRoleLabel(student) {
  if (student?.is_primary_match) return "";
  if (student?.is_sibling) return "Sibling";
  return "Student";
}

function displayClassCell(student) {
  const classNumber = cleanText(String(student?.class_number || ""));
  const division = cleanText(String(student?.division || ""));
  const location = cleanText(String(student?.school_location || ""));
  const school = cleanText(String(student?.school || ""));

  if (classNumber && division) {
    // "8-E-Shivane"
    return [classNumber, division, location].filter(Boolean).join("-");
  }
  if (classNumber) {
    // "Nursery-Baby Walnut Shivane" — use full school name, fall back to location
    return [classNumber, school || location].filter(Boolean).join("-");
  }
  // No class_number — use full class_program (e.g. "Nursery-Baby Walnut Shivane")
  const program = displayValue(student?.class_program);
  if (program !== "-") return program;
  return school || location || "-";
}

function displayConfirmNextYear(student) {
  // A flagged dropout takes over this row entirely — show only the red badge so
  // agents spot at-risk students instantly (the standalone Dropout row was removed).
  if (student?.possible_dropout) {
    return (
      '<span style="display:inline-block;padding:2px 8px;border-radius:4px;' +
      'background:#fee2e2;color:#991b1b;font-weight:600">⚠ Possible Dropout</span>'
    );
  }
  const value = cleanText(String(student?.confirm_for_next_year || ""));
  if (!value) return "-";
  // Green for a confirmed "Yes", red otherwise.
  const color = value.toLowerCase() === "yes" ? "#16a34a" : "#dc2626";
  return `<strong style="color: ${color}">${value}</strong>`;
}

function displayValue(value) {
  const text = cleanText(String(value ?? ""));
  return text || "-";
}

// Plan names look like "Single Installment Plan-(100)-...". Collapse them to just
// the installment count, e.g. "1 Installment" / "3 Installments".
const INSTALLMENT_WORDS = {
  single: 1,
  one: 1,
  two: 2,
  double: 2,
  three: 3,
  four: 4,
  five: 5,
  six: 6,
  seven: 7,
  eight: 8,
  nine: 9,
  ten: 10,
  eleven: 11,
  twelve: 12,
};

function installmentLabel(n) {
  return `${n} Installment${n > 1 ? "s" : ""}`;
}

function compactPaymentPlan(value) {
  const text = cleanText(String(value ?? ""));
  if (!text) return "-";
  // Plan names vary ("Single Installment Plan-(100)-...", "P2-(50-50)-...",
  // "Three Installment Plan-(34-33-33)-..."). The most reliable installment count
  // is the number of parts in the first "(...)" group: (100)=1, (50-50)=2, (34-33-33)=3.
  const paren = text.match(/\(([\d\s.,-]+)\)/);
  if (paren) {
    const parts = paren[1]
      .split(/[-,]/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (parts.length) return installmentLabel(parts.length);
  }
  // Fallback 1: "<Word> Installment Plan-..." word form.
  const word = text.match(/^([A-Za-z]+)\s+installment/i);
  if (word && INSTALLMENT_WORDS[word[1].toLowerCase()]) {
    return installmentLabel(INSTALLMENT_WORDS[word[1].toLowerCase()]);
  }
  // Fallback 2: "P2-..." code form.
  const pcode = text.match(/^P(\d+)\b/i);
  if (pcode && parseInt(pcode[1], 10) > 0) {
    return installmentLabel(parseInt(pcode[1], 10));
  }
  // Last resort: short, safe truncation.
  return text.length <= 56 ? text : `${text.slice(0, 56)}...`;
}

function displayPaymentPlan(student) {
  return compactPaymentPlan(
    student?.fees?.payment_plan || student?.enrollment?.payment_plan || ""
  );
}

function goBackToList() {
  const listView = route.query.list_view === "all" ? "all" : "my";
  router.push({
    path: `/tickets/${listView}`,
    query: { ...route.query, list_view: undefined },
  });
}

// Prev / Next ticket navigation
const ticketNav = computed(() => {
  try {
    return JSON.parse(sessionStorage.getItem("unity:ticket_nav") || "null");
  } catch {
    return null;
  }
});
const currentNavIdx = computed(() => {
  if (!ticketNav.value?.ids) return -1;
  return ticketNav.value.ids.indexOf(String(props.ticketId));
});
const prevTicketId = computed(() =>
  currentNavIdx.value > 0 ? ticketNav.value.ids[currentNavIdx.value - 1] : null
);
const nextTicketId = computed(() => {
  const nav = ticketNav.value;
  return nav &&
    currentNavIdx.value >= 0 &&
    currentNavIdx.value < nav.ids.length - 1
    ? nav.ids[currentNavIdx.value + 1]
    : null;
});

function goToPrevTicket() {
  if (prevTicketId.value)
    router.push({ path: `/tickets/${prevTicketId.value}`, query: route.query });
}
function goToNextTicket() {
  if (nextTicketId.value)
    router.push({ path: `/tickets/${nextTicketId.value}`, query: route.query });
}

function clearAdditionalFilters() {
  additionalFilters.from = "";
  additionalFilters.to = "";
}

function isInsideAdditionalRange(value) {
  return isInsideDateRange(value, additionalFilters);
}

function isInsideDateRange(value, filters) {
  if (!value) return true;
  const created = new Date(value);
  if (filters.from) {
    const from = new Date(`${filters.from}T00:00:00`);
    if (created < from) return false;
  }
  if (filters.to) {
    const to = new Date(`${filters.to}T23:59:59`);
    if (created > to) return false;
  }
  return true;
}

function orderedFields(rows, preferred) {
  const fields = new Set();
  rows.forEach((row) =>
    Object.keys(row).forEach((key) => !key.startsWith("__") && fields.add(key))
  );
  return [
    ...preferred.filter((field) => fields.has(field)),
    ...[...fields].filter((field) => !preferred.includes(field)),
  ];
}

function parseTicketDescription(html) {
  const result = {
    students: [],
    fees: [],
    previousTicketsHtml: "",
    remainingHtml: html || "",
  };
  if (
    !html ||
    typeof window === "undefined" ||
    typeof DOMParser === "undefined"
  ) {
    return result;
  }

  const container = document.createElement("div");
  container.innerHTML = html;
  const nodes = Array.from(container.childNodes);
  const consumed = new Set();

  nodes.forEach((node, index) => {
    const label = cleanText(node.textContent || "").toUpperCase();
    if (label.includes("STUDENT DETAILS")) {
      const tableIndex = findNextTableIndex(nodes, index + 1);
      if (tableIndex >= 0) {
        const ref = extractRef(nodes, index, tableIndex);
        result.students.push({
          ...parseKeyValueTable(nodes[tableIndex]),
          ...(ref ? { __ref: ref } : {}),
        });
        markConsumed(consumed, index, tableIndex);
      }
    } else if (label.includes("FEE DETAILS")) {
      const tableIndex = findNextTableIndex(nodes, index + 1);
      if (tableIndex >= 0) {
        const ref = extractRef(nodes, index, tableIndex);
        result.fees.push({
          ...(ref ? { Student: ref, __ref: ref } : {}),
          ...parseKeyValueTable(nodes[tableIndex]),
        });
        markConsumed(consumed, index, tableIndex);
      }
    } else if (label.includes("PREVIOUS TICKETS")) {
      const tableIndex = findNextTableIndex(nodes, index + 1);
      if (tableIndex >= 0) {
        result.previousTicketsHtml = nodes[tableIndex].outerHTML;
        markConsumed(consumed, index, tableIndex);
      }
    }
  });

  result.remainingHtml = nodes
    .filter((_, index) => !consumed.has(index))
    .map((node) => node.outerHTML || node.textContent || "")
    .join("")
    .trim();
  result.previousTicketsHtml = normalizeTicketLinksInHtml(
    result.previousTicketsHtml
  );
  result.remainingHtml = normalizeTicketLinksInHtml(result.remainingHtml);

  return result;
}

function triggerAttachmentPicker() {
  attachmentInput.value?.click();
}

async function handleAttachmentPick(event) {
  const files = Array.from(event.target.files || []);
  if (!files.length) return;
  uploadingAttachment.value = true;
  actionError.value = "";
  try {
    for (const file of files) {
      const uploaded = await uploadAttachment(
        file,
        "HD Ticket",
        props.ticketId
      );
      composerAttachments.value.push(uploaded);
    }
  } catch (err) {
    actionError.value = err.message;
  } finally {
    uploadingAttachment.value = false;
    if (attachmentInput.value) {
      attachmentInput.value.value = "";
    }
  }
}

function removeComposerAttachment(name) {
  composerAttachments.value = composerAttachments.value.filter(
    (attachment) => attachment.name !== name
  );
}

function composerPayloadHtml() {
  return composerHtml.value;
}

function composerCommentHtml() {
  const base = composerPayloadHtml();
  if (!composerAttachments.value.length) {
    return base;
  }
  const ul = document.createElement("ul");
  for (const att of composerAttachments.value) {
    const a = document.createElement("a");
    a.href = att.file_url || "#";
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = att.file_name || att.name || "attachment";
    const li = document.createElement("li");
    li.appendChild(a);
    ul.appendChild(li);
  }
  const p = document.createElement("p");
  const strong = document.createElement("strong");
  strong.textContent = "Attachments";
  p.appendChild(strong);
  return `${base}${p.outerHTML}${ul.outerHTML}`;
}

async function resetComposer() {
  composerAttachments.value = [];
  composerHtml.value = ""; // clears the active mode's draft + its sessionStorage
  editorRef.value?.clear?.();
}

// Load any cached drafts for this ticket (survives reload/navigation).
function restoreDrafts() {
  try {
    composerDrafts.reply = sessionStorage.getItem(draftKey("reply")) || "";
    composerDrafts.comment = sessionStorage.getItem(draftKey("comment")) || "";
  } catch {
    composerDrafts.reply = "";
    composerDrafts.comment = "";
  }
}

function findNextTableIndex(nodes, start) {
  for (let index = start; index < nodes.length; index += 1) {
    const node = nodes[index];
    if (
      node.nodeType === Node.ELEMENT_NODE &&
      node.tagName?.toLowerCase() === "table"
    ) {
      return index;
    }
  }
  return -1;
}

function extractRef(nodes, start, end) {
  const text = nodes
    .slice(start, end)
    .map((node) => node.textContent || "")
    .join(" ");
  const match = text.match(/-\s*([A-Za-z0-9/_-]+)/);
  return match?.[1] || "";
}

function parseKeyValueTable(table) {
  const row = {};
  table.querySelectorAll("tr").forEach((tr) => {
    const cells = Array.from(tr.children);
    if (cells.length < 2) return;
    const key = cleanText(cells[0].textContent || "").replace(/:$/, "");
    if (!key) return;
    row[key] = cells[1].innerHTML.trim() || "-";
  });
  return row;
}

function markConsumed(consumed, start, end) {
  for (let index = start; index <= end; index += 1) {
    consumed.add(index);
  }
}

function cleanText(value) {
  return value.replace(/\s+/g, " ").trim();
}

function normalizeHtml(value) {
  return cleanText((value || "").replace(/<[^>]*>/g, " "));
}

function hasMeaningfulHtml(value) {
  return !!normalizeHtml(value || "");
}

function normalizeTicketLinksInHtml(value) {
  if (!value || typeof document === "undefined") return value || "";
  const container = document.createElement("div");
  container.innerHTML = value;
  Array.from(container.querySelectorAll("a")).forEach((anchor) => {
    const href = anchor.getAttribute("href") || "";
    const hrefMatch = href.match(/\/app\/hd-ticket\/([^/?#]+)/i);
    const text = cleanText(anchor.textContent || "");
    const textMatch = text.match(/^\d+$/);
    const ticketId = hrefMatch?.[1] || textMatch?.[0];
    if (!ticketId) return;
    anchor.setAttribute("href", `/app/hd-ticket/${ticketId}`);
    anchor.setAttribute("target", "_top");
    anchor.setAttribute("rel", "noopener noreferrer");
  });
  return container.innerHTML;
}

// Render the full body of every (kept) thread message verbatim. The student /
// fee blocks live in the dedicated side panel; the thread shows complete mail
// bodies so no agent reply, customer email or note is ever blanked.
function threadContent(item) {
  return normalizeTicketLinksInHtml(item?.content || "");
}

// Display name of who sent the message / added the note (backend attaches
// item.user via get_user_info_for_avatar on sender/commented_by).
function threadAuthor(item) {
  const u = item?.user || {};
  return u.full_name || u.name || item?.sender || "";
}

// HTML to parse the student / fee / previous-ticket side panels from. Bulk-email
// tickets keep a clean description, so prefer the auto "Student Information" intake
// communication's body (which carries the full blocks); fall back to the ticket
// description for normal tickets (where the same content lives inline).
function studentInfoSourceHtml() {
  const comms = ticket.value?.communications || ticket.value?.thread || [];
  const intake = comms.find(
    (c) => (c.subject || "").trim().toLowerCase() === "student information"
  );
  return (intake && intake.content) || ticket.value?.description || "";
}

// The edu_quality auto-intake is created as a "Student Information" communication
// carrying the STUDENT DETAILS / fee / previous-ticket template (already shown in
// the side panel). Hide it from the thread. A customer message (Received) and any
// genuine agent reply / bulk message are always kept. Subject is the reliable
// signal (hardcoded by CustomHDTicket.fetch_ticket_details); a content/description
// fallback covers older intakes that lack the subject.
function isStudentDetailsIntake(item) {
  if (item?._type !== "comm") return false;
  if ((item.subject || "").trim().toLowerCase() === "student information") {
    return true;
  }
  if (item.sent_or_received === "Received") return false;
  const raw = item.content || "";
  if (!/STUDENT DETAILS/i.test(raw)) return false;
  const normalizedRaw = normalizeHtml(raw);
  return (
    !!normalizedRaw &&
    normalizedRaw === normalizeHtml(ticket.value?.description || "")
  );
}

function applyForm() {
  const current = ticket.value || {};
  form.assignee = current.assignee?.name || "";
  form.status = current.custom_is_on_hold
    ? "On Hold"
    : current.status || "Open";
  form.priority = current.priority || "";
  form.ticket_type = current.ticket_type || "";
  form.is_on_hold = !!Number(current.custom_is_on_hold || 0);
  form.hold_from = current.custom_hold_from || "";
  form.hold_to = current.custom_hold_to || "";
  form.hold_reason = current.custom_hold_reason || "";
}

async function loadLookups() {
  const [agentResult, typeResult] = await Promise.allSettled([
    getAgents(),
    getTicketTypes(),
  ]);
  agents.value =
    agentResult.status === "fulfilled" ? agentResult.value || [] : [];
  ticketTypes.value =
    typeResult.status === "fulfilled" ? typeResult.value || [] : [];
}

async function loadTicket() {
  const requestId = activeTicketRequestId + 1;
  activeTicketRequestId = requestId;
  loading.value = true;
  error.value = "";
  reloading.value = false;
  reloadPrompt.value = false;
  notice.value = sessionStorage.getItem(TICKET_NOTICE_KEY) || "";
  if (notice.value) {
    sessionStorage.removeItem(TICKET_NOTICE_KEY);
  }
  try {
    const detail = await callWithRetry(
      "helpdesk.api.unity_helpdesk_ext.get_ticket_detail",
      { name: props.ticketId },
      {
        timeoutMs: 20000,
        idempotent: true,
        onAttempt: () => {
          if (requestId === activeTicketRequestId) reloading.value = true;
        },
      }
    );
    if (requestId !== activeTicketRequestId) return;
    ticket.value = detail;
    communications.value = ticket.value.communications || [];
    comments.value = ticket.value.comments || [];
    // Parse the student / fee / previous-ticket blocks from the "Student
    // Information" intake comm when present (bulk-email tickets keep a clean
    // description, but that intake comm carries the full blocks). Normal tickets
    // fall back to the description (where the same content lives).
    parsedDescription.value = parseTicketDescription(studentInfoSourceHtml());
    applyForm();
    queueMicrotask(() => {
      if (requestId === activeTicketRequestId) {
        loadPreviousTicketDetails();
        loadRepliedToSummary();
        // Fire the student-context call separately so its ~10+ Education
        // app frappe.get_all queries don't sit inside the get_ticket_detail
        // response and push it over the 20 s timeout. The student panel
        // fills in when this lands; the rest of the page is already
        // visible by then.
        loadStudentContext();
      }
    });
  } catch (err) {
    if (requestId === activeTicketRequestId) {
      if (err instanceof AuthRedirectError || err.code === "AUTH_REDIRECT") {
        error.value = "Session expired — redirecting to login…";
      } else if (
        err.code === "NETWORK_ERROR" ||
        (err.status && err.status >= 500)
      ) {
        reloadPrompt.value = true;
      } else {
        error.value = err.message;
      }
    }
  } finally {
    if (requestId === activeTicketRequestId) {
      loading.value = false;
      reloading.value = false;
    }
  }
}

async function loadStudentContext() {
  // Render the page even if this hangs / errors — the rest of the ticket
  // is independent of the Education-app joins. Times out at 15 s on the
  // outside; failures fall back to the existing "unmatched" placeholder
  // copy that the panel already handles.
  if (!ticket.value?.name) return;
  try {
    const ctx = await call(
      "helpdesk.api.unity_helpdesk.get_student_context",
      { ticket_name: ticket.value.name },
      { timeoutMs: 15000, idempotent: true }
    );
    if (ctx) {
      ticket.value.student_context = ctx;
    }
  } catch (err) {
    // Non-fatal — log once for debugging, keep the rest of the page usable.
    console.warn("[unity-helpdesk] student-context load failed:", err);
  }
}

async function loadPreviousTicketDetails() {
  const fallbackRows = parsePreviousTicketRows(
    parsedDescription.value.previousTicketsHtml
  );
  const names = fallbackRows.map((row) => row.name).filter(Boolean);
  previousTicketRows.value = fallbackRows;

  const raisedBy = ticket.value?.raised_by || "";

  // Run the two server calls in parallel: existing ticket history (from the
  // names we parsed out of the description HTML) + bulk-email audit tickets
  // where the current user was a recipient.
  const summariesPromise = names.length
    ? call("helpdesk.api.unity_helpdesk.get_accessible_ticket_summaries", {
        names,
      }).catch(() => null)
    : Promise.resolve([]);
  const bulkPromise = raisedBy
    ? call("helpdesk.api.unity_helpdesk.get_bulk_emails_received_by", {
        email: raisedBy,
      }).catch(() => [])
    : Promise.resolve([]);

  const [summaries, bulkRows] = await Promise.all([
    summariesPromise,
    bulkPromise,
  ]);

  if (summaries === null) {
    // Summaries call failed — keep the HTML-parsed fallback rows but still
    // try to merge bulk-email matches.
    const dedup = new Map();
    fallbackRows.forEach((r) => dedup.set(String(r.name), r));
    (bulkRows || []).forEach((r) => {
      if (String(r.name) === String(ticket.value?.name)) return;
      dedup.set(String(r.name), r);
    });
    previousTicketRows.value = Array.from(dedup.values());
    return;
  }

  const byName = Object.fromEntries(
    (summaries || []).map((row) => [String(row.name), row])
  );
  const merged = fallbackRows
    .filter((row) => byName[String(row.name)])
    .map((row) => ({ ...row, ...(byName[String(row.name)] || {}) }));

  // Append bulk-email audit tickets we haven't already seen, skipping the
  // current ticket itself if it happens to be one.
  const seen = new Set(merged.map((r) => String(r.name)));
  (bulkRows || []).forEach((r) => {
    const key = String(r.name);
    if (seen.has(key)) return;
    if (key === String(ticket.value?.name)) return;
    merged.push({ ...r, custom_is_bulk_email: 1 });
    seen.add(key);
  });

  // Order newest-first so the most recent bulk email lands near the top.
  merged.sort((a, b) => new Date(b.creation || 0) - new Date(a.creation || 0));

  previousTicketRows.value = merged;
}

async function loadRepliedToSummary() {
  const sourceName = ticket.value?.custom_replied_to_ticket;
  if (!sourceName) {
    repliedToSummary.value = "";
    return;
  }
  try {
    const rows = await call(
      "helpdesk.api.unity_helpdesk.get_accessible_ticket_summaries",
      { names: [sourceName] }
    );
    const source = (rows || [])[0];
    if (!source) {
      repliedToSummary.value = "";
      return;
    }
    const parts = [];
    if (source.subject) parts.push(source.subject);
    if (source.ticket_type) parts.push(source.ticket_type);
    repliedToSummary.value = parts.join(" · ");
  } catch {
    repliedToSummary.value = "";
  }
}

function parsePreviousTicketRows(html) {
  if (!html) return [];
  const container = document.createElement("div");
  container.innerHTML = normalizeTicketLinksInHtml(html);
  return Array.from(container.querySelectorAll("tr"))
    .map((tr) => {
      const cells = Array.from(tr.children);
      if (cells.length < 2) return null;
      const link = cells[0].querySelector("a");
      const name = cleanText(link?.textContent || cells[0].textContent || "");
      if (!name || name.toLowerCase().includes("ticket")) return null;
      return {
        name,
        subject: cleanText(cells[1].textContent || ""),
        creation: "",
        status: "",
      };
    })
    .filter(Boolean);
}

function openDeskTicket(name) {
  openDeskPath(`/app/hd-ticket/${name}`);
}

// Navigate to another ticket inside the SPA — used by the Previous Tickets
// table and the "replied to" banner. Preserves the list-view query param so
// Back / Next still walks the list the user came from.
function openSpaTicket(name) {
  if (!name) return;
  router.push({ path: `/tickets/${name}`, query: route.query });
}

function isOutgoingTicket(row) {
  return !!(row?.custom_is_bulk_email || row?.custom_via_unity_portal);
}

function previousTicketRowClass(row) {
  if (isOutgoingTicket(row)) return "previous-ticket-row--outgoing";
  if (row?.custom_replied_to_ticket) return "previous-ticket-row--reply";
  return "";
}

function ticketTypeColor(name) {
  // Look the colour up in the in-memory ticket-types list (already loaded
  // by App.vue / loadLookups). Fallback to a muted grey when the type has
  // no custom_color set or the lookup hasn't populated yet.
  if (!name) return "#94a3b8";
  const match = (ticketTypes.value || []).find((t) => t && t.name === name);
  return (match && match.custom_color) || "#94a3b8";
}

async function saveTicket() {
  saving.value = true;
  actionError.value = "";
  try {
    const isOnHold = form.status === "On Hold" ? 1 : form.is_on_hold ? 1 : 0;
    await call("helpdesk.api.unity_helpdesk_ext.update_ticket", {
      name: props.ticketId,
      assignee: form.assignee,
      status: form.status,
      priority: form.priority,
      ticket_type: form.ticket_type,
      is_on_hold: isOnHold,
      hold_from: form.hold_from,
      hold_to: form.hold_to,
      hold_reason: form.hold_reason,
    });
    await loadTicket();
  } catch (err) {
    actionError.value = err.message;
  } finally {
    saving.value = false;
  }
}

async function markClosed() {
  form.status = "Closed";
  form.is_on_hold = false;
  await saveTicket();
}

async function sendReply() {
  saving.value = true;
  actionError.value = "";
  try {
    const res = await call("helpdesk.api.unity_helpdesk_ext.reply", {
      name: props.ticketId,
      message: composerPayloadHtml(),
      attachments: composerAttachments.value.map(
        (attachment) => attachment.name
      ),
    });
    await resetComposer();
    // Optimistically append the new communication so the thread updates
    // immediately. The spinner clears as soon as the reply call returns —
    // no blocking full ticket reload.
    const comm = res && res.communication;
    if (comm) {
      // Shape the item to match get_ticket_thread_components() output so both
      // the thread renderer (ticket.value.thread) and the fallback timeline
      // (communications.value) can show it.
      const threadItem = {
        ...comm,
        _type: "comm",
        attachments: comm.attachments || [],
      };
      if (ticket.value && Array.isArray(ticket.value.thread)) {
        ticket.value.thread = [...ticket.value.thread, threadItem];
      }
      communications.value = [...communications.value, threadItem];
      // Fire-and-forget background refresh for eventual consistency
      // (attachments, server-side mutations) without blocking the UI.
      loadTicket();
    } else {
      // Fallback for older backends that don't return the communication.
      await loadTicket();
    }
  } catch (err) {
    actionError.value = err.message;
  } finally {
    saving.value = false;
  }
}

async function sendComment() {
  saving.value = true;
  actionError.value = "";
  try {
    await call("helpdesk.api.unity_helpdesk_ext.add_comment", {
      name: props.ticketId,
      content: composerCommentHtml(),
    });
    await resetComposer();
    await loadTicket();
  } catch (err) {
    actionError.value = err.message;
  } finally {
    saving.value = false;
  }
}

watch(
  () => form.status,
  (value) => {
    if (value === "On Hold") {
      form.is_on_hold = true;
    } else if (form.is_on_hold) {
      form.is_on_hold = false;
    }
  }
);

watch(
  () => form.is_on_hold,
  (value) => {
    if (value && form.status !== "On Hold") {
      form.status = "On Hold";
    }
    if (!value && form.status === "On Hold") {
      form.status = "Open";
    }
  }
);

watch(
  () => composeMode.value,
  () => {
    // Switching tabs keeps each mode's own draft (see composerDrafts) — only
    // clear the transient error.
    actionError.value = "";
  }
);
</script>
