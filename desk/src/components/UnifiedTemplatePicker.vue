<template>
  <div
    ref="rootRef"
    class="template-picker"
    role="dialog"
    aria-label="Insert template"
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

    <!-- Static vs Email toggle -->
    <div v-if="emailEnabled" class="template-picker__tabs" role="tablist">
      <button
        type="button"
        class="template-picker__tab"
        :class="{ active: mode === 'email' }"
        role="tab"
        :aria-selected="mode === 'email'"
        @click="setMode('email')"
      >
        Email Templates
      </button>
      <button
        type="button"
        class="template-picker__tab"
        :class="{ active: mode === 'static' }"
        role="tab"
        :aria-selected="mode === 'static'"
        @click="setMode('static')"
      >
        Static Templates
      </button>
    </div>

    <!-- Plain-language hint so non-technical agents know which to pick -->
    <p class="template-picker__explainer">
      <template v-if="mode === 'email'">
        Auto-fills details like the student's name ({{ sampleMergeToken }}) for
        each recipient when the email is sent.
      </template>
      <template v-else>
        Inserts fixed text you can edit before sending — nothing is auto-filled.
      </template>
    </p>

    <div class="template-picker__filters">
      <select
        v-model="category"
        class="template-picker__select"
        aria-label="Filter by category"
      >
        <option value="">All categories</option>
        <option
          v-for="cat in activeCategories"
          :key="cat.value"
          :value="cat.value"
        >
          {{ cat.label }}
        </option>
      </select>
      <select
        v-if="mode === 'static'"
        v-model="language"
        class="template-picker__select"
        aria-label="Filter by language"
      >
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
      :placeholder="
        mode === 'email'
          ? 'Search email templates by name or subject…'
          : 'Search templates by title or body…'
      "
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
          {{ tpl.preview || "" }}
        </div>
      </li>
    </ul>

    <div class="template-picker__footer">
      <span class="template-picker__hint">{{ footerHint }}</span>
      <a
        class="template-picker__manage"
        :href="manageHref"
        target="_blank"
        rel="noopener"
        >Manage →</a
      >
    </div>
  </div>
</template>

<script lang="ts">
// Per-mode default-list caches survive the picker being unmounted/remounted
// (v-if toggles), so the second and later opens paint instantly.
let staticListCache: any[] | null = null;
let emailListCache: any[] | null = null;
</script>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

interface Props {
  ticketName?: string | null;
  enableEmailTemplate?: boolean;
  defaultMode?: "static" | "email";
}

interface ItemView {
  name: string;
  title: string;
  category?: string;
  preview?: string;
}

interface CategoryView {
  value: string;
  label: string;
}

interface Emits {
  (event: "close"): void;
  (
    event: "select-static",
    payload: {
      name: string;
      title: string;
      subject: string;
      body: string;
      warnings: string[];
    }
  ): void;
  (event: "select-email", payload: { subject: string; body: string }): void;
}

const props = withDefaults(defineProps<Props>(), {
  ticketName: null,
  enableEmailTemplate: false,
  defaultMode: undefined,
});
const emit = defineEmits<Emits>();

const DEBOUNCE_MS = 200;
// Literal "{{first_name}}" for the explainer — built in script so Vue's template
// parser doesn't try to interpret the nested braces as an interpolation.
const sampleMergeToken = "{{" + "first_name" + "}}";
const emailEnabled = computed(() => !!props.enableEmailTemplate);

const mode = ref<"static" | "email">(
  props.defaultMode || (props.enableEmailTemplate ? "email" : "static")
);

const rootRef = ref<HTMLElement | null>(null);
const searchInput = ref<HTMLInputElement | null>(null);
const category = ref("");
const language = ref("English");
const searchInputValue = ref("");
const search = ref("");
const staticCategories = ref<CategoryView[]>([]);
const emailCategories = ref<CategoryView[]>([]);
const templates = ref<ItemView[]>([]);
const loading = ref(false);
const activeIdx = ref(0);

let listController: AbortController | null = null;
let searchTimer: number | null = null;

const activeCategories = computed(() =>
  mode.value === "email" ? emailCategories.value : staticCategories.value
);
const footerHint = computed(() =>
  mode.value === "email"
    ? "Loads the subject + body; merge fields render per recipient on send."
    : "Inserts the saved text into your message; the subject is filled if the template has one."
);
const manageHref = computed(() =>
  mode.value === "email" ? "/app/email-template" : "/app/hd-canned-response"
);

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

async function loadCategories() {
  // Static (HD Canned Response) categories.
  try {
    const data = await callMethod<any[]>(
      "helpdesk.api.reply_templates.get_reply_template_categories"
    );
    staticCategories.value = (Array.isArray(data) ? data : []).map((c) => ({
      value: c.name,
      label: c.title || c.name,
    }));
  } catch (err) {
    console.warn("[template-picker] static categories failed:", err);
    staticCategories.value = [];
  }
  // Email Template categories (edu_quality doctype; empty when not installed).
  if (props.enableEmailTemplate) {
    try {
      const data = await callMethod<any[]>(
        "helpdesk.api.email_templates.list_email_template_categories"
      );
      emailCategories.value = (Array.isArray(data) ? data : []).map((c) => ({
        value: c.name,
        label: c.category_name || c.name,
      }));
    } catch (err) {
      console.warn("[template-picker] email categories failed:", err);
      emailCategories.value = [];
    }
  }
}

