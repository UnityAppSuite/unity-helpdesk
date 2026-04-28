<template>
  <Editor
    v-model="content"
    :api-key="apiKey"
    :disabled="disabled"
    :init="editorConfig"
    @on-init="handleInit"
    @blur="$emit('blur')"
  />
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

interface Props {
  modelValue: string;
  placeholder?: string;
  disabled?: boolean;
  minHeight?: number;
}

interface Emits {
  (event: "update:modelValue", value: string): void;
  (event: "blur"): void;
}

const props = withDefaults(defineProps<Props>(), {
  placeholder: "",
  disabled: false,
  minHeight: 240,
});

const emit = defineEmits<Emits>();
const content = ref(props.modelValue || "");
const editorInstance = ref<any>(null);
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
    "undo redo | blocks | bold italic underline | bullist numlist | blockquote table link | removeformat code",
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
