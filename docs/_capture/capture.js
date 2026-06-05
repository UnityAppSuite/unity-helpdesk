// Automated screenshot + feature-recording capture for the Unity Helpdesk app.
// Drives the real Unity Helpdesk SPA (Vue, served at /unity-helpdesk/) with
// Playwright, against the clean demo data seeded by scripts/seed_demo.py.
//   node capture.js
// Outputs PNG screenshots to ../assets/ and per-feature .webm to ./_videos/
// (convert.sh then turns the .webm files into GIFs).
//
// unity.local is mapped to 127.0.0.1 via Chromium's --host-resolver-rules, so
// no /etc/hosts entry is required. The site is reached with the correct Host
// header that way.

const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

// demo_meta.json is written by scripts/seed_demo.py on each reseed.
let META = {};
try {
  META = JSON.parse(fs.readFileSync(path.join(__dirname, "demo_meta.json"), "utf8"));
} catch {
  console.log("! demo_meta.json not found — run scripts/seed_demo.py first");
}

const HOST = process.env.HD_HOST || "unity.local";
const PORT = process.env.HD_PORT || "8000";
const BASE = `http://${HOST}:${PORT}`;
const APP = `${BASE}/unity-helpdesk`;
const USER = process.env.HD_USER || META.capture_user || "capture-agent@unity-demo.example.com";
const PASS = process.env.HD_PASS || META.capture_pass || "Capture@2026xyz";
const HERO = process.env.HD_HERO || META.hero_ticket || ""; // hero ticket (student context + reply)
const HERO_SUBJECT = "Fee receipt not received for April"; // stable fallback to find the hero
const ALL_TYPE = META.all_tickets_type || "Fees & Payments"; // demo-only type -> isolates All Tickets
const AGENT_LABEL = META.agent_label || "Helpdesk Demo"; // dashboard agent-filter option text
const AGENT_VALUE = META.capture_user || "capture-agent@unity-demo.example.com"; // agent option value (= email)
// Search demo terms (all resolve to demo-only tickets, so no real PII surfaces):
const SEARCH_REF = META.search_ref || "WS-DEMO-1001"; // student reference number
const SEARCH_GUARDIAN = META.search_guardian || "priya.sharma@unity-demo.example.com"; // family-aware guardian email
const SEARCH_BODY = META.search_body || "WALDEMO7788"; // a token that exists only in an email body

const ASSETS = path.resolve(__dirname, "../assets");
const VIDEOS = path.resolve(__dirname, "_videos");
const VIEWPORT = { width: 1440, height: 900 };