async function loadTemplates() {
  listController?.abort();
  listController = new AbortController();
  const isEmail = mode.value === "email";
  const isDefault =
    !search.value &&
    !category.value &&
    (!isEmail ? language.value === "English" : true);
  const cache = isEmail ? emailListCache : staticListCache;
  if (isDefault && cache) {
    templates.value = cache;
    activeIdx.value = templates.value.length ? 0 : -1;
    loading.value = false;
  } else {
    loading.value = true;
  }
  try {
    let list: ItemView[] = [];
    if (isEmail) {
      const data = await callMethod<any[]>(
        "helpdesk.api.email_templates.list_email_templates",
        {
          search: search.value || undefined,
          category: category.value || undefined,
          limit: 50,
        },
        listController.signal
      );
      list = (Array.isArray(data) ? data : []).map((t) => ({
        name: t.name,
        title: t.name,
        category: t.email_template_category || "",
        preview: t.subject || "",
      }));
    } else {
      const data = await callMethod<any[]>(
        "helpdesk.api.reply_templates.list_reply_templates",
        {
          category: category.value || undefined,
          language: language.value || undefined,
          search: search.value || undefined,
          limit: 50,
        },
        listController.signal
      );
      list = (Array.isArray(data) ? data : []).map((t) => ({
        name: t.name,
        title: t.title || t.name,
        category: t.category || "",
        preview: t.body_preview || "",
      }));
    }
    templates.value = list;
    activeIdx.value = list.length ? 0 : -1;
    if (isDefault) {
      if (isEmail) emailListCache = list;
      else staticListCache = list;
    }
  } catch (err: any) {
    if (err?.name !== "AbortError") {
      console.warn("[template-picker] list failed:", err);
      if (!(isDefault && cache)) {
        templates.value = [];
        activeIdx.value = -1;
      }
    }
  } finally {
    loading.value = false;
  }
}

function setMode(next: "static" | "email") {
  if (mode.value === next) return;
  mode.value = next;
  category.value = "";
  searchInputValue.value = "";
  search.value = "";
}

watch(searchInputValue, (next) => {
  if (searchTimer !== null) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    search.value = (next || "").trim();
  }, DEBOUNCE_MS) as unknown as number;
});

watch([mode, category, language, search], () => {
  void loadTemplates();
});

function moveActive(delta: number) {
  if (!templates.value.length) return;
  const len = templates.value.length;
  const current = activeIdx.value < 0 ? 0 : activeIdx.value;
  activeIdx.value = (((current + delta) % len) + len) % len;
  const item = rootRef.value?.querySelector<HTMLElement>(
    `.template-picker__item:nth-child(${activeIdx.value + 1})`
  );
  item?.scrollIntoView({ block: "nearest" });
}

function onEnter() {
  if (activeIdx.value < 0 || activeIdx.value >= templates.value.length) return;
  void selectTemplate(templates.value[activeIdx.value]);
}

async function selectTemplate(tpl: ItemView) {
  try {
    if (mode.value === "email") {
      const content = await callMethod<{ subject: string; body: string }>(
        "helpdesk.api.email_templates.get_email_template_content",
        { name: tpl.name }
      );
      emit("select-email", {
        subject: content.subject || "",
        body: content.body || "",
      });
    } else {
      const rendered = await callMethod<{
        name: string;
        title: string;
        subject: string;
        body: string;
        warnings: string[];
      }>("helpdesk.api.reply_templates.render_reply_template", {
        name: tpl.name,
        ticket_name: props.ticketName || undefined,
      });
      emit("select-static", rendered);
    }
    emit("close");
  } catch (err) {
    console.warn("[template-picker] select failed:", err);
  }
}

function onDocumentMousedown(event: MouseEvent) {
  if (rootRef.value && !rootRef.value.contains(event.target as Node)) {
    emit("close");
  }
}
function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") emit("close");
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
  if (searchTimer !== null) window.clearTimeout(searchTimer);
  listController?.abort();
});
</script>

<style scoped>
.template-picker {
  position: absolute;
  top: 44px;
  right: 0;
  width: 480px;
  max-height: 480px;
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
.template-picker__tabs {
  display: flex;
  gap: 4px;
  padding: 8px 12px 0;
}
.template-picker__tab {
  flex: 1;
  padding: 6px 8px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #f8fafc;
  color: #475569;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}
.template-picker__tab.active {
  background: #4f46e5;
  border-color: #4f46e5;
  color: #fff;
}
.template-picker__explainer {
  margin: 6px 12px 0;
  font-size: 11px;
  color: #64748b;
  line-height: 1.4;
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
  white-space: nowrap;
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.template-picker__hint {
  font-size: 10.5px;
  color: #94a3b8;
}
.template-picker__manage {
  font-size: 11.5px;
  color: #4f46e5;
  text-decoration: none;
  white-space: nowrap;
}
.template-picker__manage:hover {
  text-decoration: underline;
}
</style>
