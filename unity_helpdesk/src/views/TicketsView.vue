<template>
  <section class="page">
    <div class="toolbar">
      <select v-model="filters.status" @change="applyFiltersAndReload">
        <option value="">Status: All</option>
        <option>Open</option>
        <option>Replied</option>
        <option>On Hold</option>
        <option>Resolved</option>
        <option>Closed</option>
      </select>
      <select v-model="filters.priority" @change="applyFiltersAndReload">
        <option value="">Priority: All</option>
        <option>High</option>
        <option>Medium</option>
        <option>Low</option>
      </select>
      <select v-model="filters.ticket_type" @change="applyFiltersAndReload">
        <option value="">Ticket Type: All</option>
        <option v-for="type in ticketTypes" :key="type.name" :value="type.name">
          {{ type.name }}
        </option>
      </select>
      <!-- In My Tickets view the backend already filters to the current user — hide Assigned filter -->
      <select
        v-if="props.view === 'all'"
        v-model="filters.assigned_to"
        @change="applyFiltersAndReload"
      >
        <option value="">Assigned: All</option>
        <option value="Unassigned">Unassigned</option>
        <option v-for="agent in agents" :key="agent.name" :value="agent.name">
          {{ agent.full_name || agent.name }}
        </option>
      </select>
      <span v-else class="badge blue">Assigned to me</span>
      <input
        v-model="filters.created_from"
        type="date"
        @change="applyFiltersAndReload"
      />
      <div class="search-wrapper">
        <span class="search-icon">🔍</span>
        <input
          v-model="draftSearch"
          class="search"
          type="text"
          placeholder="Ticket ID, student name, reference no., guardian email or message keyword…"
          @keyup.enter="submitSearch"
        />
        <span
          v-if="loading && appliedSearch"
          class="search-loading"
          title="Searching…"
          >⏳</span
        >
        <button
          v-if="draftSearch"
          class="search-clear"
          type="button"
          title="Clear search"
          @click="clearSearch"
        >
          ✕
        </button>
      </div>
      <button
        class="btn secondary toolbar-search"
        type="button"
        @click="submitSearch"
      >
        Search
      </button>
      <button
        class="btn secondary toolbar-refresh"
        type="button"
        @click="refreshList"
      >
        Refresh
      </button>
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
        <span>Resolved</span>
      </div>
      <div class="metric">
        <b>{{ cards.closed || 0 }}</b>
        <span>Closed</span>
      </div>
    </div>

    <div class="table-shell">
      <div class="table-header">
        <strong>{{ title }}</strong>
        <span>{{ result.total_count || 0 }} tickets</span>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-else-if="loading && !tickets.length" class="empty">Searching…</p>
      <p v-else-if="!tickets.length && appliedSearch" class="empty">
        No tickets found for <strong>"{{ appliedSearch }}"</strong> — try a
        shorter or different term.
      </p>
      <p v-else-if="!tickets.length" class="empty">{{ emptyMessage }}</p>
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
                <button
                  class="link-btn"
                  type="button"
                  @click.stop="openDeskTicket(ticket.name)"
                >
                  #{{ ticket.name }}
                </button>
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
import {
  call,
  formatDate,
  getAgents,
  getTicketTypes,
  openDeskPath,
} from "../api";

const props = defineProps({ view: { type: String, default: "my" } });
const emit = defineEmits(["title"]);
const route = useRoute();
const router = useRouter();

const draftSearch = ref("");
const appliedSearch = ref("");
const loading = ref(false);
const loadingMore = ref(false);
const error = ref("");
const emptyMessage = ref("No tickets found.");
const agents = ref([]);
const ticketTypes = ref([]);
const rowSaving = reactive({});
const editState = reactive({});
const result = reactive({
  data: [],
  total_count: 0,
  cards: {},
  start: 0,
  page_length: 20,
});
const filters = reactive({
  status: "",
  priority: "",
  ticket_type: "",
  assigned_to: "",
  created_from: "",
});
let activeController = null;
let activeRequestId = 0;

const title = computed(() =>
  props.view === "my" ? "My Tickets" : "All Tickets"
);
const tickets = computed(() => result.data || []);
const canLoadMore = computed(
  () => tickets.value.length < (result.total_count || 0)
);
const cards = computed(() => result.cards || {});

