import DOMPurify from "dompurify";

// Shown to (non-technical) users when the server fails without a human-readable
// message. Never expose raw method paths or tracebacks in the UI.
const GENERIC_ERROR =
  "Something went wrong. Please try again, and contact support if it keeps happening.";

export function sanitize(html) {
  return DOMPurify.sanitize(html || "", {
    ADD_ATTR: ["target", "rel"],
    FORBID_TAGS: ["script", "style", "iframe"],
  });
}

// Thrown by call() after kicking off a login redirect, so callers can skip
// rendering error UI for the millisecond before window.location takes effect.
export class AuthRedirectError extends Error {
  constructor() {
    super("Session expired, redirecting to login.");
    this.name = "AuthRedirectError";
    this.code = "AUTH_REDIRECT";
  }
}

let _redirectInFlight = false;

export function redirectToLogin() {
  if (_redirectInFlight) return;
  if (typeof window === "undefined") return;
  const path = window.location.pathname || "/";
  if (path.startsWith("/login")) return;
  _redirectInFlight = true;
  const target = window.location.pathname + (window.location.search || "");
  window.location.href = "/login?redirect-to=" + encodeURIComponent(target);
}

// Match Frappe's many shapes of "you are not authenticated / authorised".
// Trigger only on signals strong enough to mean a guest / expired session —
// not on every PermissionError (which can also mean "role missing for THIS doc").
const AUTH_MESSAGE_RE =
  /not permitted|not whitelisted|login to access|guest cannot access|authentication failed|session expired|please login|please log in/i;

function _isAuthSignal(payload, status) {
  if (status === 401) return true;
  if (!payload) return false;
  const excType = payload.exc_type || "";
  if (excType === "AuthenticationError" || excType === "SessionExpired") {
    return true;
  }
  const msg = extractError(payload) || "";
  if (msg && AUTH_MESSAGE_RE.test(msg)) return true;
  // payload.exc is a JSON-encoded list of tracebacks; check its head for the
  // same auth markers (server stuffs the exception string in there too).
  if (typeof payload.exc === "string" && AUTH_MESSAGE_RE.test(payload.exc)) {
    return true;
  }
  return false;
}

