<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <RouterLink class="brand" to="/tickets/my" @click="sidebarOpen = false">
        <img :src="logoSrc" alt="" />
        <span>
          <strong>Unity Helpdesk</strong>
          <small>Simple Ticket Desk</small>
        </span>
      </RouterLink>

      <nav>
        <p>Tickets</p>
        <RouterLink to="/tickets/my" @click="sidebarOpen = false"
          >My Tickets</RouterLink
        >
        <RouterLink to="/tickets/all" @click="sidebarOpen = false"
          >All Tickets</RouterLink
        >
        <RouterLink to="/dashboard" @click="sidebarOpen = false"
          >Dashboard</RouterLink
        >
        <p>Management</p>
        <RouterLink to="/users" @click="sidebarOpen = false">Users</RouterLink>
        <RouterLink to="/profile" @click="sidebarOpen = false"
          >Profile & Settings</RouterLink
        >
      </nav>
    </aside>

    <main class="main">
      <header class="topbar">
        <button class="menu-btn" @click="sidebarOpen = !sidebarOpen">
          Menu
        </button>
        <div>
          <strong>{{ pageTitle }}</strong>
          <span>{{ pageSubtitle }}</span>
        </div>
      </header>
      <RouterView @title="setTitle" />
    </main>
  </div>
</template>

<script setup>
import { ref } from "vue";

const sidebarOpen = ref(false);
const pageTitle = ref("My Tickets");
const pageSubtitle = ref("Simple view for support work");
const logoSrc = "/assets/helpdesk/unity_desk/favicon.svg";

function setTitle(title, subtitle = "Simple view for support work") {
  pageTitle.value = title;
  pageSubtitle.value = subtitle;
}
</script>
