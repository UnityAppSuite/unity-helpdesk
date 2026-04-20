<template>
  <section class="page">
    <div class="toolbar">
      <select v-model="range" @change="load">
        <option value="day">Today</option>
        <option value="week">This week</option>
      </select>
      <button class="btn secondary" @click="load">Refresh</button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="loading" class="empty">Loading dashboard...</p>
    <template v-else>
      <div class="metrics">
        <div class="metric">
          <b>{{ cards.created }}</b
          ><span>Created</span>
        </div>
        <div class="metric">
          <b>{{ cards.pending }}</b
          ><span>Pending</span>
        </div>
        <div class="metric">
          <b>{{ cards.on_hold }}</b
          ><span>On Hold</span>
        </div>
        <div class="metric">
          <b>{{ cards.resolved }}</b
          ><span>Resolved</span>
        </div>
        <div class="metric">
          <b>{{ cards.closed }}</b
          ><span>Closed</span>
        </div>
      </div>

      <div class="panel">
        <div class="table-header">
          <strong>Created, Resolved and Closed</strong>
          <span>{{ range === "week" ? "Last 7 days" : "Today" }}</span>
        </div>
        <div class="chart">
          <div v-for="row in series" :key="row.date" class="bar">
            <span :style="{ height: `${barHeight(row.created)}px` }"></span>
            <b>{{ row.created }}</b>
            <small>{{ shortDate(row.date) }}</small>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { call } from "../api";

const emit = defineEmits(["title"]);
const range = ref("week");
const loading = ref(false);
const error = ref("");
const summary = ref({ cards: {}, series: [] });

const cards = computed(() => summary.value.cards || {});
const series = computed(() => summary.value.series || []);
const maxCreated = computed(() =>
  Math.max(...series.value.map((row) => row.created || 0), 1)
);

onMounted(() => {
  emit("title", "Dashboard", "Daily and weekly ticket summary");
  load();
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    summary.value = await call("helpdesk.api.unity.get_dashboard_summary", {
      range: range.value,
    });
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

function barHeight(value) {
  return Math.max(12, Math.round((value / maxCreated.value) * 160));
}

function shortDate(value) {
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
  }).format(new Date(value));
}
</script>
