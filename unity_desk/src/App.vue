<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <RouterLink class="brand" to="/tickets/my" @click="sidebarOpen = false">
        <span class="brand-mark">
          <img :src="brandLogo" alt="Unity Helpdesk" />
        </span>
        <span class="brand-copy">
          <strong>Unity Helpdesk</strong>
          <small>Fast support workspace</small>
        </span>
      </RouterLink>

      <nav>
        <p>Tickets</p>
        <RouterLink to="/tickets/my" @click="sidebarOpen = false">
          My Tickets
        </RouterLink>
        <RouterLink to="/tickets/all" @click="sidebarOpen = false">
          All Tickets
        </RouterLink>
        <RouterLink to="/dashboard" @click="sidebarOpen = false">
          Dashboard
        </RouterLink>
        <p>Management</p>
        <RouterLink to="/agents" @click="sidebarOpen = false"
          >Agents</RouterLink
        >
      </nav>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="topbar-main">
          <button class="menu-btn" @click="sidebarOpen = !sidebarOpen">
            Menu
          </button>
          <div>
            <strong>{{ pageTitle }}</strong>
            <span>{{ pageSubtitle }}</span>
          </div>
        </div>
        <div class="topbar-actions">
          <button class="btn" @click="openComposer = true">New Ticket</button>

          <!-- Avatar with dropdown -->
          <div class="avatar-wrap" @click="profileMenuOpen = !profileMenuOpen">
            <img
              v-if="profile.user_image"
              class="top-avatar-image"
              :src="profile.user_image"
              :alt="profile.full_name || profile.name || 'User'"
            />
            <span v-else class="avatar avatar-lg top-avatar-fallback">
              {{ initials(profile.full_name || profile.name || "") || "U" }}
            </span>
            <div v-if="profileMenuOpen" class="profile-dropdown" @click.stop>
              <div class="profile-dropdown-header">
                <strong>{{
                  profile.full_name || profile.name || "User"
                }}</strong>
                <small>{{ profile.email || "" }}</small>
              </div>
              <a class="profile-dropdown-item" href="/app" target="_top">
                Switch to Desk
              </a>
            </div>
          </div>
        </div>
      </header>

      <!-- Click-away overlay for profile menu -->
      <div
        v-if="profileMenuOpen"
        class="profile-overlay"
        @click="profileMenuOpen = false"
      ></div>

      <RouterView @title="setTitle" />
    </main>

    <!-- Create Ticket modal -->
    <div v-if="openComposer" class="modal-backdrop" @click.self="closeComposer">
      <section class="modal-card">
        <div class="modal-header">
          <div>
            <strong>Create Ticket</strong>
            <span
              >Create a ticket and send the first email to the customer.</span
            >
          </div>
          <button class="btn secondary" @click="closeComposer">Close</button>
        </div>
        <div class="modal-body stack">
          <p v-if="composerError" class="error">{{ composerError }}</p>

          <!-- Customer Email with user search -->
          <label>
            Customer Email
            <div class="input-with-action">
              <input
                v-model="composer.raised_by"
                type="email"
                placeholder="customer@example.com"
                autocomplete="off"
                @input="onEmailInput"
                @focus="onEmailInput"
              />
              <a
                href="/app/user/new-user-1"
                target="_blank"
                class="btn secondary input-action-btn"
                title="Add new user"
              >
                + Add User
              </a>
            </div>
            <!-- User suggestions -->
            <ul v-if="userSuggestions.length" class="user-suggestions">
              <li
                v-for="u in userSuggestions"
                :key="u.name"
                @mousedown.prevent="selectUser(u)"
              >
                <span
                  class="avatar"
                  style="width: 20px; height: 20px; font-size: 9px"
                >
                  {{ initials(u.full_name || u.name) }}
                </span>
                <span>{{ u.full_name || u.name }}</span>
                <small>{{ u.email || u.name }}</small>
              </li>
            </ul>
          </label>

          <label>
            Subject
            <input
              v-model="composer.subject"
              type="text"
              placeholder="Enter ticket subject"
            />
          </label>
          <label>
            Ticket Type
            <select v-model="composer.ticket_type">
              <option value="">Not set</option>
              <option
                v-for="ticketType in ticketTypes"
                :key="ticketType.name"
                :value="ticketType.name"
              >
                {{ ticketType.name }}
              </option>
            </select>
          </label>
          <label>
            Priority
            <select v-model="composer.priority">
              <option value="">Not set</option>
              <option>High</option>
              <option>Medium</option>
              <option>Low</option>
            </select>
          </label>
          <label>
            Assign To
            <select v-model="composer.assignee">
              <option value="">Unassigned</option>
              <option
                v-for="agent in agents"
                :key="agent.name"
                :value="agent.name"
              >
                {{ agent.full_name || agent.name }}
              </option>
            </select>
          </label>
          <label>
            Email Message
            <textarea
              v-model="composer.message"
              rows="8"
              placeholder="Write the email that should be sent to the customer"
            ></textarea>
          </label>
        </div>
        <div class="modal-footer">
          <button class="btn secondary" @click="closeComposer">Cancel</button>
          <button class="btn" :disabled="composerSaving" @click="createTicket">
            {{ composerSaving ? "Sending..." : "Create & Send Email" }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import {
  call,
  initials,
  getAgents,
  getTicketTypes,
  getSidebarProfile,
  searchUsers,
} from "./api";

const router = useRouter();
const sidebarOpen = ref(false);
const pageTitle = ref("My Tickets");
const pageSubtitle = ref("Fast support workspace");
const profile = ref({});
const profileMenuOpen = ref(false);
const brandLogo = "/assets/helpdesk/unity_desk/favicon.svg";
const agents = ref([]);
const ticketTypes = ref([]);
const openComposer = ref(false);
const composerSaving = ref(false);
const composerError = ref("");
const userSuggestions = ref([]);
let suggestTimeout = null;
const composer = reactive({
  raised_by: "",
  subject: "",
  message: "",
  priority: "",
  ticket_type: "",
  assignee: "",
});

onMounted(async () => {
  const [profileData, agentRows, typeRows] = await Promise.allSettled([
    getSidebarProfile(),
    getAgents(),
    getTicketTypes(),
  ]);
  profile.value =
    profileData.status === "fulfilled" ? profileData.value || {} : {};
  agents.value = agentRows.status === "fulfilled" ? agentRows.value || [] : [];
  ticketTypes.value =
    typeRows.status === "fulfilled" ? typeRows.value || [] : [];
});

function setTitle(title, subtitle = "Fast support workspace") {
  pageTitle.value = title;
  pageSubtitle.value = subtitle;
}

function closeComposer() {
  openComposer.value = false;
  composerSaving.value = false;
  composerError.value = "";
  userSuggestions.value = [];
  composer.raised_by = "";
  composer.subject = "";
  composer.message = "";
  composer.priority = "";
  composer.ticket_type = "";
  composer.assignee = "";
}

function onEmailInput() {
  clearTimeout(suggestTimeout);
  const query = composer.raised_by;
  if (!query || query.length < 2) {
    userSuggestions.value = [];
    return;
  }
  suggestTimeout = setTimeout(async () => {
    try {
      userSuggestions.value = await searchUsers(query);
    } catch {
      userSuggestions.value = [];
    }
  }, 300);
}

function selectUser(user) {
  composer.raised_by = user.email || user.name;
  userSuggestions.value = [];
}

async function createTicket() {
  composerSaving.value = true;
  composerError.value = "";
  try {
    const ticket = await call("helpdesk.api.unity_ext.create_ticket", {
      subject: composer.subject,
      raised_by: composer.raised_by,
      message: composer.message,
      priority: composer.priority,
      ticket_type: composer.ticket_type,
      assignee: composer.assignee,
    });
    closeComposer();
    if (ticket?.name) {
      router.push(`/tickets/${ticket.name}`);
    }
  } catch (err) {
    composerError.value = err.message;
  } finally {
    composerSaving.value = false;
  }
}
</script>
