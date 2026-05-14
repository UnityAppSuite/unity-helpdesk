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
          ref="searchInput"
          v-model="draftSearch"
          class="search"
          type="text"
          placeholder="Ticket ID, student, ref no., email, subject, mail body…"
          aria-label="Search tickets (press Ctrl+K to focus)"
          @keyup.enter="submitSearch"
          @focus="searchFocused = true"
          @blur="onSearchBlur"
        />
        <kbd
          v-if="!draftSearch && !searchFocused"
          class="search-shortcut"
          aria-hidden="true"
          >{{ shortcutLabel }}</kbd
        >
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
        <ul
          v-if="searchFocused && !draftSearch.trim() && recentSearches.length"
          class="recent-searches"
          @mousedown.prevent
        >
          <li class="recent-searches__heading">Recent searches</li>
          <li
            v-for="(item, idx) in recentSearches"
            :key="`${item}-${idx}`"
            class="recent-searches__item"
            @click="useRecentSearch(item)"
          >
            <span class="recent-searches__term">{{ item }}</span>
            <button
              type="button"
              class="recent-searches__remove"
              title="Remove from recent"
              @click.stop="removeRecentSearch(item)"
            >
              ✕
            </button>
          </li>
        </ul>
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
      <button
        class="btn secondary toolbar-columns"
        type="button"
        title="Customize columns"
        @click="openColumnPanel"
      >
        Columns
      </button>
    </div>

    <div
      v-if="showColumnPanel"
      class="modal-backdrop"
      @click.self="closeColumnPanel"
    >
      <section class="modal-card column-panel">
        <div class="modal-header">
          <div>
            <strong>Customize columns</strong>
            <span>Show, hide, reorder and resize ticket columns.</span>
          </div>
          <button class="btn secondary" @click="closeColumnPanel">Close</button>
        </div>
        <div class="modal-body stack">
          <ol class="column-list">
            <li
              v-for="(col, index) in draftColumns"
              :key="col.key"
              class="column-list-item"
              draggable="true"
              :class="{ dragging: dragIndex === index }"
              @dragstart="onDragStart(index)"
              @dragover="onDragOver($event, index)"
              @dragend="onDragEnd"
            >
              <span class="drag-handle" title="Drag to reorder">⠿</span>
              <label class="column-list-toggle">
                <input
                  type="checkbox"
                  :checked="col.visible"
                  :disabled="col.fixed"
                  @change="toggleDraftColumn(index)"
                />
                <span>{{ col.label }}</span>
                <small v-if="col.fixed" class="muted">(always shown)</small>
              </label>
              <input
                type="number"
                class="column-list-width"
                min="60"
                max="600"
                step="10"
                :value="col.width"
                title="Width (px)"
                @input="col.width = clampColumnWidth($event.target.value)"
              />
            </li>
          </ol>
        </div>
        <div class="modal-footer">
          <button
            type="button"
            class="btn secondary"
            @click="resetDraftColumns"
          >
            Reset to defaults
          </button>
          <button
            class="btn"
            :disabled="savingColumns"
            @click="saveColumnPreferences"
          >
            {{ savingColumns ? "Saving…" : "Save" }}
          </button>
        </div>
      </section>
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
      <div v-if="reloading" class="reloading-indicator">
        <span class="reload-spinner" aria-hidden="true"></span>
        <span>Reloading…</span>
      </div>
      <div v-if="reloadPrompt" class="reload-prompt">
        <span>Couldn't load tickets.</span>
        <button type="button" class="btn secondary" @click="load()">
          Retry
        </button>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-else-if="loading && !tickets.length" class="empty">Searching…</p>
      <p v-else-if="!tickets.length && appliedSearch" class="empty">
        No tickets found for <strong>{{ activeFilterSummary }}</strong> — try a
        shorter or different term.
      </p>
      <p v-else-if="!tickets.length" class="empty">{{ emptyMessage }}</p>
      <div v-else class="scroll-x">
        <table class="ticket-table">
          <thead>
            <tr>
              <th
                v-for="(col, colIdx) in visibleColumns"
                :key="col.key"
                :style="{ width: col.width + 'px', minWidth: col.width + 'px' }"
                :class="{
                  'col-dragging': colDragIdx === colIdx,
                  'col-draggable': !col.fixed,
                }"
                :draggable="!col.fixed"
                @dragstart="onColDragStart($event, colIdx)"
                @dragover="onColDragOver($event, colIdx)"
                @dragend="onColDragEnd"
                @drop.prevent
              >
                <span v-if="!col.fixed" class="col-drag-handle">⠿</span>
                {{ col.label }}
                <button
                  v-if="!col.fixed"
                  class="col-remove-btn"
                  title="Remove column"
                  type="button"
                  @click.stop="removeColumn(col.key)"
                >
                  ×
                </button>
                <span
                  v-if="!col.fixed"
                  class="col-resize-grabber"
                  title="Drag to resize"
                  @mousedown="startColumnResize($event, col)"
                ></span>
              </th>
              <!-- Add Column -->
              <th class="col-add-th">
                <div class="col-add-wrap">
                  <button
                    class="col-add-btn"
                    type="button"
                    title="Add column"
                    @click.stop="toggleAddColumnMenu"
                  >
                    +
                  </button>
                  <div v-if="showAddCol" class="col-add-dropdown">
                    <button
                      v-for="c in hiddenColumns"
                      :key="c.key"
                      class="col-add-item"
                      type="button"
                      @click.stop="addColumn(c.key)"
                    >
                      {{ c.label }}
                    </button>
                    <span v-if="!hiddenColumns.length" class="col-add-empty"
                      >All columns shown</span
                    >
                  </div>
                </div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="ticket in tickets"
              :key="ticket.name"
              :class="{
                'portal-ticket':
                  ticket.custom_via_unity_portal &&
                  !ticket.custom_is_bulk_email,
                'bulk-email-ticket': ticket.custom_is_bulk_email,
              }"
              @click="openTicket(ticket.name)"
            >
              <td
                v-for="col in visibleColumns"
                :key="col.key"
                :class="{ 'cell-edit': isEditableColumn(col.key) }"
                :style="{ minWidth: col.width + 'px' }"
                @click="onCellClick($event, col.key)"
              >
                <template v-if="col.key === 'name'">
                  <button
                    class="link-btn"
                    type="button"
                    @click.stop="openTicket(ticket.name)"
                  >
                    #{{ ticket.name }}
                  </button>
                </template>
                <template v-else-if="col.key === 'subject'">
                  <div class="subject">
                    {{ ticket.subject || "No subject" }}
                  </div>
                  <small class="muted">
                    <a :href="`mailto:${ticket.raised_by}`" @click.stop>{{
                      ticket.raised_by
                    }}</a>
                  </small>
                  <small
                    v-if="ticket.custom_search_student_names"
                    class="student-names"
                  >
                    {{ ticket.custom_search_student_names }}
                  </small>
                </template>
                <template v-else-if="col.key === 'ticket_type'">
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
                    <!-- Ensure current value is always an option even while ticketTypes loads -->
                    <option
                      v-if="
                        editState[ticket.name].ticket_type &&
                        !ticketTypes.find(
                          (t) => t.name === editState[ticket.name].ticket_type
                        )
                      "
                      :value="editState[ticket.name].ticket_type"
                    >
                      {{ editState[ticket.name].ticket_type }}
                    </option>
                    <option
                      v-for="type in ticketTypes"
                      :key="type.name"
                      :value="type.name"
                    >
                      {{ type.name }}
                    </option>
                  </select>
                </template>
                <template v-else-if="col.key === 'priority'">
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
                </template>
                <template v-else-if="col.key === 'status'">
                  <select
                    v-model="editState[ticket.name].status"
                    :class="[
                      'select-chip',
                      statusClass(ticket, editState[ticket.name].status),
                    ]"
                    :disabled="isSaving(ticket.name)"
                    @change="
                      quickUpdate(
                        ticket,
                        'status',
                        editState[ticket.name].status
                      )
                    "
                  >
                    <option>On Hold</option>
                    <option>Open</option>
                    <option>Replied</option>
                    <option>Resolved</option>
                    <option>Closed</option>
                  </select>
                </template>
                <template v-else-if="col.key === '_assign'">
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
                </template>
                <template v-else-if="col.key === 'creation'">
                  {{ formatDate(ticket.creation) }}
                </template>
                <template v-else-if="col.key === 'custom_is_on_hold'">
                  <span v-if="ticket.custom_is_on_hold">
                    {{ formatHoldWindow(ticket) }}
                  </span>
                  <span v-else class="muted">-</span>
                </template>
                <template v-else-if="col.key === 'custom_hold_reason'">
                  <input
                    v-model="editState[ticket.name].hold_reason"
                    class="table-input"
                    type="text"
                    :disabled="isSaving(ticket.name)"
                    placeholder="Add hold reason"
                    @blur="saveHoldReason(ticket)"
                    @keyup.enter="saveHoldReason(ticket)"
                  />
                </template>
                <template v-else>
                  <span>{{ formatCellValue(ticket, col.key) }}</span>
                </template>
              </td>
              <td class="col-add-td"></td>
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
import {
  computed,
  inject,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  call,
  callWithRetry,
  formatDate,
  formatDateTime,
  getAgents,
  getTicketTypes,
} from "../api";

