export async function call(method, params = {}) {
  const response = await fetch(`/api/method/${method}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-Frappe-CSRF-Token": window.csrf_token || "",
    },
    body: JSON.stringify(params),
    credentials: "same-origin",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.exc || payload._server_messages) {
    const message = extractError(payload) || `Request failed: ${method}`;
    throw new Error(message);
  }
  return payload.message;
}

export function extractError(payload) {
  if (!payload) return "";
  if (payload.message && typeof payload.message === "string")
    return payload.message;
  if (payload._server_messages) {
    try {
      const messages = JSON.parse(payload._server_messages);
      return messages.map((msg) => JSON.parse(msg).message).join("\n");
    } catch {
      return payload._server_messages;
    }
  }
  return "";
}

export function stripHtml(value = "") {
  const el = document.createElement("div");
  el.innerHTML = value || "";
  return el.textContent || el.innerText || "";
}

export function initials(name = "") {
  const parts = name.split(/[ .@_-]+/).filter(Boolean);
  return (parts[0]?.[0] || "U") + (parts[1]?.[0] || "");
}

export function formatDate(value) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

export function formatDateTime(value) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function priorityTarget(priority) {
  return (
    { High: "Same day", Medium: "1-2 days", Low: "2-3 days" }[priority] || ""
  );
}

export function openDeskPath(path) {
  try {
    if (window.top && window.top !== window) {
      window.top.location.assign(path);
      return;
    }
  } catch {
    // Fall back to current window navigation.
  }
  window.location.assign(path);
}

// ---------------------------------------------------------------------------
// Shared lookup helpers — use Frappe built-in APIs directly so they work
// regardless of whether helpdesk.api.unity module is loaded in memory.
// ---------------------------------------------------------------------------

export async function getAgents() {
  const rows = await call("frappe.client.get_list", {
    doctype: "HD Agent",
    fields: ["name", "agent_name", "user_image", "is_active"],
    order_by: "agent_name asc",
    page_length: 500,
  });
  return (rows || []).map((r) => ({
    name: r.name,
    full_name: r.agent_name || r.name,
    user_image: r.user_image || "",
    is_active: r.is_active,
  }));
}

export async function getTicketTypes() {
  const rows = await call("frappe.client.get_list", {
    doctype: "HD Ticket Type",
    fields: ["name"],
    order_by: "name asc",
    page_length: 500,
  });
  return rows || [];
}

export async function getSidebarProfile() {
  try {
    const username = await call("frappe.auth.get_logged_user");
    if (!username) return {};
    const rows = await call("frappe.client.get_list", {
      doctype: "User",
      fields: ["name", "full_name", "email", "user_image"],
      filters: { name: username },
      page_length: 1,
    });
    return rows?.[0] || {};
  } catch {
    return {};
  }
}

export async function searchUsers(query) {
  if (!query || query.length < 2) return [];
  const rows = await call("frappe.client.get_list", {
    doctype: "User",
    fields: ["name", "full_name", "email"],
    filters: [
      ["enabled", "=", 1],
      ["user_type", "=", "System User"],
    ],
    or_filters: [
      ["name", "like", `%${query}%`],
      ["full_name", "like", `%${query}%`],
    ],
    page_length: 10,
  });
  return rows || [];
}
