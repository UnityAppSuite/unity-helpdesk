<template>
  <div
    v-if="expand"
    class="rounded-lg bg-white p-3 shadow-sm ring-1 ring-gray-200"
  >
    <div class="mb-3 flex items-start justify-between gap-3">
      <span class="text-base">
        <span class="flex items-center justify-between">
          <UserAvatar
            :name="authStore.userName"
            :image="authStore.userImage"
            expand
            strong
          />
          <slot name="top-right" />
        </span>
        <slot name="top-bottom" />
      </span>
    </div>

    <TinyMceEditor
      ref="e"
      :model-value="content"
      :placeholder="placeholder"
      :min-height="220"
      @update:model-value="$emit('update:content', $event)"
    />

    <div v-if="attachments.length" class="mt-3 flex flex-wrap gap-2">
      <AttachmentItem
        v-for="attachment in attachments"
        :key="attachment.file_url"
        :label="attachment.file_name"
      >
        <template #suffix>
          <Icon
            icon="lucide:x"
            @click.stop="
              $emit(
                'update:attachments',
                attachments.filter(
                  (item) => item.file_url !== attachment.file_url
                )
              )
            "
          />
        </template>
      </AttachmentItem>
    </div>

    <div class="mt-3 flex flex-col gap-2">
      <slot name="bottom-top" />
      <div
        class="flex flex-col gap-2 overflow-auto sm:flex-row sm:items-center sm:justify-between"
      >
        <div class="flex items-center gap-1">
          <slot name="bottom-left" />
          <FileUploader
            :upload-args="{
              folder: 'Home/Helpdesk',
              private: true,
            }"
            @success="(file: File) => $emit('update:attachments', [...attachments, file])"
          >
            <template #default="{ openFileSelector }">
              <Button theme="gray" variant="ghost" @click="openFileSelector()">
                <template #icon>
                  <Icon icon="lucide:paperclip" />
                </template>
              </Button>
            </template>
          </FileUploader>
        </div>
        <div class="flex items-center gap-2">
          <Button
            label="Discard"
            theme="gray"
            variant="subtle"
            @click="clear"
          />
          <slot name="bottom-right" />
        </div>
      </div>
    </div>
  </div>
  <div
    v-else
    class="flex w-full cursor-pointer items-center gap-2 rounded bg-gray-100 px-3.5 py-2 hover:bg-gray-200"
    @click="() => $emit('update:expand', !expand)"
  >
    <UserAvatar
      :name="authStore.userName"
      :image="authStore.userImage"
      size="sm"
    />
    <span class="text-base text-gray-700">
      {{ placeholder }}
    </span>
  </div>
</template>

<script setup lang="ts">
import { Button, FileUploader } from "frappe-ui";
import { Icon } from "@iconify/vue";
import { useAuthStore } from "@/stores/auth";
import { AttachmentItem, TinyMceEditor, UserAvatar } from "@/components";
import { File } from "@/types";
import { ref } from "vue";

interface P {
  content: string;
  placeholder: string;
  attachments: File[];
  expand?: boolean;
}

interface E {
  (event: "clear"): void;
  (event: "update:content", content: string): void;
  (event: "update:attachments", attachments: File[]): void;
  (event: "update:expand", expand: boolean): void;
}

withDefaults(defineProps<P>(), {
  expand: false,
});
const emit = defineEmits<E>();
const e = ref(null);
const authStore = useAuthStore();

function clear() {
  e.value?.clear();
  emit("update:attachments", []);
  emit("clear");
}

defineExpose({
  clear,
  focus: () => {
    e.value?.focus();
  },
  insertContent: (value: string) => {
    e.value?.insertContent(value);
  },
  isEmpty: () => e.value?.isEmpty?.() ?? true,
  quoteReply: (value: string) => {
    e.value?.quoteReply(value);
  },
});
</script>
