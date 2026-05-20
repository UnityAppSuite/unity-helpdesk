<template>
  <section class="page dashboard-page">
    <div class="dashboard-filterbar">
      <div class="dashboard-presets">
        <button
          v-for="preset in presets"
          :key="preset.value"
          class="filter-chip"
          :class="{ active: range === preset.value }"
          type="button"
          @click="setRange(preset.value)"
        >
          {{ preset.label }}
        </button>
      </div>

      <div class="dashboard-actions">
        <label v-if="canViewAgentDashboard" class="date-field agent-filter">
          <span>Agent</span>
          <select v-model="selectedAgent" @change="load">
            <option value="">All agents</option>
            <option
              v-for="agent in agents"
              :key="agent.name"
              :value="agent.name"
            >
              {{ agent.full_name || agent.name }}
            </option>
          </select>
        </label>
        <template v-if="range === 'custom'">
          <label class="date-field">
            <span>From</span>
            <input v-model="customFrom" type="date" @change="load" />
          </label>
          <label class="date-field">
            <span>To</span>
            <input v-model="customTo" type="date" @change="load" />
          </label>
        </template>
        <button class="btn secondary" type="button" @click="load">
          Refresh
        </button>
      </div>
    </div>

    <div v-if="reloading" class="reloading-indicator">
      <span class="reload-spinner" aria-hidden="true"></span>
      <span>Reloading…</span>
    </div>
    <div v-if="reloadPrompt" class="reload-prompt">
      <span>Couldn't load the dashboard.</span>
      <button type="button" class="btn secondary" @click="load()">Retry</button>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="loading" class="empty">Loading dashboard...</p>

    <template v-else>
      <div class="dashboard-overview">
        <div class="overview-copy">
          <strong>{{ summary.range_label || "Dashboard" }}</strong>
          <span>
            {{ formattedWindow }}
          </span>
        </div>
        <div class="overview-meta">
          <span class="overview-pill">Bucket: {{ bucketLabel }}</span>
          <span class="overview-pill"
            >Types tracked: {{ ticketTypeBreakdown.length }}</span
          >
        </div>
      </div>

      <div class="dashboard-metrics dashboard-metrics-six">
        <div class="metric metric-strong">
          <small>Total</small>
          <b>{{ cards.total || 0 }}</b>
          <span>Tickets in selected window</span>
        </div>
        <div class="metric metric-accent">
          <small>Created</small>
          <b>{{ cards.created || 0 }}</b>
          <span>New tickets added</span>
        </div>
        <div class="metric metric-warning">
          <small>Open / Pending</small>
          <b>{{ cards.pending || 0 }}</b>
          <span>Active workload</span>
        </div>
        <div class="metric metric-hold">
          <small>On Hold</small>
          <b>{{ cards.on_hold || 0 }}</b>
          <span>Waiting with blockers</span>
        </div>
        <div class="metric metric-success">
          <small>Resolved</small>
          <b>{{ cards.resolved || 0 }}</b>
          <span>Marked resolved</span>
        </div>
        <div class="metric metric-muted">
          <small>Closed</small>
          <b>{{ cards.closed || 0 }}</b>
          <span>Fully closed tickets</span>
        </div>
      </div>

      <div class="dashboard-grid">
        <section class="panel analytics-panel">
          <div class="table-header">
            <strong>Ticket Type Distribution</strong>
            <span>{{ summary.range_label || "Selected range" }}</span>
          </div>
          <div class="donut-layout">
            <div class="donut-wrap">
              <svg class="donut-chart" viewBox="0 0 120 120" aria-hidden="true">
                <circle class="donut-bg" cx="60" cy="60" r="42"></circle>
                <circle
                  v-for="segment in donutSegments"
                  :key="segment.name"
                  class="donut-segment"
                  cx="60"
                  cy="60"
                  r="42"
                  :stroke="segment.color"
                  :stroke-dasharray="segment.dasharray"
                  :stroke-dashoffset="segment.dashoffset"
                ></circle>
              </svg>
              <div class="donut-center">
                <strong>{{ cards.total || 0 }}</strong>
                <span>Total</span>
              </div>
            </div>

            <div class="donut-legend">
              <div
                v-for="segment in donutLegend"
                :key="segment.name"
                class="legend-row"
              >
                <span
                  class="legend-dot"
                  :style="{ backgroundColor: segment.color }"
                ></span>
                <div class="legend-copy">
                  <strong>{{ segment.name }}</strong>
                  <small
                    >{{ segment.value }} tickets · {{ segment.percent }}%</small
                  >
                </div>
              </div>
              <p v-if="!donutLegend.length" class="empty inline-empty">
                No ticket type data for this range.
              </p>
            </div>
          </div>
        </section>

        <section class="panel analytics-panel">
          <div class="table-header">
            <strong>Status Trend</strong>
            <span>{{ bucketDescription }}</span>
          </div>
          <div class="stacked-chart">
            <div v-if="!statusTrend.length" class="empty">
              No status trend data available.
            </div>
            <template v-else>
              <div class="stacked-bars">
                <div
                  v-for="row in statusTrend"
                  :key="row.key"
                  class="stacked-bar-card"
                >
                  <div class="stacked-bar-track">
                    <div
                      v-for="segment in statusSegments(row)"
                      :key="`${row.key}-${segment.key}`"
                      class="stacked-bar-segment"
                      :style="{
                        height: `${segment.height}%`,
                        background: segment.color,
                      }"
                      :title="`${segment.label}: ${segment.value}`"
                    ></div>
                  </div>
                  <strong>{{ row.total }}</strong>
                  <small>{{ row.label }}</small>
                </div>
              </div>
              <div class="stacked-legend">
                <span
                  v-for="item in statusPalette"
                  :key="item.key"
                  class="legend-inline"
                >
                  <i :style="{ backgroundColor: item.color }"></i
                  >{{ item.label }}
                </span>
              </div>
            </template>
          </div>
        </section>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, inject, onMounted, ref } from "vue";