const props = defineProps({ view: { type: String, default: "my" } });
const emit = defineEmits(["title"]);
const route = useRoute();
const router = useRouter();
const unitySession = inject("unitySession", null);
const refreshUnitySession = inject("refreshUnitySession", null);

const draftSearch = ref("");
const appliedSearch = ref("");
const loading = ref(false);
const loadingMore = ref(false);
const reloading = ref(false);
const reloadPrompt = ref(false);
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

// --- Column customization ---
const showColumnPanel = ref(false);
const draftColumns = ref([]); // popover working copy: [{ key, visible, width }]
const savingColumns = ref(false);

const availableColumns = computed(() => unitySession?.available_columns || []);
const availableColumnMap = computed(() => {
  const map = {};
  for (const col of availableColumns.value) map[col.key] = col;
  return map;
});
const visibleColumns = computed(() => {
  const prefs =
    (unitySession?.settings?.column_preferences || []).filter(
      (p) => availableColumnMap.value[p.key]
    ) || [];
  return prefs.map((p) => ({
    ...availableColumnMap.value[p.key],
    width: p.width,
  }));
});

function buildDraftColumns() {
  const prefs = unitySession?.settings?.column_preferences || [];
  const selectedSet = new Set(prefs.map((p) => p.key));
  // Selected first (preserves user order), unselected appended at the bottom.
  const draft = prefs
    .map((p) => {
      const def = availableColumnMap.value[p.key];
      if (!def) return null;
      return {
        key: p.key,
        label: def.label,
        fixed: def.fixed,
        width: p.width || def.width || 140,
        visible: true,
      };
    })
    .filter(Boolean);
  for (const col of availableColumns.value) {
    if (selectedSet.has(col.key)) continue;
    draft.push({
      key: col.key,
      label: col.label,
      fixed: col.fixed,
      width: col.width || 140,
      visible: false,
    });
  }
  return draft;
}

