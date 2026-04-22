<template>
  <section class="page">
    <div class="toolbar">
      <button class="btn secondary" @click="$router.back()">
        Back to Tickets
      </button>
      <strong v-if="ticket.name">#{{ ticket.name }}</strong>
      <span
        v-if="ticket.status_indicator"
        class="badge"
        :class="ticket.status_indicator.color"
      >
        {{ ticket.status_indicator.label }}
      </span>
      <button class="btn" :disabled="saving" @click="markResolved">
        Mark Resolved
      </button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="loading" class="empty">Loading ticket...</p>

    <div v-else class="detail-grid">
      <div class="detail-main">
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
          v-if="studentRows.length || ticket.custom_list_of_student"
          class="detail-section"
        >
          <h3>Student Details</h3>
          <div v-if="studentRows.length" class="detail-body compact-body">
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
                      v-html="value.html || '-'"
                    ></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div
            v-else
            class="detail-body safe-html compact-html"
            v-html="ticket.custom_list_of_student"
          ></div>
        </section>

        <section
          v-if="
            feeRows.length ||
            ticket.custom_all_fees_details_of_students ||
            ticket.custom_payment_schedule
          "
          class="detail-section"
        >
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
                      v-html="fee[field] || '-'"
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
              v-html="ticket.custom_all_fees_details_of_students"
            ></div>
            <div
              v-if="ticket.custom_payment_schedule"
              class="detail-body safe-html compact-html"
              v-html="ticket.custom_payment_schedule"
            ></div>
          </template>
        </section>

        <section v-if="hasAdditionalDetails" class="detail-section">
          <button
            class="section-toggle"
            type="button"
            @click="additionalOpen = !additionalOpen"
          >
            <span>Previous Ticket Details</span>
            <small>
              {{ filteredPreviousTickets.length }} of
              {{ previousTicketRows.length }} previous tickets
            </small>
            <strong>{{ additionalOpen ? "Hide" : "Show" }}</strong>
          </button>
          <div class="detail-body stack">
            <div class="thread-filters">
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
            <p v-if="!additionalOpen" class="muted">
              Previous tickets and extra details are collapsed to keep this page
              short.
            </p>
            <div v-else class="stack">
              <div
                v-if="ticket.custom_student_remark"
                class="safe-html compact-html"
                v-html="ticket.custom_student_remark"
              ></div>
              <div v-if="previousTicketRows.length" class="scroll-x">
                <table class="compact-info-table previous-ticket-table">
                  <thead>
                    <tr>
                      <th>Ticket No</th>
                      <th>Subject</th>
                      <th>Created On</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in filteredPreviousTickets" :key="row.name">
                      <td>
                        <a :href="`/app/hd-ticket/${row.name}`">{{
                          row.name
                        }}</a>
                      </td>
                      <td>{{ row.subject || "-" }}</td>
                      <td>{{ formatDate(row.creation) }}</td>
                      <td>{{ row.status || "-" }}</td>
                    </tr>
                  </tbody>
                </table>
                <p
                  v-if="!filteredPreviousTickets.length"
                  class="muted inline-empty"
                >
                  No previous tickets found in this date range.
                </p>
              </div>
              <div
                v-else-if="previousTicketsHtml"
                class="safe-html compact-html"
                v-html="previousTicketsHtml"
              ></div>
              <div
                v-if="ticket.custom_previous_ticket_details"
                class="safe-html compact-html"
                v-html="ticket.custom_previous_ticket_details"
              ></div>
            </div>
          </div>
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
                <span class="chat-msg-sender">{{ item.sender }}</span>
                <span class="chat-msg-time">{{
                  formatDateTime(item.creation)
                }}</span>
              </div>
              <div
                class="chat-msg-body safe-html"
                v-html="threadContent(item)"
              ></div>
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
            <textarea
              v-model="replyText"
              rows="6"
              :placeholder="
                composeMode === 'reply'
                  ? 'Type your reply to the customer...'
                  : 'Add an internal note (not sent to customer)...'
              "
            ></textarea>
            <button
              v-if="composeMode === 'reply'"
              class="btn"
              :disabled="saving || !replyText.trim()"
              @click="sendReply"
            >
              {{ saving ? "Sending..." : "Send Reply" }}
            </button>
            <button
              v-else
              class="btn"
              style="background: #f59e0b; border-color: #f59e0b"
              :disabled="saving || !replyText.trim()"
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
import { computed, reactive, ref, watch } from "vue";
import {
  call,
  formatDate,
  formatDateTime,
  getAgents,
  getTicketTypes,
} from "../api";

const props = defineProps({ ticketId: { type: String, required: true } });
const emit = defineEmits(["title"]);

const ticket = ref({});
const agents = ref([]);
const ticketTypes = ref([]);
const communications = ref([]);
const comments = ref([]);
const loading = ref(false);
const saving = ref(false);
const error = ref(""); // page-load errors only
const actionError = ref(""); // reply / comment / save errors
const replyText = ref("");
const composeMode = ref("reply"); // 'reply' | 'comment'
const additionalOpen = ref(false);
const previousTicketRows = ref([]);
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
const timeline = computed(() => {
  const comms = communications.value.map((c) => ({
    ...c,
    _type: "comm",
  }));
  const notes = comments.value.map((c) => ({
    ...c,
    _type: "comment",
    sender: c.commented_by,
  }));
  return [...comms, ...notes].sort(
    (a, b) => new Date(a.creation) - new Date(b.creation)
  );
});
const filteredPreviousTickets = computed(() =>
  previousTicketRows.value.filter((item) =>
    isInsideAdditionalRange(item.creation)
  )
);
const agentUsers = computed(() => agents.value);
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

