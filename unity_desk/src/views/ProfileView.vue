<template>
  <section class="page">
    <div class="detail-grid">
      <div class="detail-section">
        <h3>User Profile</h3>
        <div class="detail-body">
          <p v-if="error" class="error">{{ error }}</p>
          <p v-else-if="loading" class="empty">Loading profile...</p>
          <div v-else class="field-grid">
            <div class="field">
              <label>Name</label
              ><strong>{{ profile.full_name || profile.name }}</strong>
            </div>
            <div class="field">
              <label>Username</label
              ><strong>{{ profile.username || profile.name }}</strong>
            </div>
            <div class="field">
              <label>Email</label><strong>{{ profile.email || "-" }}</strong>
            </div>
            <div class="field">
              <label>Roles</label
              ><strong>{{ (profile.roles || []).join(", ") }}</strong>
            </div>
          </div>
        </div>
      </div>
      <div class="detail-section">
        <h3>Settings</h3>
        <div class="detail-body stack">
          <p class="muted">
            For security, your current password is never shown here.
          </p>
          <a class="btn" href="/app/user-profile">Change Password</a>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { call } from "../api";

const emit = defineEmits(["title"]);
const profile = ref({});
const loading = ref(false);
const error = ref("");

onMounted(() => {
  emit("title", "Profile & Settings", "Your account details");
  load();
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    profile.value = await call("helpdesk.api.unity.get_profile");
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}
</script>