function openColumnPanel() {
  draftColumns.value = buildDraftColumns();
  showColumnPanel.value = true;
}

function closeColumnPanel() {
  showColumnPanel.value = false;
}

const dragIndex = ref(null);

function onDragStart(index) {
  dragIndex.value = index;
}
function onDragOver(e, index) {
  e.preventDefault();
  if (dragIndex.value === null || dragIndex.value === index) return;
  const arr = draftColumns.value.slice();
  const [item] = arr.splice(dragIndex.value, 1);
  arr.splice(index, 0, item);
  draftColumns.value = arr;
  dragIndex.value = index;
}
function onDragEnd() {
  dragIndex.value = null;
}

function toggleDraftColumn(index) {
  const col = draftColumns.value[index];
  if (col.fixed) return;
  draftColumns.value = draftColumns.value.map((c, i) =>
    i === index ? { ...c, visible: !c.visible } : c
  );
}

function resetDraftColumns() {
  draftColumns.value = (availableColumns.value || []).map((col) => ({
    key: col.key,
    label: col.label,
    fixed: col.fixed,
    width: col.width || 140,
    visible: !!col.default,
  }));
}

async function saveColumnPreferences() {
  const payload = draftColumns.value
    .filter((c) => c.visible)
    .map((c) => ({ key: c.key, width: clampColumnWidth(c.width) }));
  savingColumns.value = true;
  try {
    await call("helpdesk.api.unity_helpdesk.update_column_preferences", {
      column_preferences: JSON.stringify(payload),
    });
    if (refreshUnitySession) await refreshUnitySession();
    showColumnPanel.value = false;
    await load();
  } catch (err) {
    error.value = err.message;
  } finally {
    savingColumns.value = false;
  }
}

