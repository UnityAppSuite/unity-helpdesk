<template>
  <section class="page">
    <div class="toolbar">
      <select v-model="filters.status" @change="reload">
        <option value="">Status: All</option>
        <option>Open</option>
        <option>Replied</option>
        <option>On Hold</option>
        <option>Resolved</option>
        <option>Closed</option>
      </select>
      <select v-model="filters.priority" @change="reload">
        <option value="">Priority: All</option>
        <option>High</option>
        <option>Medium</option>
        <option>Low</option>
      </select>
      <!-- In My Tickets view the backend already filters to the current user — hide Assigned filter -->
      <select
        v-if="props.view === 'all'"
        v-model="filters.assigned_to"
        @change="reload"
      >
        <option value="">Assigned: All</option>
        <option value="Unassigned">Unassigned</option>
        <option v-for="agent in agents" :key="agent.name" :value="agent.name">
          {{ agent.full_name || agent.name }}
        </option>
      </select>
      <span v-else class="badge blue">Assigned to me</span>
      <input v-model="filters.created_from" type="date" @change="reload" />
      <input
        v-model="search"
        class="search"
        type="search"
        placeholder="Search ticket ID, subject, email, student or fee details"
        @keyup.enter="reload"
      />
      <button class="btn secondary" @click="reload">Search</button>
    </div>

    <div class="metrics">
      <div class="metric">
        <b>{{ result.total_count || 0 }}</b>
        <span>Total Tickets</span>
      </div>
      <div class="metric">
        <b>{{ cards.pending || 0 }}</b>
        <span>Pending</span>
      </div>
      <div class="metric">
        <b>{{ cards.on_hold || 0 }}</b>
        <span>On Hold</span>
      </div>
      <div class="metric">
        <b>{{ cards.resolved || 0 }}</b>
        <span>Resolved This Week</span>
      </div>
      <div class="metric">
        <b>{{ cards.closed || 0 }}</b>
        <span>Closed This Week</span>
      </div>
    </div>

    <div class="table-shell">
      <div class="table-header">
        <strong>{{ title }}</strong>
        <span>{{ result.total_count || 0 }} tickets</span>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-else-if="loading" class="empty">Loading tickets...</p>
      <p v-else-if="!tickets.length" class="empty">No tickets found.</p>
      <div v-else class="scroll-x">
        <table class="ticket-table">
          <thead>
            <tr>
              <th>Ticket ID</th>
              <th>Subject</th>
              <th>Ticket Type</th>
              <th>Priority</th>
              <th>Status</th>
              <th>Assigned To</th>
              <th>Created On</th>
              <th>Issues On Hold</th>
              <th>Reason Of Hold</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="ticket in tickets"
              :key="ticket.name"
              @click="openTicket(ticket.name)"
            >
              <td>
                <strong>#{{ ticket.name }}</strong>
              </td>
              <td>
                <div class="subject">{{ ticket.subject || "No subject" }}</div>
                <small class="muted">{{ ticket.raised_by }}</small>
              </td>
              <td class="cell-edit" @click.stop>
                <select
                  v-model="editState[ticket.name].ticket_type"
                  :class="[
                    'select-chip',
                    ticketTypeClass(editState[ticket.name].ticket_type),
                  ]"
                  :disabled="isSaving(ticket.name)"
                  @change="
                    quickUpdate(
                      ticket,
                      'ticket_type',
                      editState[ticket.name].ticket_type
                    )
                  "
                >
                  <option value="">Not set</option>
                  <option
                    v-for="type in ticketTypes"
                    :key="type.name"
                    :value="type.name"
                  >
                    {{ type.name }}
                  </option>
                </select>
              </td>
              <td class="cell-edit" @click.stop>
                <select
                  v-model="editState[ticket.name].priority"
                  :class="[
                    'select-chip',
                    priorityClass(editState[ticket.name].priority),
                  ]"
                  :disabled="isSaving(ticket.name)"
                  @change="
                    quickUpdate(
                      ticket,
                      'priority',
                      editState[ticket.name].priority
                    )
                  "
                >
                  <option value="">Not set</option>
                  <option>High</option>
                  <option>Medium</option>
                  <option>Low</option>
                </select>
              </td>
              <td class="cell-edit" @click.stop>
                <select
                  v-model="editState[ticket.name].status"
                  :class="[
                    'select-chip',
                    statusClass(ticket, editState[ticket.name].status),
                  ]"
                  :disabled="isSaving(ticket.name)"
                  @change="
                    quickUpdate(ticket, 'status', editState[ticket.name].status)
                  "
                >
                  <option>On Hold</option>
                  <option>Open</option>
                  <option>Replied</option>
                  <option>Resolved</option>
                  <option>Closed</option>
                </select>
              </td>
              <td class="cell-edit" @click.stop>
                <select
                  v-model="editState[ticket.name].assignee"
                  :class="[
                    'select-chip',
                    assignmentClass(editState[ticket.name].assignee),
                  ]"
                  :disabled="isSaving(ticket.name)"
                  @change="
                    quickUpdate(
                      ticket,
                      'assignee',
                      editState[ticket.name].assignee
                    )
                  "
                >
                  <option value="">Unassigned</option>
                  <option
                    v-for="agent in agents"
                    :key="agent.name"
                    :value="agent.name"
                  >
                    {{ agent.full_name || agent.name }}
                  </option>
                </select>
              </td>
              <td>{{ formatDate(ticket.creation) }}</td>
              <td>
                <span v-if="ticket.custom_is_on_hold">
                  {{ formatHoldWindow(ticket) }}
                </span>
                <span v-else class="muted">-</span>
              </td>
              <td class="cell-edit" @click.stop>
                <input
                  v-model="editState[ticket.name].hold_reason"
                  class="table-input"
                  type="text"
                  :disabled="isSaving(ticket.name)"
                  placeholder="Add hold reason"
                  @blur="saveHoldReason(ticket)"
                  @keyup.enter="saveHoldReason(ticket)"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="table-header">
        <span
          >Showing {{ tickets.length }} of {{ result.total_count || 0 }}</span
        >
        <button v-if="canLoadMore" class="btn secondary" @click="loadMore">
          Load more
        </button>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { call, formatDate, getAgents, getTicketTypes } from "../api";

