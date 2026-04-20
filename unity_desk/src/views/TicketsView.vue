<template>
  <section class="page">
    <div class="toolbar">
      <select v-model="filters.status" @change="load">
        <option value="">Status: All</option>
        <option>Open</option>
        <option>Replied</option>
        <option>On Hold</option>
        <option>Resolved</option>
        <option>Closed</option>
      </select>
      <select v-model="filters.priority" @change="load">
        <option value="">Priority: All</option>
        <option>High</option>
        <option>Medium</option>
        <option>Low</option>
      </select>
      <select v-model="filters.assigned_to" @change="load">
        <option value="">Assigned: All</option>
        <option value="Unassigned">Unassigned</option>
        <option v-for="user in users" :key="user.name" :value="user.name">
          {{ user.full_name || user.name }}
        </option>
      </select>
      <input v-model="filters.created_from" type="date" @change="load" />
      <input
        v-model="search"
        class="search"
        type="search"
        placeholder="Search ticket ID, subject, email, student or fee details"
        @keyup.enter="load"
      />
      <button class="btn secondary" @click="load">Search</button>
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
        <table>
          <thead>
            <tr>
              <th>Ticket ID</th>
              <th>Subject</th>
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
              <td>
                <span class="priority" :class="priorityClass(ticket.priority)">
                  {{ ticket.priority || "Not set" }}
                </span>
                <small v-if="ticket.priority_target" class="muted">
                  {{ ticket.priority_target }}</small
                >
              </td>
              <td>
                <span class="badge" :class="ticket.status_indicator.color">
                  {{ ticket.status_indicator.label }}
                </span>
              </td>
              <td>
                <span v-if="ticket.assignee">
                  <span class="avatar">{{
                    initials(ticket.assignee.full_name || ticket.assignee.name)
                  }}</span>
                  {{ ticket.assignee.full_name || ticket.assignee.name }}
                </span>
                <span v-else class="muted">Unassigned</span>
              </td>
              <td>{{ formatDate(ticket.creation) }}</td>
              <td>
                <span v-if="ticket.custom_is_on_hold">
                  {{ formatDate(ticket.custom_hold_from) }} -
                  {{ formatDate(ticket.custom_hold_to) }}
                </span>
                <span v-else class="muted">-</span>
              </td>
              <td>{{ ticket.custom_hold_reason || "-" }}</td>
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
import { useRouter } from "vue-router";
import { call, formatDate, initials } from "../api";

const props = defineProps({ view: { type: String, default: "my" } });
const emit = defineEmits(["title"]);
const router = useRouter();

const search = ref("");
const loading = ref(false);
const error = ref("");
const users = ref([]);
const summary = ref({ cards: {} });
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
  () => props.view,
  () => {
    result.start = 0;
    result.data = [];
    load();
    emit("title", title.value, "Search, filter, and open tickets");
  }
);

onMounted(async () => {
  emit("title", title.value, "Search, filter, and open tickets");
  load();
  loadSummary();
  try {
    users.value = await call("helpdesk.api.unity.get_users");
  } catch {
    users.value = [];
  }
});

function priorityClass(priority = "") {
  return priority.toLowerCase();
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
}

function openTicket(name) {
  router.push(`/tickets/${name}`);
}
</script>
