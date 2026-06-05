import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import "./style.css";

const routes = [
  { path: "/", redirect: "/tickets/my" },
  { path: "/dashboard", component: () => import("./views/DashboardView.vue") },
  { path: "/tickets", redirect: "/tickets/my" },
  {
    path: "/tickets/my",
    component: () => import("./views/TicketsView.vue"),
    props: { view: "my" },
  },
  {
    path: "/tickets/all",
    component: () => import("./views/TicketsView.vue"),
    props: { view: "all" },
  },
  {
    path: "/tickets/:ticketId",
    component: () => import("./views/TicketDetailView.vue"),
    props: true,
  },
  { path: "/agents", redirect: "/settings" },
  { path: "/settings", component: () => import("./views/ProfileView.vue") },
];

const router = createRouter({
  history: createWebHistory("/unity-helpdesk/"),
  routes,
});

createApp(App).use(router).mount("#app");