// Shared helper: persist a raw prefs array and refresh
async function persistColumnPrefs(prefs) {
  try {
    await call("helpdesk.api.unity_helpdesk.update_column_preferences", {
      column_preferences: JSON.stringify(prefs),
    });
    if (refreshUnitySession) await refreshUnitySession();
    await load();
  } catch (err) {
    error.value = err.message;
  }
}

// Inline column management (header drag / remove / add)
const colDragIdx = ref(null);
const showAddCol = ref(false);

const hiddenColumns = computed(() => {
  const visible = new Set(
    (unitySession?.settings?.column_preferences || []).map((p) => p.key)
  );
  return (availableColumns.value || []).filter((c) => !visible.has(c.key));
});

function toggleAddColumnMenu() {
  showAddCol.value = !showAddCol.value;
}

function onColDragStart(e, idx) {
  colDragIdx.value = idx;
  e.dataTransfer.effectAllowed = "move";
}

function onColDragOver(e, idx) {
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  if (colDragIdx.value === null || colDragIdx.value === idx) return;
  const prefs = (unitySession?.settings?.column_preferences || []).slice();
  const [moved] = prefs.splice(colDragIdx.value, 1);
  prefs.splice(idx, 0, moved);
  if (unitySession) unitySession.settings.column_preferences = prefs;
  colDragIdx.value = idx;
}

async function onColDragEnd() {
  colDragIdx.value = null;
  await persistColumnPrefs(unitySession?.settings?.column_preferences || []);
}

async function removeColumn(key) {
  const prefs = (unitySession?.settings?.column_preferences || []).filter(
    (p) => p.key !== key
  );
  await persistColumnPrefs(prefs);
}

async function addColumn(key) {
  const def = availableColumnMap.value[key];
  if (!def) return;
  const prefs = [
    ...(unitySession?.settings?.column_preferences || []),
    { key, width: def.width || 140 },
  ];
  showAddCol.value = false;
  await persistColumnPrefs(prefs);
}

// Close add-column dropdown on outside click
function onDocClick() {
  showAddCol.value = false;
}
onMounted(() => document.addEventListener("click", onDocClick));
onBeforeUnmount(() => document.removeEventListener("click", onDocClick));

function clampColumnWidth(width) {
  const n = Number.parseInt(width, 10);
  if (Number.isNaN(n)) return 140;
  return Math.min(600, Math.max(60, n));
}

// Column resize (drag right edge of <th>)
const resizingKey = ref(null);
let resizeStartX = 0;
let resizeStartWidth = 0;

function startColumnResize(event, col) {
  if (!col || col.fixed === undefined) return;
  resizingKey.value = col.key;
  resizeStartX = event.clientX;
  resizeStartWidth = col.width || 140;
  document.body.classList.add("col-resizing");
  window.addEventListener("mousemove", handleColumnResize);
  window.addEventListener("mouseup", endColumnResize, { once: true });
  event.preventDefault();
  event.stopPropagation();
}

