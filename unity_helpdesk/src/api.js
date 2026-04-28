export async function call(method, params = {}, options = {}) {
  const controller = new AbortController();
  const timeoutMs = Number(options.timeoutMs || 0);
  const upstreamSignal = options.signal;
  let timeoutId = null;
  let upstreamAbort = null;

  if (upstreamSignal) {
    if (upstreamSignal.aborted) {
      controller.abort(upstreamSignal.reason);
    } else {
      upstreamAbort = () => controller.abort(upstreamSignal.reason);
      upstreamSignal.addEventListener("abort", upstreamAbort, { once: true });
    }
  }

  if (timeoutMs > 0) {
    timeoutId = window.setTimeout(() => controller.abort("timeout"), timeoutMs);
  }

  try {
    const response = await fetch(`/api/method/${method}`, {
      method: "POST",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "X-Frappe-CSRF-Token": window.csrf_token || "",
      },
      body: JSON.stringify(params),
      credentials: "same-origin",
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.exc || payload._server_messages) {
      const message = extractError(payload) || `Request failed: ${method}`;
      throw new Error(message);
    }
    return payload.message;
  } catch (error) {
    if (error?.name === "AbortError") {
      const isTimeout = controller.signal.reason === "timeout";
      const wrapped = new Error(
        isTimeout
          ? "No tickets found within 60 seconds."
          : "Request was cancelled."
      );
      wrapped.code = isTimeout ? "REQUEST_TIMEOUT" : "REQUEST_ABORTED";
      throw wrapped;
    }
    throw error;
  } finally {
    if (timeoutId) {
      window.clearTimeout(timeoutId);
    }
    if (upstreamSignal && upstreamAbort) {
      upstreamSignal.removeEventListener("abort", upstreamAbort);
    }
  }
}

export async function uploadAttachment(file, doctype, docname) {
  const formData = new FormData();
  formData.append("file", file);
  if (doctype) {
    formData.append("doctype", doctype);
  }
  if (docname) {
    formData.append("docname", docname);
  }
  formData.append("is_private", "1");

  const response = await fetch("/api/method/upload_file", {
    method: "POST",
    body: formData,
    headers: {
      "X-Frappe-CSRF-Token": window.csrf_token || "",
    },
    credentials: "same-origin",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.exc || payload._server_messages) {
    const message = extractError(payload) || "Attachment upload failed";
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

export async function getUnityProfile() {
  return call("helpdesk.api.unity_helpdesk.get_profile");
}

export async function getAgents() {
  return (await call("helpdesk.api.unity_helpdesk.get_agents")) || [];
}

export async function getTicketTypes() {
  return (await call("helpdesk.api.unity_helpdesk.get_ticket_types")) || [];
}

export async function getSidebarProfile() {
  try {
    return (await getUnityProfile()) || {};
  } catch {
    return {};
  }
}

export async function getAgentCandidates() {
  return (await call("helpdesk.api.unity_helpdesk.get_agent_candidates")) || [];
}

export async function createAgent(user) {
  return call("helpdesk.api.unity_helpdesk.create_agent", { user });
}

export async function createTicketType(params) {
  return call("helpdesk.api.unity_helpdesk.create_ticket_type", params);
}

export async function updateUnitySettings(params) {
  return call("helpdesk.api.unity_helpdesk.update_unity_settings", params);
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
