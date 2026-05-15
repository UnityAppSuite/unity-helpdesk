<template>
  <div
    ref="rootRef"
    class="template-picker"
    role="dialog"
    aria-label="Insert reply template"
    @mousedown.stop
    @click.stop
  >
    <div class="template-picker__header">
      <div class="template-picker__title">Insert template</div>
      <button
        type="button"
        class="template-picker__close"
        aria-label="Close"
        @click="emit('close')"
      >
        ✕
      </button>
    </div>

    <div class="template-picker__filters">
      <select v-model="category" class="template-picker__select">
        <option value="">All categories</option>
        <option v-for="cat in categories" :key="cat.name" :value="cat.name">
          {{ cat.title }}
        </option>
      </select>
      <select v-model="language" class="template-picker__select">
        <option value="">Any language</option>
        <option value="English">English</option>
        <option value="Hindi">Hindi</option>
        <option value="Marathi">Marathi</option>
      </select>
    </div>

    <input
      ref="searchInput"
      v-model="searchInputValue"
      class="template-picker__search"
      type="text"
      placeholder="Search templates by title or body…"
      autocomplete="off"
      @keydown.down.prevent="moveActive(1)"
      @keydown.up.prevent="moveActive(-1)"
      @keydown.enter.prevent="onEnter"
      @keydown.esc="emit('close')"
    />

    <div v-if="loading" class="template-picker__loading">Loading…</div>
    <div v-else-if="!templates.length" class="template-picker__empty">
      No templates found.
    </div>
    <ul v-else class="template-picker__list" role="listbox">
      <li
        v-for="(tpl, idx) in templates"
        :key="tpl.name"
        class="template-picker__item"
        :class="{ active: idx === activeIdx }"
        role="option"
        :aria-selected="idx === activeIdx"
        @mouseenter="activeIdx = idx"
        @mousedown.prevent="selectTemplate(tpl)"
      >
        <div class="template-picker__row">
          <span class="template-picker__item-title">{{ tpl.title }}</span>
          <span v-if="tpl.category" class="template-picker__chip">{{
            tpl.category
          }}</span>
        </div>
        <div class="template-picker__preview">
          {{ tpl.body_preview || "" }}
        </div>
      </li>
    </ul>

    <div class="template-picker__footer">
      <a
        class="template-picker__manage"
        href="/app/hd-canned-response"
        target="_blank"
        rel="noopener"
        >Manage templates →</a
      >
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

interface Props {
  ticketName?: string | null;
}

interface Category {
  name: string;
  title: string;
  color?: string;
  description?: string;
}

interface Template {
  name: string;
  title: string;
  category?: string;
  language?: string;
  subject_template?: string;
  body_preview?: string;
  modified?: string;
}

interface Rendered {
  name: string;
  title: string;
  subject: string;
  body: string;
  warnings: string[];
}

interface Emits {
  (event: "close"): void;
  (event: "select", payload: Rendered): void;
}

const props = withDefaults(defineProps<Props>(), {
  ticketName: null,
});
const emit = defineEmits<Emits>();

const DEBOUNCE_MS = 200;
const MIN_SEARCH_CHARS = 0; // empty search allowed; just lists everything

const rootRef = ref<HTMLElement | null>(null);
const searchInput = ref<HTMLInputElement | null>(null);
const category = ref("");
const language = ref("English");
const searchInputValue = ref("");
const search = ref("");
const categories = ref<Category[]>([]);
const templates = ref<Template[]>([]);
const loading = ref(false);
const activeIdx = ref(0);

let listController: AbortController | null = null;
let searchTimer: number | null = null;

// --- Frappe call helper (raw fetch, works in both desk/ and unity_helpdesk/ builds) ---