function handleColumnResize(event) {
  if (!resizingKey.value) return;
  const delta = event.clientX - resizeStartX;
  const next = clampColumnWidth(resizeStartWidth + delta);
  const prefs = (unitySession?.settings?.column_preferences || []).map((p) =>
    p.key === resizingKey.value ? { ...p, width: next } : p
  );
  if (unitySession) unitySession.settings.column_preferences = prefs;
}

function endColumnResize() {
  window.removeEventListener("mousemove", handleColumnResize);
  document.body.classList.remove("col-resizing");
  const key = resizingKey.value;
  resizingKey.value = null;
  if (!key) return;
  // Persist new width
  const payload = (unitySession?.settings?.column_preferences || []).map(
    (p) => ({
      key: p.key,
      width: p.width,
    })
  );
  call("helpdesk.api.unity_helpdesk.update_column_preferences", {
    column_preferences: JSON.stringify(payload),
  }).catch(() => {
    /* width is best-effort; ignore transient failures */
  });
}

// --- Search UX helpers ---
const RECENT_SEARCH_KEY = "unity-helpdesk:recent-searches";
const RECENT_SEARCH_LIMIT = 8;
const isMac =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad/.test(navigator.platform);
const shortcutLabel = isMac ? "⌘K" : "Ctrl+K";

const searchInput = ref(null);
const searchFocused = ref(false);
const recentSearches = ref([]);

function loadRecentSearches() {
  try {
    const raw = localStorage.getItem(RECENT_SEARCH_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    recentSearches.value = Array.isArray(parsed)
      ? parsed
          .filter((s) => typeof s === "string" && s.trim())
          .slice(0, RECENT_SEARCH_LIMIT)
      : [];
  } catch {
    recentSearches.value = [];
  }
}

function persistRecentSearches() {
  try {
    localStorage.setItem(
      RECENT_SEARCH_KEY,
      JSON.stringify(recentSearches.value)
    );
  } catch {
    /* storage unavailable — ignore */
  }
}

function rememberSearch(term) {
  const value = (term || "").trim();
  if (!value) return;
  const next = [value, ...recentSearches.value.filter((s) => s !== value)];
  recentSearches.value = next.slice(0, RECENT_SEARCH_LIMIT);
  persistRecentSearches();
}

function removeRecentSearch(term) {
  recentSearches.value = recentSearches.value.filter((s) => s !== term);
  persistRecentSearches();
}

function useRecentSearch(term) {
  draftSearch.value = term;
  searchFocused.value = false;
  submitSearch();
}

function onSearchBlur() {
  // Delay so click on a recent-search item registers before the dropdown closes.
  setTimeout(() => {
    searchFocused.value = false;
  }, 120);
}

function focusSearchInput() {
  const el = searchInput.value;
  if (el && typeof el.focus === "function") {
    el.focus();
    el.select?.();
  }
}

function onGlobalKeydown(event) {
  // Ctrl/Cmd+K → focus search; Esc with input focused → blur it.
  const isShortcut =
    (event.ctrlKey || event.metaKey) &&
    !event.shiftKey &&
    !event.altKey &&
    (event.key === "k" || event.key === "K");
  if (isShortcut) {
    event.preventDefault();
    focusSearchInput();
    return;
  }
  if (event.key === "Escape" && document.activeElement === searchInput.value) {
    searchInput.value?.blur();
  }
}

const title = computed(() =>
  props.view === "my" ? "My Tickets" : "All Tickets"
);
const tickets = computed(() => result.data || []);
const canLoadMore = computed(
  () => tickets.value.length < (result.total_count || 0)
);
const cards = computed(() => result.cards || {});
const activeFilterSummary = computed(() => {
  return `search: "${appliedSearch.value.trim()}"`;
});

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
  loadRecentSearches();
  if (typeof window !== "undefined") {
    window.addEventListener("keydown", onGlobalKeydown);
  }
  await loadLookups();
});