watch(
  () => [props.view, route.fullPath],
  async () => {
    emit("title", title.value, "Search, edit, and open tickets");
    applyRouteState();
    await reload();
  },
  { immediate: true }
);

onMounted(async () => {
  applyRouteState();
  await loadLookups();
});

function applyRouteState() {
  draftSearch.value = String(route.query.search || "");
  appliedSearch.value = String(route.query.search || "");
  filters.status = String(route.query.status || "");
  filters.priority = String(route.query.priority || "");
  filters.ticket_type = String(route.query.ticket_type || "");
  filters.assigned_to = String(route.query.assigned_to || "");
  filters.created_from = String(route.query.created_from || "");
}

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

async function reload() {
  result.start = 0;
  await load({ append: false });
}

function routeQueryFromState() {
  return {
    ...route.query,
    status: filters.status || undefined,
    priority: filters.priority || undefined,
    ticket_type: filters.ticket_type || undefined,
    assigned_to: filters.assigned_to || undefined,
    created_from: filters.created_from || undefined,
    search: appliedSearch.value.trim() || undefined,
  };
}

async function applyFiltersAndReload() {
  await router.replace({ query: routeQueryFromState() });
  await reload();
}

function submitSearch() {
  appliedSearch.value = draftSearch.value.trim();
  router.replace({ query: routeQueryFromState() });
  reload();
}

function clearSearch() {
  draftSearch.value = "";
  appliedSearch.value = "";
  router.replace({ query: routeQueryFromState() });
  reload();
}

function refreshList() {
  reload();
}

function resetResults() {
  result.data = [];
  result.total_count = 0;
  result.cards = {};
  result.start = 0;
}

async function load({ append = false } = {}) {
  const requestId = activeRequestId + 1;
  activeRequestId = requestId;
  activeController?.abort();
  activeController = new AbortController();
  if (append) {
    loadingMore.value = true;
  } else {
    loading.value = true;
  }
  error.value = "";
  emptyMessage.value = "No tickets found.";
  try {
    const data = await call(
      "helpdesk.api.unity_helpdesk.get_tickets",
      {
        view: props.view,
        filters: cleanFilters(),
        search: appliedSearch.value,
        page_length: result.page_length,
        start: append ? tickets.value.length : 0,
      },
      {
        signal: activeController.signal,
        timeoutMs: appliedSearch.value.trim() ? 20000 : 30000,
      }
    );
    if (requestId !== activeRequestId) return;
    if (append) {
      result.data = [...result.data, ...(data.data || [])];
      result.total_count = data.total_count || 0;
      result.cards = data.cards || {};
      result.start = data.start || result.start;
      result.page_length = data.page_length || result.page_length;
    } else {
      Object.assign(result, data);
    }
    syncEditState(data.data || []);
  } catch (err) {
    if (requestId !== activeRequestId || err.code === "REQUEST_ABORTED") {
      return;
    }
    if (err.code === "REQUEST_TIMEOUT") {
      resetResults();
      emptyMessage.value = appliedSearch.value.trim()
        ? `Search timed out. Try a more specific query — e.g. a ticket ID or exact name.`
        : "Loading timed out. Please refresh and try again.";
      return;
    }
    error.value = err.message;
  } finally {
    if (requestId === activeRequestId) {
      if (append) {
        loadingMore.value = false;
      } else {
        loading.value = false;
      }
      activeController = null;
    }
  }
}

async function loadMore() {
  if (loading.value || loadingMore.value) return;
  await load({ append: true });
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
    const updated = await call(
      "helpdesk.api.unity_helpdesk_ext.update_ticket",
      payload
    );
    const index = result.data.findIndex((row) => row.name === ticket.name);
    if (index >= 0) {
      result.data[index] = updated;
      syncEditState([updated]);
    }
    await reload();
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
  router.push({
    path: `/tickets/${name}`,
    query: {
      ...routeQueryFromState(),
      list_view: props.view,
    },
  });
}

function openDeskTicket(name) {
  openDeskPath(`/app/hd-ticket/${name}`);
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