const props = defineProps({ view: { type: String, default: "my" } });
const emit = defineEmits(["title"]);
const route = useRoute();
const router = useRouter();

const search = ref("");
const loading = ref(false);
const error = ref("");
const agents = ref([]);
const ticketTypes = ref([]);
const summary = ref({ cards: {} });
const rowSaving = reactive({});
const editState = reactive({});
const result = reactive({
  data: [],
  total_count: 0,
  start: 0,
  page_length: 20,
});
const filters = reactive({
  status: "",
  priority: "",
  assigned_to: "",
  created_from: "",
});

const title = computed(() =>
  props.view === "my" ? "My Tickets" : "All Tickets"
);
const tickets = computed(() => result.data || []);
const canLoadMore = computed(
  () => tickets.value.length < (result.total_count || 0)
);
const cards = computed(() => summary.value.cards || {});

watch(
  () => [props.view, route.fullPath],
  async () => {
    emit("title", title.value, "Search, edit, and open tickets");
    await reload();
  },
  { immediate: true }
);

onMounted(async () => {
  loadSummary();
  await loadLookups();
});

function syncEditState(rows) {
  rows.forEach((ticket) => {
    editState[ticket.name] = {
      ticket_type: ticket.ticket_type || "",
      priority: ticket.priority || "",
      status: ticket.custom_is_on_hold ? "On Hold" : ticket.status || "Open",
      assignee: ticket.assignee?.name || "",
      hold_reason: ticket.custom_hold_reason || "",
    };
  });
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

function isSaving(name) {
  return !!rowSaving[name];
}

function cleanFilters() {
  return Object.fromEntries(
    Object.entries(filters).filter(([, value]) => value)
  );
}

async function loadSummary() {
  try {
    summary.value = await call("helpdesk.api.unity.get_dashboard_summary", {
      range: "week",
    });
  } catch {
    summary.value = { cards: {} };
  }
}

async function reload() {
  result.start = 0;
  result.data = [];
  await load();
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const data = await call("helpdesk.api.unity.get_tickets", {
      view: props.view,
      filters: cleanFilters(),
      search: search.value,
      page_length: result.page_length,
      start: 0,
    });
    Object.assign(result, data);
    syncEditState(data.data || []);
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

async function loadMore() {
  const data = await call("helpdesk.api.unity.get_tickets", {
    view: props.view,
    filters: cleanFilters(),
    search: search.value,
    page_length: result.page_length,
    start: tickets.value.length,
  });
  result.data = [...result.data, ...data.data];
  result.total_count = data.total_count;
  syncEditState(data.data || []);
}

async function quickUpdate(ticket, field, value) {
  rowSaving[ticket.name] = true;
  error.value = "";
  try {
    const payload = {
      name: ticket.name,
      [field]: value,
    };
    if (field === "status") {
      payload.is_on_hold = value === "On Hold" ? 1 : 0;
      if (value === "On Hold") {
        payload.hold_from = ticket.custom_hold_from || todayString();
        payload.hold_reason = editState[ticket.name].hold_reason || "";
      }
    }
    const updated = await call("helpdesk.api.unity_ext.update_ticket", payload);
    const index = result.data.findIndex((row) => row.name === ticket.name);
    if (index >= 0) {
      result.data[index] = updated;
      syncEditState([updated]);
    }
  } catch (err) {
    error.value = err.message;
    await load();
  } finally {
    rowSaving[ticket.name] = false;
  }
}

async function saveHoldReason(ticket) {
  const value = editState[ticket.name]?.hold_reason || "";
  if ((ticket.custom_hold_reason || "") === value) {
    return;
  }
  await quickUpdate(ticket, "hold_reason", value);
}

function todayString() {
  return new Date().toISOString().slice(0, 10);
}

function formatHoldWindow(ticket) {
  if (!ticket.custom_hold_from && !ticket.custom_hold_to) {
    return "On hold";
  }
  if (ticket.custom_hold_from && ticket.custom_hold_to) {
    return `${formatDate(ticket.custom_hold_from)} - ${formatDate(
      ticket.custom_hold_to
    )}`;
  }
  return formatDate(ticket.custom_hold_from || ticket.custom_hold_to);
}

function openTicket(name) {
  router.push(`/tickets/${name}`);
}

function assignmentClass(assignee) {
  return assignee ? "blue" : "pink";
}

function statusClass(ticket, selectedStatus) {
  if (selectedStatus === "On Hold") return "yellow";
  if (selectedStatus === "Resolved") return "green";
  if (selectedStatus === "Closed") return "grey";
  return editState[ticket.name]?.assignee ? "blue" : "pink";
}

function priorityClass(priority) {
  const value = (priority || "").toLowerCase();
  if (value === "high") return "pink";
  if (value === "medium") return "yellow";
  if (value === "low") return "green";
  return "grey";
}

function ticketTypeClass(ticketType) {
  const value = (ticketType || "").toLowerCase();
  if (!value) return "grey";
  if (["app", "tech"].includes(value)) return "blue";
  if (["calling", "result"].includes(value)) return "pink";
  if (["walmiki"].includes(value)) return "yellow";
  return "green";
}
</script>
