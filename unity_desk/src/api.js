export async function call(method, params = {}) {
  const response = await fetch(`/api/method/${method}`, {
    method: "POST",
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