import {
  AuthRedirectError,
  call,
  callWithRetry,
  formatDate,
  getAgents,
} from "../api";

const emit = defineEmits(["title"]);
const unitySession = inject("unitySession", { capabilities: {} });
const range = ref("week");
const customFrom = ref("");
const customTo = ref("");
const selectedAgent = ref("");
const agents = ref([]);
const loading = ref(false);
const reloading = ref(false);
const reloadPrompt = ref(false);
const error = ref("");
const summary = ref({
  cards: {},
  ticket_type_breakdown: [],
  status_trend: [],
  range_label: "",
  from_date: "",
  to_date: "",
  bucket: "day",
});

const presets = [
  { value: "today", label: "Today" },
  { value: "week", label: "This week" },
  { value: "month", label: "This month" },
  { value: "quarter", label: "This quarter" },
  { value: "year", label: "This year" },
  { value: "custom", label: "Custom date" },
];

const statusPalette = [
  { key: "open", label: "Open", color: "#4f46e5" },
  { key: "replied", label: "Replied", color: "#7c3aed" },
  { key: "on_hold", label: "On Hold", color: "#f59e0b" },
  { key: "resolved", label: "Resolved", color: "#10b981" },
  { key: "closed", label: "Closed", color: "#64748b" },
];
const donutPalette = [
  "#4f46e5",
  "#0ea5e9",
  "#f59e0b",
  "#10b981",
  "#ec4899",
  "#64748b",
  "#8b5cf6",
];

const cards = computed(() => summary.value.cards || {});
const ticketTypeBreakdown = computed(
  () => summary.value.ticket_type_breakdown || []
);
const statusTrend = computed(() =>
  (summary.value.status_trend || []).map((row) => ({
    ...row,
    total:
      (row.open || 0) +
      (row.replied || 0) +
      (row.on_hold || 0) +
      (row.resolved || 0) +
      (row.closed || 0),
  }))
);
const bucketLabel = computed(
  () =>
    ({ day: "Daily", week: "Weekly", month: "Monthly" }[summary.value.bucket] ||
    "Daily")
);
const bucketDescription = computed(
  () =>
    `${bucketLabel.value} view for ${
      summary.value.range_label || "selected range"
    }`
);
const formattedWindow = computed(() => {
  if (!summary.value.from_date || !summary.value.to_date)
    return "Live ticket analytics";
  return `${formatDate(summary.value.from_date)} to ${formatDate(
    summary.value.to_date
  )}`;
});
const canViewAgentDashboard = computed(
  () => !!unitySession.capabilities?.can_view_agent_dashboard
);

const donutLegend = computed(() => {
  const total = cards.value.total || 0;
  return ticketTypeBreakdown.value.map((row, index) => ({
    ...row,
    color: donutPalette[index % donutPalette.length],
    percent: total ? Math.round((row.value / total) * 100) : 0,
  }));
});

const donutSegments = computed(() => {
  const total = ticketTypeBreakdown.value.reduce(
    (sum, row) => sum + (row.value || 0),
    0
  );
  const circumference = 2 * Math.PI * 42;
  let offset = 0;
  return ticketTypeBreakdown.value.map((row, index) => {
    const value = row.value || 0;
    const fraction = total ? value / total : 0;
    const dash = fraction * circumference;
    const segment = {
      name: row.name,
      color: donutPalette[index % donutPalette.length],
      dasharray: `${dash} ${circumference - dash}`,
      dashoffset: `${-offset}`,
    };
    offset += dash;
    return segment;
  });
});

onMounted(() => {
  emit("title", "Dashboard", "Advanced ticket analytics and trends");
  loadAgents();
  load();
});

async function loadAgents() {
  if (!canViewAgentDashboard.value) return;
  try {
    agents.value = await getAgents();
  } catch {
    agents.value = [];
  }
}

function setRange(nextRange) {
  range.value = nextRange;
  if (nextRange !== "custom") {
    customFrom.value = "";
    customTo.value = "";
  }
  load();
}

async function load() {
  loading.value = true;
  error.value = "";
  reloading.value = false;
  reloadPrompt.value = false;
  try {
    summary.value = await callWithRetry(
      "helpdesk.api.unity_helpdesk.get_dashboard_summary",
      {
        range: range.value,
        from_date: customFrom.value || undefined,
        to_date: customTo.value || undefined,
        agent: selectedAgent.value || undefined,
      },
      {
        idempotent: true,
        onAttempt: () => {
          reloading.value = true;
        },
      }
    );
    if (range.value === "custom") {
      customFrom.value = summary.value.from_date || customFrom.value;
      customTo.value = summary.value.to_date || customTo.value;
    }
  } catch (err) {
    if (err instanceof AuthRedirectError || err.code === "AUTH_REDIRECT") {
      error.value = "Session expired — redirecting to login…";
      return;
    }
    if (err.code === "NETWORK_ERROR" || (err.status && err.status >= 500)) {
      reloadPrompt.value = true;
    } else {
      error.value = err.message;
    }
  } finally {
    loading.value = false;
    reloading.value = false;
  }
}

function statusSegments(row) {
  return statusPalette
    .map((item) => ({
      ...item,
      value: row[item.key] || 0,
      height: row.total ? ((row[item.key] || 0) / row.total) * 100 : 0,
    }))
    .filter((item) => item.value > 0);
}
</script>
