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
    <TemplatePicker
      v-if="pickerOpen"
      :ticket-name="ticketName"
      @close="pickerOpen = false"
      @select="onTemplateSelected"
    />
    <EmailTemplatePicker
      v-if="emailTemplatePickerOpen"
      @close="emailTemplatePickerOpen = false"
      @select="onEmailTemplateSelected"
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
import TemplatePicker from "./TemplatePicker.vue";
import EmailTemplatePicker from "./EmailTemplatePicker.vue";

interface Props {
  modelValue: string;
  placeholder?: string;
  disabled?: boolean;
  minHeight?: number;
  ticketName?: string | null;
  enableEmailTemplate?: boolean;
}

interface Emits {
  (event: "update:modelValue", value: string): void;
  (event: "blur"): void;
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
});

const emit = defineEmits<Emits>();
const content = ref(props.modelValue || "");
const editorInstance = ref<any>(null);
const pickerOpen = ref(false);
const emailTemplatePickerOpen = ref(false);
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
  toolbar:
    "undo redo | blocks | bold italic underline | bullist numlist | blockquote table link | removeformat code |" +
    (props.enableEmailTemplate ? " emailtemplate" : "") +
    " templates",
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
      text: "Static Templates",
      tooltip: "Insert saved reply template (static text)",
      onAction: () => {
        pickerOpen.value = true;
      },
    });
    editor.ui.registry.addButton("emailtemplate", {
      text: "Email Template",
      tooltip: "Load a Frappe Email Template (subject + body)",
      onAction: () => {
        emailTemplatePickerOpen.value = true;
      },
    });
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
  pickerOpen.value = false;
}

// Email Template is the PRIMARY source: replace the whole body (not insert) and
// hand the subject to the parent. The parent sets v-model (modelValue), which
// flows back into the editor via the modelValue watch.
function onEmailTemplateSelected(payload: { subject: string; body: string }) {
  if (payload.body) {
    setContent(payload.body);
  }
  emit("email-template-selected", payload);
  emailTemplatePickerOpen.value = false;
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