onBeforeUnmount(() => {
  if (typeof window !== "undefined") {
    window.removeEventListener("keydown", onGlobalKeydown);
  }
});

function applyRouteState() {
  const routeSearch = String(
    route.query.search || route.query.message_body || ""
  );
  draftSearch.value = routeSearch;
  appliedSearch.value = routeSearch;
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
    message_body: undefined,
    status: filters.status || undefined,
    priority: filters.priority || undefined,
    ticket_type: filters.ticket_type || undefined,
    assigned_to: filters.assigned_to || undefined,
    created_from: filters.created_from || undefined,
    search: appliedSearch.value.trim() || undefined,
  };
}

function compactQuery(query) {
  return Object.fromEntries(
    Object.entries(query || {}).filter(([, value]) => value !== undefined)
  );
}

function sameQuery(left, right) {
  return (
    JSON.stringify(compactQuery(left)) === JSON.stringify(compactQuery(right))
  );
}

async function replaceRouteOrReload() {
  const nextQuery = routeQueryFromState();
  if (sameQuery(route.query, nextQuery)) {
    await reload();
    return;
  }
  await router.replace({ query: nextQuery });
}

async function applyFiltersAndReload() {
  await replaceRouteOrReload();
}

async function submitSearch() {
  appliedSearch.value = draftSearch.value.trim();
  rememberSearch(appliedSearch.value);
  searchFocused.value = false;
  await replaceRouteOrReload();
}

async function clearSearch() {
  draftSearch.value = "";
  appliedSearch.value = "";
  await replaceRouteOrReload();
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
  reloading.value = false;
  reloadPrompt.value = false;
  emptyMessage.value = "No tickets found.";
  try {
    const data = await callWithRetry(
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
        idempotent: true,
        onAttempt: () => {
          if (requestId === activeRequestId) reloading.value = true;
        },
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
    // Network/5xx exhausted retries → unobtrusive reload prompt.
    if (err.code === "NETWORK_ERROR" || (err.status && err.status >= 500)) {
      reloadPrompt.value = true;
    } else {
      // Real 4xx / app error — surface message.
      error.value = err.message;
    }
  } finally {
    if (requestId === activeRequestId) {
      if (append) {
        loadingMore.value = false;
      } else {
        loading.value = false;
      }
      reloading.value = false;
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

// --- Cell rendering helpers (used by the dynamic <td v-for> in the table) ---
const EDITABLE_COLUMN_KEYS = new Set([
  "ticket_type",
  "priority",
  "status",
  "_assign",
  "custom_hold_reason",
]);

function isEditableColumn(key) {
  return EDITABLE_COLUMN_KEYS.has(key);
}

function onCellClick(event, key) {
  if (EDITABLE_COLUMN_KEYS.has(key)) event.stopPropagation();
}

const DATE_COLUMN_KEYS = new Set([
  "creation",
  "custom_hold_from",
  "custom_hold_to",
]);
const DATETIME_COLUMN_KEYS = new Set([
  "modified",
  "response_by",
  "resolution_by",
  "first_responded_on",
  "resolution_date",
]);

function formatCellValue(ticket, key) {
  const raw = ticket?.[key];
  if (raw == null || raw === "") return "-";
  if (DATE_COLUMN_KEYS.has(key)) return formatDate(raw);
  if (DATETIME_COLUMN_KEYS.has(key)) return formatDateTime(raw);
  if (key === "_assign") {
    return ticket.assignee || "Unassigned";
  }
  return String(raw);
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
  // Store current list for prev/next navigation in ticket detail
  sessionStorage.setItem(
    "unity:ticket_nav",
    JSON.stringify({
      ids: tickets.value.map((t) => String(t.name)),
      view: props.view,
    })
  );
  router.push({
    path: `/tickets/${name}`,
    query: {
      ...routeQueryFromState(),
      list_view: props.view,
    },
  });
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