fs.mkdirSync(ASSETS, { recursive: true });
fs.mkdirSync(VIDEOS, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function login(browser) {
  const ctx = await browser.newContext({ viewport: VIEWPORT });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("#login_email", USER);
  await page.fill("#login_password", PASS);
  await page.click(".btn-login");
  await page.waitForTimeout(3000);
  const state = await ctx.storageState();
  await ctx.close();
  return state;
}

async function newPage(browser, state, record) {
  const opts = { viewport: VIEWPORT, storageState: state };
  if (record) opts.recordVideo = { dir: VIDEOS, size: VIEWPORT };
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  return { ctx, page };
}

async function gotoApp(page, sub) {
  await page.goto(`${APP}${sub}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1800);
}

// Open the Dashboard via in-app navigation rather than a full page load. The
// agent-filter dropdown is only populated when the session capabilities are
// already present; a fresh `goto /dashboard` races that load and leaves the
// dropdown empty. Loading any list first (so the shared session populates) and
// then clicking the Dashboard nav link avoids the race.
async function openDashboard(page) {
  await gotoApp(page, "/tickets/my");
  await page.waitForTimeout(2500);
  await page.getByRole("link", { name: "Dashboard" }).click();
  await page.waitForSelector(".donut-chart, .metric", { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

// Open the ticket list and wait for the summary cards / table to render.
async function openList(page, view) {
  await gotoApp(page, `/tickets/${view}`);
  await page.waitForSelector(".ticket-table, .skeleton-row", { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(1500);
}

// Optional task filter: HD_ONLY="dashboard,feature-dashboard" runs a subset.
const ONLY = (process.env.HD_ONLY || "").split(",").map((s) => s.trim()).filter(Boolean);
const want = (name) => ONLY.length === 0 || ONLY.includes(name);

// ---- Screenshots -----------------------------------------------------------
async function shot(browser, state, name, fn) {
  if (!want(name)) return;
  const { ctx, page } = await newPage(browser, state, false);
  try {
    await fn(page);
    await page.screenshot({ path: path.join(ASSETS, `${name}.png`), fullPage: true });
    console.log(`shot: ${name}.png`);
  } catch (e) {
    console.log(`! shot ${name}: ${e.message.split("\n")[0]}`);
  }
  await ctx.close();
}

// ---- Recordings ------------------------------------------------------------
async function record(browser, state, name, fn) {
  if (!want(name)) return;
  const { ctx, page } = await newPage(browser, state, true);
  try {
    await fn(page);
  } catch (e) {
    console.log(`  ! ${name} error: ${e.message.split("\n")[0]}`);
  }
  await page.waitForTimeout(600);
  const video = page.video();
  await ctx.close();
  if (video) {
    const src = await video.path();
    const dst = path.join(VIDEOS, `${name}.webm`);
    try {
      fs.renameSync(src, dst);
    } catch {
      fs.copyFileSync(src, dst);
    }
    console.log(`rec : ${name}.webm`);
  }
}

// Restrict the All Tickets list to the demo agent's tickets. We filter by
// ASSIGNEE rather than ticket-type because the assignee filter resolves via the
// indexed ToDo table (instant), whereas a ticket_type filter triggers a ~50s
// full scan over the 90k-row table. This keeps the All Tickets view clean
// (demo data only — no real PII) and fast.
async function filterByAgent(page, agentValue) {
  const sel = page.locator("select").filter({ has: page.locator('option:has-text("Assigned: All")') }).first();
  if (await sel.count()) {
    await sel.selectOption({ value: agentValue }).catch(async () => {
      await sel.selectOption({ label: AGENT_LABEL }).catch(() => {});
    });
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(2500);
  }
}

// Type a term into the ticket-list search box and apply it (Enter). We start
// from My Tickets so the pre-search list is already demo-only; the search
// itself reaches across all tickets but every demo term resolves to demo
// records only, so the filtered result is clean.
async function searchTickets(page, term) {
  await openList(page, "my");
  const box = page.locator("input.search").first();
  await box.click();
  await box.fill(term);
  await page.waitForTimeout(900); // let the live suggestions appear
  await box.press("Enter");
  await page.waitForLoadState("networkidle").catch(() => {});
  // wait for the "Searching…" spinner and the "previous request" banner to clear
  await page.locator(".search-loading").waitFor({ state: "detached", timeout: 10000 }).catch(() => {});
  await page
    .locator("text=results below are from the previous request")
    .waitFor({ state: "detached", timeout: 10000 })
    .catch(() => {});
  await page.waitForTimeout(2000);
}

// Recording variant of the search: shows the clean demo list, types the term
// character-by-character so the filtering is visible on camera, then holds the
// settled result. All demo terms resolve to demo-only tickets (no real PII).
async function recordSearch(page, term) {
  await openList(page, "my");
  await sleep(2500); // hold the clean demo-only list (start of kept window)
  const box = page.locator("input.search").first();
  await box.click();
  await box.type(term, { delay: 80 }); // visible typing
  await sleep(700);
  await box.press("Enter");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.locator(".search-loading").waitFor({ state: "detached", timeout: 10000 }).catch(() => {});
  await page
    .locator("text=results below are from the previous request")
    .waitFor({ state: "detached", timeout: 10000 })
    .catch(() => {});
  await sleep(4500); // hold the settled, filtered result
}

async function openHero(page) {
  if (HERO) {
    await gotoApp(page, `/tickets/${HERO}`);
  } else {
    // fall back: find the hero by its stable subject from My Tickets
    await openList(page, "my");
    await page.locator(".ticket-table tbody tr", { hasText: HERO_SUBJECT }).first().click().catch(() => {});
    await page.waitForTimeout(1500);
  }
  await page.waitForSelector(".compose-tabs, .detail-section, .student-context-banner", { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

async function expandStudentDetails(page) {
  const banner = page.locator(".student-context-banner, .student-context-table").first();
  const h = page.getByRole("heading", { name: "Student Details" }).first();
  if (!(await h.count())) return;
  // Toggle only if the context content isn't already visible; verify it opens.
  for (let i = 0; i < 3; i++) {
    if (await banner.isVisible().catch(() => false)) break;
    await h.click().catch(() => {});
    await page.waitForTimeout(1200);
  }
  await banner.waitFor({ state: "visible", timeout: 6000 }).catch(() => {});
  await page.waitForTimeout(800);
}

// ============================================================================
(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ["--host-resolver-rules=MAP unity.local 127.0.0.1", "--no-sandbox"],
  });
  const state = await login(browser);
  console.log("logged in, capturing...");

  // ---- screenshots ----
  await shot(browser, state, "my-tickets", async (page) => {
    await openList(page, "my");
  });
  await shot(browser, state, "all-tickets", async (page) => {
    await openList(page, "all");
    await filterByAgent(page, AGENT_VALUE);
  });
  await shot(browser, state, "ticket-student-context", async (page) => {
    await openHero(page);
    await expandStudentDetails(page);
  });
  await shot(browser, state, "dashboard", async (page) => {
    await openDashboard(page);
    await selectAgent(page); // filter to demo agent -> demo data only
    await page.waitForTimeout(2000);
  });
  await shot(browser, state, "settings", async (page) => {
    await gotoApp(page, "/settings");
    await page.waitForTimeout(2500);
  });
  await shot(browser, state, "bulk-email", async (page) => {
    // open from My Tickets so the background list behind the modal is demo-only
    await openList(page, "my");
    await page.getByRole("button", { name: "Bulk Email" }).click();
    await page.waitForTimeout(1500);
    await fillBulkEmail(page);
    await page.waitForTimeout(800);
  });
  await shot(browser, state, "search-ref", async (page) => {
    await searchTickets(page, SEARCH_REF);
  });
  await shot(browser, state, "search-guardian", async (page) => {
    await searchTickets(page, SEARCH_GUARDIAN);
  });
  await shot(browser, state, "search-body", async (page) => {
    await searchTickets(page, SEARCH_BODY);
  });

  // ---- recordings ----
  await record(browser, state, "feature-my-tickets", async (page) => {
    await openList(page, "my");
    await page.mouse.wheel(0, 350);
    await sleep(2500);
    await page.mouse.wheel(0, 350);
    await sleep(3000);
  });

  await record(browser, state, "feature-all-tickets", async (page) => {
    await openList(page, "all");
    await sleep(1000);
    await filterByAgent(page, AGENT_VALUE);
    // hold the filtered (demo-only) view well past convert.sh's 9s trim window
    await sleep(10000);
  });

  await record(browser, state, "feature-student-context", async (page) => {
    await openHero(page);
    await sleep(1500);
    await expandStudentDetails(page);
    await page.mouse.wheel(0, 300);
    await sleep(3500);
  });

  await record(browser, state, "feature-reply", async (page) => {
    await openHero(page);
    await sleep(1500);
    await composeReply(page, "Hello Mrs. Sharma, your April fee receipt is attached. Apologies for the delay — please let us know if you need anything else.");
    await sleep(3500);
  });

  await record(browser, state, "feature-bulk-email", async (page) => {
    await openList(page, "my"); // clean demo-only background behind the modal
    await page.getByRole("button", { name: "Bulk Email" }).click();
    await sleep(1500);
    await fillBulkEmail(page);
    await sleep(3500);
  });

  await record(browser, state, "feature-dashboard", async (page) => {
    await openDashboard(page);
    await sleep(1000);
    await selectAgent(page);
    // hold the agent-filtered (demo-only) dashboard past the 9s trim window
    await sleep(10000);
  });

  await record(browser, state, "feature-settings", async (page) => {
    await gotoApp(page, "/settings");
    await sleep(2500);
    await page.mouse.wheel(0, 400);
    await sleep(3500);
  });

  // Search demos: briefly show the clean (demo-only) list, type the term
  // visibly, then let it filter and hold the settled result. Sequenced so the
  // kept (last-9s) window shows typing -> filtering -> settled result.
  await record(browser, state, "feature-search-ref", (page) => recordSearch(page, SEARCH_REF));
  await record(browser, state, "feature-search-guardian", (page) => recordSearch(page, SEARCH_GUARDIAN));
  await record(browser, state, "feature-search-body", (page) => recordSearch(page, SEARCH_BODY));

  await browser.close();
  console.log("DONE");
})();

// ---- interaction helpers used by both shots and recordings -----------------
async function fillBulkEmail(page) {
  const subj = page.locator('input[placeholder="Email subject"]').first();
  if (await subj.count()) await subj.fill("Fee payment reminder — Term 2");
  // Ticket Type (required) <select>
  const tsel = page.locator(".bulk-modal select, .modal-backdrop select").first();
  if (await tsel.count()) await tsel.selectOption({ label: "Fees & Payments" }).catch(() => {});
  // BCC recipients
  const bcc = page.locator('input[placeholder="Type student name or email…"]').first();
  if (await bcc.count()) {
    await bcc.click();
    await bcc.fill("priya.sharma@unity-demo.example.com");
    await bcc.press("Enter");
    await page.waitForTimeout(400);
    await bcc.fill("rahul.mehta@unity-demo.example.com");
    await bcc.press("Enter");
    await page.waitForTimeout(400);
  }
  // Include guardian emails
  const ig = page.locator('input[type="checkbox"]').first();
  if (await ig.count()) await ig.check().catch(() => {});
  // message body
  const msg = page.locator(".modal-backdrop textarea, .bulk-modal textarea").first();
  if (await msg.count()) await msg.fill("Dear Parents,\n\nThis is a gentle reminder that Term 2 fees are due shortly. Kindly complete the payment at your convenience.\n\nWarm regards,\nWalnut School Helpdesk");
  // NOTE: we intentionally do not click Send — avoids enqueuing real email.
}

async function composeReply(page, text) {
  // pick the Reply compose tab
  const replyTab = page.locator(".compose-tab", { hasText: "Reply" }).first();
  if (await replyTab.count()) {
    await replyTab.click();
    await page.waitForTimeout(800);
  }
  // TinyMCE renders into an iframe; fall back to any contenteditable/textarea
  const frame = page.frameLocator("iframe.tox-edit-area__iframe, .editor-toolbar-tinymce iframe").first();
  const body = frame.locator("body[contenteditable], body").first();
  if (await body.count().catch(() => 0)) {
    await body.click().catch(() => {});
    await body.type(text, { delay: 12 }).catch(async () => {
      await page.keyboard.type(text, { delay: 12 });
    });
  } else {
    const ta = page.locator(".compose-tabs ~ * textarea, .editor-toolbar-tinymce ~ * textarea").first();
    if (await ta.count()) await ta.fill(text);
  }
  await page.waitForTimeout(800);
  // NOTE: leave the Send Reply button visible but do not click (no real email).
}

async function selectAgent(page) {
  const sel = page.locator(".agent-filter select").first();
  if (!(await sel.count())) return;
  // options load async; wait for the demo agent's option to be present
  await sel.locator(`option[value="${AGENT_VALUE}"]`).waitFor({ state: "attached", timeout: 8000 }).catch(() => {});
  await sel.selectOption({ value: AGENT_VALUE }).catch(async () => {
    await sel.selectOption({ label: AGENT_LABEL }).catch(() => {});
  });
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(2500);
}