async function callMethod<T>(
  method: string,
  params: Record<string, unknown> = {},
  signal?: AbortSignal
): Promise<T> {
  const response = await fetch(`/api/method/${method}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-Frappe-CSRF-Token": (window as any).csrf_token || "",
    },
    body: JSON.stringify(params),
    credentials: "same-origin",
    signal,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.exc) {
    const err: any = new Error(payload.exc || `Request failed: ${method}`);
    err.status = response.status;
    err.payload = payload;
    throw err;
  }
  return payload.message as T;
}

// --- Data loading ---

async function loadCategories() {
  try {
    const data = await callMethod<Category[]>(
      "helpdesk.api.reply_templates.get_reply_template_categories"
    );
    categories.value = Array.isArray(data) ? data : [];
  } catch (err) {
    console.warn("[template-picker] categories load failed:", err);
    categories.value = [];
  }
}

async function loadTemplates() {
  listController?.abort();
  listController = new AbortController();
  loading.value = true;
  try {
    const data = await callMethod<Template[]>(
      "helpdesk.api.reply_templates.list_reply_templates",
      {
        category: category.value || undefined,
        language: language.value || undefined,
        search: search.value || undefined,
        limit: 50,
      },
      listController.signal
    );
    templates.value = Array.isArray(data) ? data : [];
    activeIdx.value = templates.value.length ? 0 : -1;
  } catch (err: any) {
    if (err?.name !== "AbortError") {
      console.warn("[template-picker] list failed:", err);
      templates.value = [];
      activeIdx.value = -1;
    }
  } finally {
    loading.value = false;
  }
}

// --- Search debounce ---

watch(searchInputValue, (next) => {
  if (searchTimer !== null) {
    window.clearTimeout(searchTimer);
  }
  searchTimer = window.setTimeout(() => {
    search.value = (next || "").trim();
  }, DEBOUNCE_MS) as unknown as number;
});

watch([category, language, search], () => {
  void loadTemplates();
});

// --- Keyboard nav ---

function moveActive(delta: number) {
  if (!templates.value.length) return;
  const len = templates.value.length;
  const current = activeIdx.value < 0 ? 0 : activeIdx.value;
  activeIdx.value = (((current + delta) % len) + len) % len;
  scrollActiveIntoView();
}

function scrollActiveIntoView() {
  if (!rootRef.value) return;
  const item = rootRef.value.querySelector<HTMLElement>(
    `.template-picker__item:nth-child(${activeIdx.value + 1})`
  );
  item?.scrollIntoView({ block: "nearest" });
}

function onEnter() {
  if (activeIdx.value < 0 || activeIdx.value >= templates.value.length) return;
  void selectTemplate(templates.value[activeIdx.value]);
}

// --- Selection ---

async function selectTemplate(tpl: Template) {
  try {
    const rendered = await callMethod<Rendered>(
      "helpdesk.api.reply_templates.render_reply_template",
      {
        name: tpl.name,
        ticket_name: props.ticketName || undefined,
      }
    );
    emit("select", rendered);
    emit("close");
  } catch (err) {
    console.warn("[template-picker] render failed:", err);
  }
}

// --- Lifecycle ---

function onDocumentMousedown(event: MouseEvent) {
  if (rootRef.value && !rootRef.value.contains(event.target as Node)) {
    emit("close");
  }
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    emit("close");
  }
}

onMounted(async () => {
  document.addEventListener("mousedown", onDocumentMousedown, true);
  document.addEventListener("keydown", onDocumentKeydown);
  await loadCategories();
  await loadTemplates();
  searchInput.value?.focus();
});

onBeforeUnmount(() => {
  document.removeEventListener("mousedown", onDocumentMousedown, true);
  document.removeEventListener("keydown", onDocumentKeydown);
  if (searchTimer !== null) {
    window.clearTimeout(searchTimer);
  }
  listController?.abort();
});
</script>

<style scoped>
.template-picker {
  position: absolute;
  top: 44px;
  right: 0;
  width: 480px;
  max-height: 460px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
  z-index: 50;
  display: flex;
  flex-direction: column;
  font-family: Inter, sans-serif;
  font-size: 13px;
}

.template-picker__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid #f1f5f9;
}

.template-picker__title {
  font-weight: 600;
  font-size: 13px;
  color: #111827;
}

.template-picker__close {
  background: none;
  border: none;
  color: #94a3b8;
  font-size: 14px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
}

.template-picker__close:hover {
  background: #f1f5f9;
  color: #111827;
}

.template-picker__filters {
  display: flex;
  gap: 6px;
  padding: 8px 12px 0;
}

.template-picker__select {
  flex: 1;
  padding: 5px 8px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 12px;
  background: #fff;
}

.template-picker__search {
  margin: 8px 12px;
  padding: 6px 10px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
}

.template-picker__search:focus {
  border-color: #4f46e5;
  box-shadow: 0 0 0 2px rgba(79, 70, 229, 0.15);
}

.template-picker__loading,
.template-picker__empty {
  padding: 16px;
  text-align: center;
  color: #6b7280;
  font-size: 12px;
}

.template-picker__list {
  list-style: none;
  margin: 0;
  padding: 0 0 4px;
  overflow-y: auto;
  flex: 1;
}

.template-picker__item {
  padding: 8px 12px;
  cursor: pointer;
  border-left: 3px solid transparent;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.template-picker__item.active,
.template-picker__item:hover {
  background: #f8fafc;
  border-left-color: #4f46e5;
}

.template-picker__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.template-picker__item-title {
  font-weight: 500;
  color: #111827;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-picker__chip {
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 999px;
  background: #ede9fe;
  color: #4338ca;
}

.template-picker__preview {
  color: #6b7280;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-picker__footer {
  padding: 8px 12px;
  border-top: 1px solid #f1f5f9;
  text-align: right;
}

.template-picker__manage {
  font-size: 11.5px;
  color: #4f46e5;
  text-decoration: none;
}

.template-picker__manage:hover {
  text-decoration: underline;
}
</style>