async function _refreshCsrfToken() {
  try {
    const r = await fetch(
      "/api/method/helpdesk.api.unity_helpdesk.get_csrf_token",
      { method: "GET", credentials: "same-origin", cache: "no-store" }
    );
    const data = await r.json().catch(() => null);
    if (data?.message) {
      window.csrf_token = data.message;
      return true;
    }
  } catch (err) {
    console.warn("[unity-helpdesk] CSRF token refresh failed:", err);
  }
  return false;
}

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
      // CSRF token expired — fetch a fresh one and retry exactly once
      if (response.status === 403 && payload?.exc_type === "CSRFTokenError") {
        const refreshed = await _refreshCsrfToken();
        if (refreshed) {
          const retry = await fetch(`/api/method/${method}`, {
            method: "POST",
            cache: "no-store",
            headers: {
              "Content-Type": "application/json",
              "X-Frappe-CSRF-Token": window.csrf_token || "",
            },
            body: JSON.stringify(params),
            credentials: "same-origin",
          });
          const retryPayload = await retry.json().catch(() => ({}));
          if (!retry.ok || retryPayload.exc || retryPayload._server_messages) {
            if (_isAuthSignal(retryPayload, retry.status)) {
              redirectToLogin();
              throw new AuthRedirectError();
            }
            const message = extractError(retryPayload) || GENERIC_ERROR;
            const err = new Error(message);
            err.status = retry.status;
            err.payload = retryPayload;
            throw err;
          }
          return retryPayload.message;
        }
        // CSRF refresh failed — assume session is gone, go to login
        redirectToLogin();
        throw new AuthRedirectError();
      }
      if (_isAuthSignal(payload, response.status)) {
        redirectToLogin();
        throw new AuthRedirectError();
      }
      const message = extractError(payload) || GENERIC_ERROR;
      const err = new Error(message);
      err.status = response.status;
      err.payload = payload;
      throw err;
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
    if (error?.name === "TypeError") {
      error.code = "NETWORK_ERROR";
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

// Retry transient network/5xx failures up to 3 attempts with backoff (1s, 3s, 7s).
// Application errors (PermissionError, ValidationError, etc — 4xx + payload.exc
// from a 200 response) surface immediately. options.onAttempt(n) is invoked
// before each retry so views can show a "Reloading…" indicator.
//
// SAFETY: retries can cause duplicate side-effects on POSTs that create or mutate
// state (e.g. create_ticket, bulk_send_email). Callers MUST opt in by passing
// { idempotent: true } — without that flag this behaves exactly like call().
export async function callWithRetry(method, params = {}, options = {}) {
  if (!options.idempotent) {
    return call(method, params, options);
  }
  const delays = options.delays || [1000, 3000, 7000];
  const noop = () => undefined;
  const onAttempt = options.onAttempt || noop;
  let lastError;
  for (let attempt = 0; attempt <= delays.length; attempt += 1) {
    if (attempt > 0) onAttempt(attempt);
    try {
      return await call(method, params, options);
    } catch (error) {
      lastError = error;
      if (!isRetriable(error) || attempt === delays.length) {
        throw error;
      }
      await new Promise((resolve) =>
        window.setTimeout(resolve, delays[attempt])
      );
    }
  }
  throw lastError;
}

function isRetriable(error) {
  if (!error) return false;
  if (error.code === "NETWORK_ERROR") return true;
  if (typeof error.status === "number" && error.status >= 500) return true;
  // AbortError from a timeout is user-visible; don't loop on it.
  return false;
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
    if (_isAuthSignal(payload, response.status)) {
      redirectToLogin();
      throw new AuthRedirectError();
    }
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

export async function getAgentCandidates(search = "") {
  // Searched server-side across every enabled System User — passing no `search`
  // returns a short default browse list, not the whole table.
  return (
    (await call("helpdesk.api.unity_helpdesk.get_agent_candidates", {
      search,
    })) || []
  );
}

export async function createAgent(user) {
  return call("helpdesk.api.unity_helpdesk.create_agent", { user });
}

export async function createTicketType(params) {
  return call("helpdesk.api.unity_helpdesk.create_ticket_type", params);
}

export async function listTicketTypesWithKeywords() {
  return (
    (await call(
      "helpdesk.api.unity_helpdesk.list_ticket_types_with_keywords"
    )) || []
  );
}

export async function updateTicketTypeKeywords(name, keywords) {
  return call("helpdesk.api.unity_helpdesk.update_ticket_type_keywords", {
    name,
    keywords,
  });
}

export async function updateTicketTypeColor(name, color) {
  return call("helpdesk.api.unity_helpdesk.update_ticket_type_color", {
    name,
    color: color || "",
  });
}

// --- Team Settings (admin only; all three gate on can_manage_unity_settings) ---

export async function listTeams() {
  return (await call("helpdesk.api.unity_helpdesk.list_teams")) || [];
}

export async function updateTeamColor(name, color) {
  return call("helpdesk.api.unity_helpdesk.update_team_color", {
    name,
    color: color || "",
  });
}

// Replaces the whole member list. NOT cosmetic — membership drives which team
// gets stamped on that person's next assignment, and what they can see if the
// team visibility restriction is switched on.
export async function updateTeamMembers(name, users) {
  return call("helpdesk.api.unity_helpdesk.update_team_members", {
    name,
    users: users || [],
  });
}

export async function updateUnitySettings(params) {
  return call("helpdesk.api.unity_helpdesk.update_unity_settings", params);
}

// --- Reply templates (HD Canned Response) admin CRUD ---

export async function listReplyTemplateCategoriesAdmin() {
  return call("helpdesk.api.reply_templates.get_reply_template_categories", {
    include_inactive: 1,
  });
}

export async function listReplyTemplatesAdmin(params = {}) {
  return call("helpdesk.api.reply_templates.list_reply_templates", {
    limit: 200,
    include_inactive: 1,
    ...params,
  });
}

export async function createReplyTemplateCategory(params) {
  return call(
    "helpdesk.api.reply_templates.create_reply_template_category",
    params
  );
}

export async function updateReplyTemplateCategory(params) {
  return call(
    "helpdesk.api.reply_templates.update_reply_template_category",
    params
  );
}

export async function deleteReplyTemplateCategory(name) {
  return call("helpdesk.api.reply_templates.delete_reply_template_category", {
    name,
  });
}

export async function createReplyTemplate(params) {
  return call("helpdesk.api.reply_templates.create_reply_template", params);
}

export async function updateReplyTemplate(params) {
  return call("helpdesk.api.reply_templates.update_reply_template", params);
}

export async function deleteReplyTemplate(name) {
  return call("helpdesk.api.reply_templates.delete_reply_template", { name });
}

export async function bulkUpdateTickets(names, field, value) {
  return call("helpdesk.api.unity_helpdesk.bulk_update_tickets", {
    names,
    field,
    value: value ?? "",
  });
}

export async function listAgentGroups() {
  // A Unity endpoint rather than a generic frappe.client.get_list: it decides
  // whether to include `custom_color` based on whether the column exists. If
  // the CLIENT named that field, a site that hasn't run the schema patch would
  // get "Unknown column" and lose the Agent Group filter and bulk-edit dialog
  // along with the colour.
  return (await call("helpdesk.api.unity_helpdesk.get_agent_groups")) || [];
}

// Priorities for the generic filter popover. Read live rather than hardcoded so
// a priority added in Desk shows up without a rebuild.
export async function listTicketPriorities() {
  const rows = await call("frappe.client.get_list", {
    doctype: "HD Ticket Priority",
    fields: ["name"],
    page_length: 50,
    order_by: "integer_value asc",
  });
  return rows || [];
}

// Fetch a single template with its full body (the list endpoint only returns a preview)
export async function getReplyTemplateDoc(name) {
  return call("frappe.client.get", {
    doctype: "HD Canned Response",
    name,
  });
}

export async function searchUsers(query) {
  if (!query || query.length < 2) return [];
  // Access-gated, bounded, injection-safe server endpoint (prefix-ranked).
  const rows = await call("helpdesk.api.unity_helpdesk.search_users", {
    query,
  });
  return rows || [];
}
