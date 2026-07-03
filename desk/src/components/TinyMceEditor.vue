<template>
  <div class="tinymce-editor-wrap">
    <Editor
      v-model="content"
      :api-key="apiKey"
      :disabled="disabled"
      :init="editorConfig"
      @on-init="handleInit"
      @blur="$emit('blur')"
    />
    <UnifiedTemplatePicker
      v-if="templatePickerOpen"
      :ticket-name="ticketName"
      :enable-email-template="enableEmailTemplate"
      @close="templatePickerOpen = false"
      @select-static="onTemplateSelected"
      @select-email="onEmailTemplateSelected"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import Editor from "@tinymce/tinymce-vue";
import tinymce from "tinymce/tinymce";
import "tinymce/icons/default";
import "tinymce/models/dom";
import "tinymce/plugins/advlist";
import "tinymce/plugins/autolink";
import "tinymce/plugins/code";
import "tinymce/plugins/link";
import "tinymce/plugins/lists";
import "tinymce/plugins/table";
import "tinymce/themes/silver";
import "tinymce/skins/ui/oxide/skin.min.css";
import "tinymce/skins/content/default/content.min.css";
import UnifiedTemplatePicker from "./UnifiedTemplatePicker.vue";

interface Props {
  modelValue: string;
  placeholder?: string;
  disabled?: boolean;
  minHeight?: number;
  ticketName?: string | null;
  enableEmailTemplate?: boolean;
  enableAttach?: boolean;
}

interface Emits {
  (event: "update:modelValue", value: string): void;
  (event: "blur"): void;
  (event: "attach"): void;
  (event: "template-subject", value: string): void;
  (
    event: "email-template-selected",
    value: { subject: string; body: string }
  ): void;
}

const props = withDefaults(defineProps<Props>(), {
  placeholder: "",
  disabled: false,
  minHeight: 240,
  ticketName: null,
  enableEmailTemplate: false,
  enableAttach: false,
});

const emit = defineEmits<Emits>();
const content = ref(props.modelValue || "");
const editorInstance = ref<any>(null);
const templatePickerOpen = ref(false);
const apiKey = "no-api-key";

const editorConfig = computed(() => ({
  branding: false,
  menubar: false,
  min_height: props.minHeight,
  plugins: "advlist autolink code link lists table",
  promotion: false,
  resize: "vertical",
  skin: false,
  content_css: false,
  statusbar: false,
  // Keep the toolbar docked inside the editor — `toolbar_sticky` (TinyMCE default)
  // detaches it and floats it over the page when the editor sits in a scrolling
  // modal (the bulk-email composer), so it's explicitly disabled.
  toolbar_sticky: false,
  // Wrap overflow buttons onto extra rows instead of the default "floating" drawer
  // (the "…" popup that stayed open over the form and didn't close on
  // template-select / outside-click). Wrapping keeps every button visible inline.
  toolbar_mode: "wrap",
  toolbar:
    "undo redo | blocks | bold italic underline | bullist numlist | blockquote table link | removeformat code |" +
    " templates" +
    (props.enableAttach ? " attach" : ""),
  placeholder: props.placeholder,
  content_style: `
    body {
      font-family: Inter, sans-serif;
      font-size: 14px;
      line-height: 1.6;
      color: #111827;
      margin: 0.75rem;
    }
    p { margin: 0 0 0.75rem; }
    blockquote {
      border-left: 3px solid #cbd5e1;
      margin: 0.75rem 0;
      padding-left: 0.75rem;
      color: #475569;
    }
  `,
  setup: (editor: any) => {
    editor.ui.registry.addButton("templates", {
      text: "Templates",
      tooltip: "Insert an email or static template",
      onAction: () => {
        templatePickerOpen.value = true;
      },
    });
    if (props.enableAttach) {
      editor.ui.registry.addButton("attach", {
        icon: "upload",
        text: "Attach",
        tooltip: "Attach files",
        onAction: () => {
          emit("attach");
        },
      });
    }
  },
}));

watch(
  () => props.modelValue,
  (value) => {
    const nextValue = value || "";
    if (nextValue === content.value) return;
    content.value = nextValue;
    if (
      editorInstance.value &&
      editorInstance.value.getContent() !== nextValue
    ) {
      editorInstance.value.setContent(nextValue);
    }
  }
);

watch(content, (value) => {
  emit("update:modelValue", value || "");
});

function handleInit(_event: unknown, editor: any) {
  editorInstance.value = editor;
  // Re-apply current content so a re-initialization (e.g. the toolbar config
  // changing when `enableEmailTemplate` flips on a tab switch) never leaves the
  // editor visually blank while the model still holds text.
  const value = content.value || "";
  if (value && editor.getContent() !== value) {
    editor.setContent(value);
  }
}

function setContent(value = "") {
  content.value = value;
  editorInstance.value?.setContent(value || "");
}

function insertContent(value = "") {
  if (!value) return;
  if (!editorInstance.value) {
    setContent(`${content.value}${value}`);
    return;
  }
  editorInstance.value.insertContent(value);
  content.value = editorInstance.value.getContent();
}

interface Rendered {
  name: string;
  title: string;
  subject: string;
  body: string;
  warnings: string[];
}

function onTemplateSelected(rendered: Rendered) {
  if (rendered.body) {
    insertContent(rendered.body);
  }
  if (rendered.subject) {
    emit("template-subject", rendered.subject);
  }
  if (rendered.warnings && rendered.warnings.length) {
    console.warn("[template-picker] warnings:", rendered.warnings);
  }
  templatePickerOpen.value = false;
}

// Email Template is the PRIMARY source: replace the whole body (not insert) and
// hand the subject to the parent. The parent sets v-model (modelValue), which
// flows back into the editor via the modelValue watch.
function onEmailTemplateSelected(payload: { subject: string; body: string }) {
  if (payload.body) {
    setContent(payload.body);
  }
  emit("email-template-selected", payload);
  templatePickerOpen.value = false;
}

function focus() {
  editorInstance.value?.focus();
}

function clear() {
  setContent("");
}

function quoteReply(value = "") {
  setContent(`<p></p><blockquote>${value || ""}</blockquote><p></p>`);
  focus();
}

function isEmpty() {
  const plainText =
    tinymce.activeEditor === editorInstance.value
      ? editorInstance.value?.getContent({ format: "text" }) || ""
      : editorInstance.value?.getContent({ format: "text" }) ||
        content.value.replace(/<[^>]*>/g, " ");
  return !plainText.replace(/\s+/g, " ").trim();
}

defineExpose({
  clear,
  focus,
  insertContent,
  isEmpty,
  quoteReply,
  setContent,
  get editor() {
    return editorInstance.value;
  },
});
</script>

<style scoped>
.tinymce-editor-wrap {
  position: relative;
}
</style>
