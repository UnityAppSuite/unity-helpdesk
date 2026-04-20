<template>
  <section class="page">
    <div class="toolbar">
      <input
        v-model="search"
        class="search"
        type="search"
        placeholder="Search users by name or email"
      />
      <button class="btn secondary" @click="load">Refresh</button>
    </div>
    <div class="table-shell">
      <div class="table-header">
        <strong>Users</strong>
        <span>{{ filteredUsers.length }} users</span>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-else-if="loading" class="empty">Loading users...</p>
      <div v-else class="scroll-x">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Username / Email</th>
              <th>Helpdesk Role</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in filteredUsers" :key="user.name">
              <td>
                <span class="avatar">{{
                  initials(user.full_name || user.name)
                }}</span
                >{{ user.full_name || user.name }}
              </td>
              <td>{{ user.email || user.name }}</td>
              <td>{{ user.is_agent ? "Agent" : "User" }}</td>
              <td><span class="badge green">Enabled</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { call, initials } from "../api";

const emit = defineEmits(["title"]);
const users = ref([]);
const search = ref("");
const loading = ref(false);
const error = ref("");

const filteredUsers = computed(() => {
  const term = search.value.toLowerCase();
  if (!term) return users.value;
  return users.value.filter((user) =>
    [user.name, user.full_name, user.email].some((value) =>
      (value || "").toLowerCase().includes(term)
    )
  );
});

onMounted(() => {
  emit("title", "Users", "Enabled users and helpdesk agents");
  load();
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    users.value = await call("helpdesk.api.unity.get_users");
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}
</script>