watch(
  () => props.ticketId,
  async () => {
    emit("title", "Ticket Detail", `#${props.ticketId}`);
    await Promise.all([loadTicket(), loadLookups(), loadComments()]);
  },
  { immediate: true }
);

function priorityClass(priority = "") {
  return priority.toLowerCase();
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

  return result;
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

function threadContent(item) {
  if (item?._type !== "comm") {
    return item?.content || "";
  }

  const parsedContent = parseTicketDescription(item.content || "");
  const hasStructuredBlocks =
    parsedContent.students.length ||
    parsedContent.fees.length ||
    parsedContent.previousTicketsHtml;
  const trimmedThreadHtml = (parsedContent.remainingHtml || "").trim();

  if (hasStructuredBlocks) {
    return trimmedThreadHtml;
  }

  const matchesDescription =
    normalizeHtml(item.content) &&
    normalizeHtml(item.content) === normalizeHtml(ticket.value.description);
  if (matchesDescription) {
    return (parsedDescription.value.remainingHtml || "").trim();
  }

  return item.content || "";
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

async function loadComments() {
  try {
    const rows = await call("frappe.client.get_list", {
      doctype: "HD Ticket Comment",
      fields: ["name", "content", "commented_by", "creation", "is_pinned"],
      filters: [["reference_ticket", "=", props.ticketId]],
      order_by: "creation asc",
      page_length: 500,
    });
    comments.value = rows || [];
  } catch {
    comments.value = [];
  }
}

async function loadTicket() {
  loading.value = true;
  error.value = "";
  try {
    ticket.value = await call("helpdesk.api.unity_ext.get_ticket_detail", {
      name: props.ticketId,
    });
    communications.value = ticket.value.communications || [];
    parsedDescription.value = parseTicketDescription(
      ticket.value.description || ""
    );
    applyForm();
    await loadPreviousTicketDetails();
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

async function loadPreviousTicketDetails() {
  const fallbackRows = parsePreviousTicketRows(
    parsedDescription.value.previousTicketsHtml
  );
  const names = fallbackRows.map((row) => row.name).filter(Boolean);
  previousTicketRows.value = fallbackRows;
  if (!names.length) return;

  try {
    const rows = await call("frappe.client.get_list", {
      doctype: "HD Ticket",
      fields: ["name", "subject", "creation", "status"],
      filters: [["HD Ticket", "name", "in", names]],
      limit_page_length: names.length,
    });
    const byName = Object.fromEntries(
      (rows || []).map((row) => [String(row.name), row])
    );
    previousTicketRows.value = fallbackRows.map((row) => ({
      ...row,
      ...(byName[String(row.name)] || {}),
    }));
  } catch {
    previousTicketRows.value = fallbackRows;
  }
}

function parsePreviousTicketRows(html) {
  if (!html) return [];
  const container = document.createElement("div");
  container.innerHTML = html;
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

async function saveTicket() {
  saving.value = true;
  actionError.value = "";
  try {
    const isOnHold = form.status === "On Hold" ? 1 : form.is_on_hold ? 1 : 0;
    await call("helpdesk.api.unity_ext.update_ticket", {
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

async function markResolved() {
  form.status = "Resolved";
  form.is_on_hold = false;
  await saveTicket();
}

// Create a Communication record directly from the frontend (fallback when
// unity_ext.reply fails — e.g. module cache stale or no email account).
async function _createCommunicationDirect(message) {
  const sender = await call("frappe.auth.get_logged_user");
  await call("frappe.client.insert", {
    doc: {
      doctype: "Communication",
      communication_type: "Communication",
      communication_medium: "",
      sent_or_received: "Sent",
      email_status: "Open",
      subject: `Re: ${ticket.value.subject || ""} (#${props.ticketId})`,
      sender: sender || "",
      recipients: ticket.value.raised_by || "",
      content: message,
      status: "Linked",
      reference_doctype: "HD Ticket",
      reference_name: props.ticketId,
    },
  });
}

async function sendReply() {
  saving.value = true;
  actionError.value = "";
  try {
    try {
      await call("helpdesk.api.unity_ext.reply", {
        name: props.ticketId,
        message: replyText.value,
      });
    } catch {
      // unity_ext.reply unavailable (module cache stale) or no email account —
      // fall back to creating the Communication record directly.
      await _createCommunicationDirect(replyText.value);
    }
    replyText.value = "";
    await loadTicket();
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
    // Insert HD Ticket Comment directly — no backend function needed.
    const user = await call("frappe.auth.get_logged_user");
    await call("frappe.client.insert", {
      doc: {
        doctype: "HD Ticket Comment",
        commented_by: user || "",
        content: replyText.value,
        is_pinned: 0,
        reference_ticket: props.ticketId,
      },
    });
    replyText.value = "";
    await loadComments();
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
</script>
