<template>
  <div class="app-shell" :class="threadLayoutClass">
    <transition name="global-notice-fade">
      <div
        v-if="globalNotice"
        class="global-notice"
        :class="`global-notice--${globalNotice.type}`"
        role="status"
        @click="dismissGlobalNotice"
      >
        {{ globalNotice.text }}
      </div>
    </transition>
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
        <RouterLink
          v-if="canViewMyTickets"
          to="/tickets/my"
          @click="sidebarOpen = false"
        >
          My Tickets
        </RouterLink>
        <RouterLink
          v-if="canViewAllTickets"
          to="/tickets/all"
          @click="sidebarOpen = false"
        >
          All Tickets
        </RouterLink>
        <RouterLink
          v-if="canViewMyTickets"
          to="/dashboard"
          @click="sidebarOpen = false"
        >
          Dashboard
        </RouterLink>
        <template v-if="canManageUnitySettings">
          <p>Management</p>
          <RouterLink
            v-if="canManageUnitySettings"
            to="/settings"
            @click="sidebarOpen = false"
          >
            Settings
          </RouterLink>
        </template>
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
          <button
            v-if="!route.params.ticketId"
            class="btn"
            @click="openComposer = true"
          >
            New Ticket
          </button>
          <button
            v-if="canViewAllTickets && !route.params.ticketId"
            class="btn secondary"
            @click="openBulkEmailModal"
          >
            Bulk Email
          </button>

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
              <RouterLink
                v-if="canManageUnitySettings"
                class="profile-dropdown-item"
                to="/settings"
                @click="profileMenuOpen = false"
              >
                Settings
              </RouterLink>
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
          <p v-else-if="composerWarning" class="warning-banner">
            {{ composerWarning }}
          </p>

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
            Subject <span class="required-asterisk">*</span>
            <input
              v-model="composer.subject"
              type="text"
              placeholder="Enter ticket subject"
              required
            />
          </label>
          <label>
            Ticket Type <span class="required-asterisk">*</span>
            <select v-model="composer.ticket_type" required>
              <option value="" disabled>Select ticket type…</option>
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
            <div class="assignee-combobox">
              <input
                v-model="assigneeQuery"
                type="text"
                :placeholder="
                  composer.assignee ? '' : 'Unassigned — search agent…'
                "
                autocomplete="off"
                @input="onAssigneeInput"
                @focus="onAssigneeFocus"
                @blur="onAssigneeBlur"
              />
              <ul v-if="assigneeOpen" class="user-suggestions">
                <li @mousedown.prevent="clearAssignee">
                  <span class="muted">Unassigned</span>
                </li>
                <li
                  v-for="agent in assigneeMatches"
                  :key="agent.name"
                  @mousedown.prevent="pickAssignee(agent)"
                >
                  <span
                    class="avatar"
                    style="width: 20px; height: 20px; font-size: 9px"
                  >
                    {{ initials(agent.full_name || agent.name) }}
                  </span>
                  <span>{{ agent.full_name || agent.name }}</span>
                  <small>{{ agent.email || agent.name }}</small>
                </li>
                <li v-if="!assigneeMatches.length" class="disabled">
                  <small class="muted">No agents match</small>
                </li>
              </ul>
            </div>
          </label>
          <label>
            Email Message
            <TinyMceEditor
              v-model="composer.message"
              :min-height="260"
              placeholder="Write the email that should be sent to the customer"
              :enable-email-template="true"
              :enable-attach="true"
              @attach="composerAttachmentInput?.click()"
              @template-subject="applyTemplateSubjectToComposer"
              @email-template-selected="applyEmailTemplateToCreateTicket"
            />
            <input
              ref="composerAttachmentInput"
              type="file"
              class="hidden-file-input"
              multiple
              @change="handleComposerAttachments"
            />
            <span v-if="composerUploading" class="muted">Uploading…</span>
            <div
              v-if="composer.attachments.length"
              class="attachment-list attachment-list-modal"
            >
              <div
                v-for="attachment in composer.attachments"
                :key="attachment.name"
                class="attachment-item"
              >
                <a
                  :href="attachment.file_url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ attachment.file_name || attachment.name }}
                </a>
                <button
                  type="button"
                  class="link-btn danger-link"
                  @click="removeComposerAttachment(attachment.name)"
                >
                  Remove
                </button>
              </div>
            </div>
          </label>

          <!-- Optional: send a test copy first, then verify before the real send -->
          <div class="test-mail-step">
            <label class="test-mail-toggle">
              <input v-model="composer.testEnabled" type="checkbox" />
              Send a test copy first
            </label>
            <div v-if="composer.testEnabled" class="test-mail-body">
              <div class="recipient-multiselect" @click="focusCreateTestInput">
                <span
                  v-for="r in composer.testRecipients"
                  :key="r.email"
                  class="recipient-chip"
                >
                  <span class="recipient-chip-label" :title="r.email">{{
                    r.name
                  }}</span>
                  <button
                    type="button"
                    class="recipient-chip-remove"
                    @click.stop="removeCreateTestRecipient(r.email)"
                  >
                    ×
                  </button>
                </span>
                <div class="recipient-input-wrap">
                  <input
                    ref="composerTestInputRef"
                    v-model="composerTestQuery"
                    type="text"
                    class="recipient-input"
                    placeholder="Add a user by name or email…"
                    autocomplete="off"
                    @input="onCreateTestEmailInput"
                    @paste="onCreateTestPaste"
                    @keydown.enter.prevent="addCreateTestFromInput"
                    @keydown="onCreateTestKeydown"
                    @focus="onCreateTestEmailInput"
                  />
                  <div
                    v-if="composerTestSuggestions.length"
                    class="recipient-dropdown"
                  >
                    <button
                      v-for="u in composerTestSuggestions"
                      :key="u.name"
                      type="button"
                      class="recipient-dropdown-item"
                      @mousedown.prevent="selectCreateTestUser(u)"
                    >
                      <span class="rd-name">{{ u.full_name || u.name }}</span>
                      <span class="rd-email">{{ u.email || u.name }}</span>
                    </button>
                  </div>
                </div>
              </div>
              <div class="composer-attachment-actions" style="margin-top: 6px">
                <button
                  type="button"
                  class="btn secondary"
                  :disabled="
                    composerTestSending || !composer.testRecipients.length
                  "
                  @click="sendCreateTestEmail"
                >
                  {{ composerTestSending ? "Sending…" : "Send Test" }}
                </button>
              </div>
              <p v-if="composer.testSent" class="muted test-mail-hint success">
                ✓ Test sent to
                {{ composer.testRecipients.map((r) => r.name).join(", ") }} —
                verify it, then Create &amp; Send. Editing the email re-requires
                a test.
              </p>
              <p v-else class="muted test-mail-hint">
                Adds one or more verifiers (chips). Send them a copy rendered
                like the customer's email; verify it, then the Create &amp; Send
                button unlocks.
              </p>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn secondary" @click="closeComposer">Cancel</button>
          <button class="btn" :disabled="composerSaving" @click="createTicket">
            {{ composerSaving ? "Sending..." : "Create & Send Email" }}
          </button>
        </div>
      </section>
    </div>

    <!-- Bulk Email modal -->
    <div
      v-if="openBulkEmail"
      class="modal-backdrop"
      @click.self="closeBulkEmail"
    >
      <section class="modal-card">
        <div class="modal-header">
          <div>
            <strong>Send Bulk Email</strong>
            <span
              >Send a personalised email to each student (and their guardians).
              One ticket is created per student.</span
            >
          </div>
          <button class="btn secondary" @click="closeBulkEmail">Close</button>
        </div>
        <div class="modal-body stack">
          <p v-if="bulkEmailError" class="error">{{ bulkEmailError }}</p>
          <p v-else-if="bulkEmailWarning" class="warning-banner">
            {{ bulkEmailWarning }}
          </p>

          <!-- Recipient input mode -->
          <div class="bulk-mode-toggle" role="tablist">
            <button
              type="button"
              class="bulk-mode-btn"
              :class="{ active: bulkEmail.mode === 'reference' }"
              role="tab"
              :aria-selected="bulkEmail.mode === 'reference'"
              @click="setBulkMode('reference')"
            >
              Enter reference numbers
            </button>
            <button
              type="button"
              class="bulk-mode-btn"
              :class="{ active: bulkEmail.mode === 'csv' }"
              role="tab"
              :aria-selected="bulkEmail.mode === 'csv'"
              @click="setBulkMode('csv')"
            >
              Import CSV
            </button>
          </div>

          <!-- Reference-number mode -->
          <template v-if="bulkEmail.mode === 'reference'">
            <label>
              Reference numbers / students
              <div class="recipient-multiselect" @click="focusBccInput">
                <span
                  v-for="s in bulkEmail.students"
                  :key="s.key"
                  class="recipient-chip"
                  :class="{
                    'recipient-chip-warn':
                      s.status === 'notfound' || s.status === 'noemail',
                  }"
                >
                  <span class="recipient-chip-label" :title="chipTitle(s)">{{
                    s.name
                  }}</span>
                  <button
                    type="button"
                    class="recipient-chip-remove"
                    @click.stop="removeStudent(s.key)"
                  >
                    ×
                  </button>
                </span>
                <div class="recipient-input-wrap">
                  <input
                    ref="bccInputRef"
                    v-model="bccSearchQuery"
                    type="text"
                    class="recipient-input"
                    placeholder="Type or paste reference numbers (comma-separated)…"
                    autocomplete="off"
                    @input="onBccSearch"
                    @paste="onBccPaste"
                    @keydown.enter.prevent="addStudentFromInput"
                    @keydown.backspace="onBccBackspace"
                    @keydown.escape="bccResults = []"
                    @focus="onBccSearch"
                  />
                  <div v-if="bccResults.length" class="recipient-dropdown">
                    <button
                      v-for="r in bccResults"
                      :key="r.email"
                      type="button"
                      class="recipient-dropdown-item"
                      @mousedown.prevent="selectStudent(r)"
                    >
                      <span class="rd-name">{{ r.name }}</span>
                      <span class="rd-email">{{ r.email }}</span>
                    </button>
                  </div>
                </div>
              </div>
              <div class="composer-attachment-actions" style="margin-top: 6px">
                <span v-if="bulkResolving" class="muted">resolving…</span>
                <span class="muted" style="margin-left: auto">
                  {{ bulkEmailStudentCount }} student{{
                    bulkEmailStudentCount === 1 ? "" : "s"
                  }}
                </span>
              </div>
            </label>
          </template>

          <!-- CSV mode -->
          <template v-else>
            <label>
              Import students from CSV
              <div class="composer-attachment-actions" style="margin-top: 6px">
                <button
                  type="button"
                  class="btn secondary"
                  :disabled="bulkEmailUploading"
                  @click="bulkEmailCsvInput?.click()"
                >
                  {{ bulkEmailUploading ? "Importing..." : "Import CSV" }}
                </button>
                <input
                  ref="bulkEmailCsvInput"
                  type="file"
                  accept=".csv,text/csv"
                  class="hidden-file-input"
                  @change="handleBulkEmailCsv"
                />
                <a
                  href="/api/method/helpdesk.api.unity_helpdesk_ext.get_bulk_email_sample_csv"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="btn secondary"
                >
                  Sample CSV
                </a>
                <span class="muted" style="margin-left: auto">
                  {{ bulkEmailStudentCount }} student{{
                    bulkEmailStudentCount === 1 ? "" : "s"
                  }}
                </span>
              </div>
              <div
                v-if="bulkEmail.students.length"
                class="bulk-email-chip-list"
              >
                <span
                  v-for="s in bulkEmail.students"
                  :key="s.key"
                  class="recipient-chip"
                  :class="{ 'recipient-chip-warn': s.status === 'noemail' }"
                >
                  <span class="recipient-chip-label" :title="chipTitle(s)">{{
                    s.name
                  }}</span>
                  <button
                    type="button"
                    class="recipient-chip-remove"
                    @click.stop="removeStudent(s.key)"
                  >
                    ×
                  </button>
                </span>
              </div>
            </label>
          </template>

          <!-- Recipient options (both modes): choose students and/or guardians -->
          <div v-if="bulkEmail.students.length" class="bulk-recipient-options">
            <label class="bulk-email-guardian-toggle">
              <input v-model="includeGuardians" type="checkbox" />
              Include guardian emails
              <span
                v-if="guardianCountLabel"
                class="muted"
                style="margin-left: 6px"
                >{{ guardianCountLabel }}</span
              >
            </label>
            <label class="bulk-email-guardian-toggle">
              <input v-model="excludeStudent" type="checkbox" />
              Exclude student email (send to guardians only)
            </label>
          </div>
          <label v-if="recipientsPreview">
            Recipients
            <textarea
              class="recipients-preview"
              :value="recipientsPreview"
              rows="4"
              readonly
            ></textarea>
          </label>

          <!-- Concise hint: any Student field works as a merge token. -->
          <div class="merge-fields-hint">
            <span class="muted">
              Tip: type
              <code class="merge-field-chip">{{
                mergeFieldToken("field_name")
              }}</code>
              in the subject or body to auto-fill any student detail (e.g.
              <code class="merge-field-chip">{{
                mergeFieldToken("first_name")
              }}</code
              >,
              <code class="merge-field-chip">{{
                mergeFieldToken("last_name")
              }}</code
              >). It's filled per student and left blank if the student has no
              value.
            </span>
          </div>
          <!-- What the composed email actually uses, with unknown-token warning -->
          <div v-if="templateTokens.length" class="merge-fields-hint">
            This email uses:
            <code
              v-for="t in templateTokens"
              :key="t.token"
              class="merge-field-chip"
              :class="{ 'merge-field-chip-bad': !t.recognised }"
              :title="
                t.recognised
                  ? 'Auto-filled per student'
                  : 'Not a known field — will be blank'
              "
              >{{ mergeFieldToken(t.token) }}</code
            >
            <span v-if="unknownTokens.length" class="bulk-token-warn"
              >⚠ {{ unknownTokens.join(", ") }} won't auto-fill — check the
              field name.</span
            >
          </div>

          <label>
            Subject
            <input
              v-model="bulkEmail.subject"
              type="text"
              placeholder="Email subject (auto-filled when you pick an Email Template)"
            />
          </label>
          <label>
            Ticket Type <span class="required-asterisk">*</span>
            <select v-model="bulkEmail.ticket_type" required>
              <option value="" disabled>Select ticket type…</option>
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
            CC (optional)
            <div class="recipient-multiselect" @click="focusCcInput">
              <span
                v-for="c in bulkEmail.cc"
                :key="c.email"
                class="recipient-chip"
              >
                <span class="recipient-chip-label" :title="c.email">{{
                  c.email
                }}</span>
                <button
                  type="button"
                  class="recipient-chip-remove"
                  @click.stop="removeCc(c.email)"
                >
                  ×
                </button>
              </span>
              <div class="recipient-input-wrap">
                <input
                  ref="ccInputRef"
                  v-model="ccInputQuery"
                  type="text"
                  class="recipient-input"
                  placeholder="cc1@example.com"
                  autocomplete="off"
                  @keydown.enter.prevent="addCcFromInput"
                  @keydown="onCcKeydown"
                />
              </div>
            </div>
          </label>
          <label>
            Message
            <TinyMceEditor
              v-model="bulkEmail.message"
              :min-height="240"
              placeholder="Compose the email message"
              :enable-email-template="true"
              :enable-attach="true"
              @attach="bulkEmailAttachmentInput?.click()"
              @template-subject="applyTemplateSubjectToBulkEmail"
              @email-template-selected="applyEmailTemplateToBulkEmail"
            />
            <input
              ref="bulkEmailAttachmentInput"
              type="file"
              class="hidden-file-input"
              multiple
              @change="handleBulkEmailAttachments"
            />
            <div
              v-if="bulkEmail.attachments.length"
              class="attachment-list attachment-list-modal"
            >
              <div
                v-for="attachment in bulkEmail.attachments"
                :key="attachment.name"
                class="attachment-item"
              >
                <a
                  :href="attachment.file_url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ attachment.file_name || attachment.name }}
                </a>
                <button
                  type="button"
                  class="link-btn danger-link"
                  @click="removeBulkEmailAttachment(attachment.name)"
                >
                  Remove
                </button>
              </div>
            </div>
          </label>

          <!-- Optional: send a test copy first, then verify before the real send -->
          <div class="test-mail-step">
            <label class="test-mail-toggle">
              <input v-model="bulkEmail.testEnabled" type="checkbox" />
              Send a test copy first
            </label>
            <div v-if="bulkEmail.testEnabled" class="test-mail-body">
              <div class="recipient-multiselect" @click="focusBulkTestInput">
                <span
                  v-for="r in bulkEmail.testRecipients"
                  :key="r.email"
                  class="recipient-chip"
                >
                  <span class="recipient-chip-label" :title="r.email">{{
                    r.name
                  }}</span>
                  <button
                    type="button"
                    class="recipient-chip-remove"
                    @click.stop="removeBulkTestRecipient(r.email)"
                  >
                    ×
                  </button>
                </span>
                <div class="recipient-input-wrap">
                  <input
                    ref="bulkTestInputRef"
                    v-model="bulkTestQuery"
                    type="text"
                    class="recipient-input"
                    placeholder="Add a user by name or email…"
                    autocomplete="off"
                    @input="onBulkTestEmailInput"
                    @paste="onBulkTestPaste"
                    @keydown.enter.prevent="addBulkTestFromInput"
                    @keydown="onBulkTestKeydown"
                    @focus="onBulkTestEmailInput"
                  />
                  <div
                    v-if="bulkTestSuggestions.length"
                    class="recipient-dropdown"
                  >
                    <button
                      v-for="u in bulkTestSuggestions"
                      :key="u.name"
                      type="button"
                      class="recipient-dropdown-item"
                      @mousedown.prevent="selectBulkTestUser(u)"
                    >
                      <span class="rd-name">{{ u.full_name || u.name }}</span>
                      <span class="rd-email">{{ u.email || u.name }}</span>
                    </button>
                  </div>
                </div>
              </div>
              <div class="composer-attachment-actions" style="margin-top: 6px">
                <button
                  type="button"
                  class="btn secondary"
                  :disabled="
                    bulkTestSending || !bulkEmail.testRecipients.length
                  "
                  @click="sendBulkTestEmail"
                >
                  {{ bulkTestSending ? "Sending…" : "Send Test" }}
                </button>
              </div>
              <p v-if="bulkEmail.testSent" class="muted test-mail-hint success">
                ✓ Test sent to
                {{ bulkEmail.testRecipients.map((r) => r.name).join(", ") }} —
                verify it, then Send to all. Editing the email or recipients
                re-requires a test.
              </p>
              <p v-else class="muted test-mail-hint">
                Adds one or more verifiers (chips). Send them a copy rendered
                like the first recipient's email; verify it, then the Send
                button unlocks.
              </p>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn secondary" @click="closeBulkEmail">Cancel</button>
          <button
            class="btn"
            :disabled="bulkEmailSending || !bulkEmailStudentCount"
            @click="sendBulkEmail"
          >
            {{
              bulkEmailSending
                ? "Sending..."
                : `Send to ${bulkEmailStudentCount} student${
                    bulkEmailStudentCount === 1 ? "" : "s"
                  }`
            }}
          </button>
        </div>
      </section>
    </div>

    <!-- Bulk send: live progress + honest result + failed-CSV export (BUG-2 / BUG-3) -->
    <div
      v-if="bulkProgressOpen"
      class="modal-backdrop"
      @click.self="maybeCloseBulkProgress"
    >
      <section class="modal-card bulk-progress-card">
        <div class="modal-header">
          <h2>
            {{
              bulkProgress.done ? "Bulk email finished" : "Sending bulk email…"
            }}
          </h2>
          <button
            v-if="bulkProgress.done"
            class="icon-btn"
            aria-label="Close"
            @click="closeBulkProgress"
          >
            ×
          </button>
        </div>
        <div class="modal-body stack">
          <p v-if="bulkProgress.subject" class="muted bulk-progress-subject">
            {{ bulkProgress.subject }}
          </p>
          <div class="bulk-progress-bar" role="progressbar">
            <div
              class="bulk-progress-fill"
              :class="{
                'has-errors': bulkProgress.failed > 0,
                done: bulkProgress.done,
                preparing: !bulkProgress.batchId && !bulkProgress.done,
              }"
              :style="{
                width:
                  (!bulkProgress.batchId && !bulkProgress.done
                    ? 100
                    : bulkProgress.progress) + '%',
              }"
            ></div>
          </div>
          <div class="bulk-progress-stats">
            <span v-if="!bulkProgress.batchId && !bulkProgress.done"
              >Preparing to send…</span
            >
            <span v-else>
              {{ bulkProgress.done ? "Processed" : "Sending" }}
              {{ bulkProgress.processed + bulkProgress.skipped }} of
              {{ bulkProgress.total }}
            </span>
            <span class="stat-ok">Sent {{ bulkProgress.sent }}</span>
            <span v-if="bulkProgress.failed" class="stat-err">
              Failed {{ bulkProgress.failed }}
            </span>
            <span v-if="bulkProgress.skipped" class="muted">
              Skipped {{ bulkProgress.skipped }}
            </span>
          </div>

          <div
            v-if="bulkProgress.done && bulkProgress.failed_rows.length"
            class="bulk-failed"
          >
            <div class="bulk-failed-head">
              <strong
                >{{ bulkProgress.failed_rows.length }} recipient(s)
                failed</strong
              >
              <button class="btn secondary small" @click="exportFailedCsv">
                Export failed as CSV
              </button>
            </div>
            <div class="bulk-failed-table">
              <table>
                <thead>
                  <tr>
                    <th>Student</th>
                    <th>Email</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(r, i) in bulkProgress.failed_rows" :key="i">
                    <td>{{ r.student || "—" }}</td>
                    <td>{{ r.email || "—" }}</td>
                    <td>{{ r.reason }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <p
            v-else-if="bulkProgress.done && !bulkProgress.failed"
            class="bulk-all-ok"
          >
            All {{ bulkProgress.sent }} email(s) queued for delivery.
          </p>
          <p v-else-if="!bulkProgress.done" class="muted">
            You can keep working — this window updates live and the tickets
            appear in the list as they’re created.
          </p>
        </div>
        <div v-if="bulkProgress.done" class="modal-footer">
          <button class="btn" @click="closeBulkProgress">Close</button>
        </div>
      </section>
    </div>

    <!-- Duplicate-submission guard: confirm before resending an identical send (BUG-4) -->
    <div
      v-if="bulkDuplicate"
      class="modal-backdrop"
      @click.self="bulkDuplicate = null"
    >
      <section class="modal-card bulk-dup-card">
        <div class="modal-header"><h2>Looks like a duplicate</h2></div>
        <div class="modal-body stack">
          <p>{{ bulkDuplicate.message }}</p>
          <p class="muted">
            This usually means a double-click or a second tab. Only resend if
            you are sure it did not go out.
          </p>
        </div>
        <div class="modal-footer">
          <button class="btn secondary" @click="bulkDuplicate = null">
            Don’t resend
          </button>
          <button class="btn danger" @click="confirmResendBulk">
            Resend anyway
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import {
  computed,
  onBeforeUnmount,
  onMounted,
  provide,
  reactive,
  ref,
  watch,
} from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import TinyMceEditor from "@desk/components/TinyMceEditor.vue";
import {
  AuthRedirectError,
  call,
  initials,
  getAgents,
  getTicketTypes,
  getUnityProfile,
  redirectToLogin,
  searchUsers,
  uploadAttachment,
} from "./api";

const TICKET_NOTICE_KEY = "unity_helpdesk_ticket_notice";
const router = useRouter();
const route = useRoute();
const sidebarOpen = ref(false);
const pageTitle = ref("My Tickets");
const pageSubtitle = ref("Fast support workspace");
const profile = ref({});
const profileMenuOpen = ref(false);
const brandLogo = "/assets/helpdesk/unity_helpdesk/favicon.svg";
const agents = ref([]);
const ticketTypes = ref([]);
const openComposer = ref(false);
const composerSaving = ref(false);
const composerUploading = ref(false);
const composerError = ref("");
const composerWarning = ref("");
const composerAttachmentInput = ref(null);
const userSuggestions = ref([]);
const session = reactive({
  name: "",
  full_name: "",
  email: "",
  username: "",
  user_image: "",
  roles: [],
  capabilities: {},
  settings: {
    unity_email_thread_layout: "Classic",
    column_preferences: [],
  },
  available_columns: [],
});
let suggestTimeout = null;
const composer = reactive({
  raised_by: "",
  subject: "",
  message: "",
  priority: "",
  ticket_type: "",
  assignee: "",
  attachments: [],
  // Optional "send a test copy first" step (manual preview → verify → unlock send).
  // testRecipients: [{ email, name }] verifiers shown as chips.
  testEnabled: false,
  testRecipients: [],
  testSent: false,
});
// Test-mail step (create composer): its own typeahead query/list + in-flight flag, so
// it never collides with the raised_by (customer email) typeahead above.
const composerTestSending = ref(false);
const composerTestSuggestions = ref([]);
const composerTestQuery = ref("");
const composerTestInputRef = ref(null);
let composerTestSuggestTimeout = null;
// Searchable "Assign To" combobox (create composer). agents already arrive A–Z from
// the backend; we re-sort defensively and filter by the typed query.
const assigneeQuery = ref("");
const assigneeOpen = ref(false);

// --- Bulk email state ---
const openBulkEmail = ref(false);
const bulkEmailSending = ref(false);
// In-flight guard for the actual send (distinct from bulkEmailSending, which is
// tied to the composer UI and reset by closeBulkEmail). Prevents the same submission
// from firing twice; server-side the job is also idempotent per batch_id.
const bulkSubmitting = ref(false);
// Live send progress + result (polled from the Unity Bulk Email Batch record). Drives
// the progress bar, the honest "X sent / K failed" panel, and the failed-CSV export.
const bulkProgressOpen = ref(false);
const bulkProgress = reactive({
  batchId: "",
  subject: "",
  status: "",
  total: 0,
  processed: 0,
  sent: 0,
  failed: 0,
  skipped: 0,
  progress: 0,
  done: false,
  failed_rows: [],
});
let bulkPollTimer = null;
// Duplicate-submission prompt: { message, payload }. Set when the server refuses an
// identical send within the guard window; "Resend anyway" re-submits with confirm_resend.
const bulkDuplicate = ref(null);
const bulkEmailUploading = ref(false);
const bulkEmailError = ref("");
const bulkEmailWarning = ref("");
const bulkEmailCsvInput = ref(null);
const bulkEmailAttachmentInput = ref(null);
const bulkResolving = ref(false);
const bulkEmail = reactive({
  mode: "reference", // "reference" (type ref numbers) | "csv" (import file)
  subject: "",
  ticket_type: "",
  message: "",
  cc: [],
  // Reference-mode raw inputs (reference numbers / student names / emails).
  tokens: [],
  // Resolved recipients — one per student (or free email). Each becomes ONE
  // ticket + ONE email to [student + guardians]:
  //   { key, token, student, name, email, guardian_emails: [], data: {}, status }
  students: [],
  attachments: [],
  mergeFields: [], // student fields usable as {{field}}
  csvImported: false,
  // Optional "send a test copy first" step (manual preview → verify → unlock send).
  // testRecipients: [{ email, name }] verifiers shown as chips.
  testEnabled: false,
  testRecipients: [],
  testSent: false,
});
// Test-mail step (bulk composer): its own typeahead query/list + in-flight flag.
const bulkTestSending = ref(false);
const bulkTestSuggestions = ref([]);
const bulkTestQuery = ref("");
const bulkTestInputRef = ref(null);
let bulkTestSuggestTimeout = null;
const includeGuardians = ref(false);
// Recipient toggles (both modes): exclude the student's own email (send to
// guardians only). includeGuardians defaults on in CSV mode (see setBulkMode).
const excludeStudent = ref(false);
// All Student doctype fields, fetched once when the bulk modal opens — used to
// show the full merge-field list and to flag {{tokens}} that won't auto-fill.
const studentMergeFields = ref([]);
const ccInputRef = ref(null);
const ccInputQuery = ref("");
const bccInputRef = ref(null);
const bccSearchQuery = ref("");
const bccResults = ref([]);
let _bccSearchTimer = null;

provide("unitySession", session);
provide("refreshUnitySession", loadSession);
// Lookups loaded at app level so child views (TicketsView, TicketDetailView)
// can reuse them via inject instead of re-fetching on every navigation.
provide("unityAgents", agents);
provide("unityTicketTypes", ticketTypes);

// Cross-view "tickets changed" signal — TicketsView injects it and reloads when it
// bumps, so a non-blocking send is reflected in the list once the request finishes.
const ticketsRefreshSignal = ref(0);
provide("unityTicketsRefresh", ticketsRefreshSignal);
function signalTicketsRefresh() {
  ticketsRefreshSignal.value += 1;
}

// Global, non-blocking notice (toast) shown above everything. Lets the send
// composers close immediately and report progress/outcome out-of-band, so the UI
// never hangs on a slow send.
const globalNotice = ref(null); // { text, type: 'info' | 'success' | 'error' }
let globalNoticeTimer = null;
function showGlobalNotice(text, type = "info", autoDismissMs = 0) {
  if (globalNoticeTimer) {
    clearTimeout(globalNoticeTimer);
    globalNoticeTimer = null;
  }
  globalNotice.value = { text, type };
  if (autoDismissMs > 0) {
    globalNoticeTimer = setTimeout(() => {
      globalNotice.value = null;
    }, autoDismissMs);
  }
}
function dismissGlobalNotice() {
  if (globalNoticeTimer) clearTimeout(globalNoticeTimer);
  globalNotice.value = null;
}

// The composer modals are tall (recipients, CSV, attachments, test-copy step),
// so a validation/blocked-action error shown only in the top-of-body banner is
// invisible when the user is down at the Send / Send Test buttons — they click
// and nothing seems to happen. These helpers surface the SAME message both in
// the existing inline banner AND as the fixed top-center popup (showGlobalNotice,
// z-index above the modal), so it's directly visible without scrolling. Every
// blocking error/condition in the create-ticket and bulk-email composers routes
// through here.
function composerFail(msg) {
  composerError.value = msg;
  showGlobalNotice(msg, "error", 6000);
}
function bulkFail(msg) {
  bulkEmailError.value = msg;
  showGlobalNotice(msg, "error", 6000);
}

const capabilities = computed(() => session.capabilities || {});
const canViewMyTickets = computed(
  () => !!capabilities.value.can_view_my_tickets
);
const canViewAllTickets = computed(
  () => !!capabilities.value.can_view_all_tickets
);
const canManageAgents = computed(() => !!capabilities.value.can_manage_agents);
const canManageUnitySettings = computed(
  () => !!capabilities.value.can_manage_unity_settings
);
const threadLayout = computed(
  () => session.settings?.unity_email_thread_layout || "Classic"
);
const threadLayoutClass = computed(
  () =>
    `thread-layout-${String(threadLayout.value || "classic")
      .toLowerCase()
      .replace(/\s+/g, "-")}`
);

onMounted(async () => {
  await Promise.allSettled([loadSession(), loadLookups()]);
});

watch(
  () => route.fullPath,
  () => {
    enforceRouteAccess();
  }
);

function setTitle(title, subtitle = "Fast support workspace") {
  pageTitle.value = title;
  pageSubtitle.value = subtitle;
}

async function loadSession() {
  try {
    const data = (await getUnityProfile()) || {};
    profile.value = data || {};
    session.name = data.name || "";
    session.full_name = data.full_name || "";
    session.email = data.email || "";
    session.username = data.username || "";
    session.user_image = data.user_image || "";
    session.roles = data.roles || [];
    session.capabilities = data.capabilities || {};
    session.settings = {
      unity_email_thread_layout:
        data.settings?.unity_email_thread_layout || "Classic",
      column_preferences: Array.isArray(data.settings?.column_preferences)
        ? data.settings.column_preferences
        : [],
    };
    session.available_columns = Array.isArray(data.available_columns)
      ? data.available_columns
      : [];
    enforceRouteAccess();
  } catch (err) {
    if (err instanceof AuthRedirectError) {
      // api.js already kicked off the redirect; nothing else to do.
      return;
    }
    // If the error didn't carry the standard auth markers but the profile
    // came back empty (no email, no roles), the session is effectively gone.
    // Fall back to a login redirect so we don't render the SPA shell as Guest.
    if (!session.email && !session.roles?.length) {
      redirectToLogin();
      return;
    }
    composerError.value = err.message;
  }
}

async function loadLookups() {
  const [agentRows, typeRows] = await Promise.allSettled([
    getAgents(),
    getTicketTypes(),
  ]);
  agents.value = agentRows.status === "fulfilled" ? agentRows.value || [] : [];
  ticketTypes.value =
    typeRows.status === "fulfilled" ? typeRows.value || [] : [];
}

function enforceRouteAccess() {
  if (!canViewMyTickets.value && route.path !== "/") {
    return;
  }
  if (route.path === "/tickets/all" && !canViewAllTickets.value) {
    router.replace("/tickets/my");
    return;
  }
  if (route.path === "/agents" && !canManageAgents.value) {
    router.replace(canManageUnitySettings.value ? "/settings" : "/tickets/my");
    return;
  }
  if (route.path === "/settings" && !canManageUnitySettings.value) {
    router.replace("/tickets/my");
  }
}

function closeComposer() {
  openComposer.value = false;
  composerSaving.value = false;
  composerUploading.value = false;
  composerError.value = "";
  composerWarning.value = "";
  userSuggestions.value = [];
  composer.raised_by = "";
  composer.subject = "";
  composer.message = "";
  composer.priority = "";
  composer.ticket_type = "";
  composer.assignee = "";
  assigneeQuery.value = "";
  assigneeOpen.value = false;
  composer.attachments = [];
  composer.testEnabled = false;
  composer.testRecipients = [];
  composer.testSent = false;
  composerTestQuery.value = "";
  composerTestSuggestions.value = [];
  composerTestSending.value = false;
}

function applyTemplateSubjectToComposer(subject) {
  if (!subject) return;
  if (
    composer.subject &&
    composer.subject.trim() &&
    !window.confirm("Replace the current subject with the template's subject?")
  ) {
    return;
  }
  composer.subject = subject;
}

function applyTemplateSubjectToBulkEmail(subject) {
  if (!subject) return;
  if (
    bulkEmail.subject &&
    bulkEmail.subject.trim() &&
    !window.confirm("Replace the current subject with the template's subject?")
  ) {
    return;
  }
  bulkEmail.subject = subject;
}

// Build a "{{field}}" label without putting literal }} in the template (Vue's
// mustache parser would close the interpolation at the first }}).
function mergeFieldToken(field) {
  return "{{" + field + "}}";
}

// Email Template is the primary source — replace BOTH subject and body. The
// editor already swapped its content; we sync v-model + the subject field here.
function applyEmailTemplateToBulkEmail(payload) {
  const subject = payload && payload.subject;
  const body = payload && payload.body;
  if (typeof subject === "string" && subject.trim()) {
    bulkEmail.subject = subject;
  }
  if (typeof body === "string") {
    bulkEmail.message = body;
  }
}

// Create-Ticket composer: same as bulk — Email Template drives subject + body.
// The single send does NOT render Jinja, so any {{placeholders}} load as-is for
// the agent to fill before sending (matches the canned-response button).
function applyEmailTemplateToCreateTicket(payload) {
  const subject = payload && payload.subject;
  const body = payload && payload.body;
  if (typeof subject === "string" && subject.trim()) {
    composer.subject = subject;
  }
  if (typeof body === "string") {
    composer.message = body;
  }
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

// --- Searchable "Assign To" combobox (create composer) ---
const sortedAgents = computed(() =>
  [...agents.value].sort((a, b) =>
    (a.full_name || a.name || "").localeCompare(b.full_name || b.name || "")
  )
);
const assigneeMatches = computed(() => {
  const q = assigneeQuery.value.trim().toLowerCase();
  if (!q) return sortedAgents.value;
  return sortedAgents.value.filter((a) =>
    [a.full_name, a.name, a.email].some((v) =>
      String(v || "")
        .toLowerCase()
        .includes(q)
    )
  );
});
function assigneeLabel(name) {
  if (!name) return "";
  const a = agents.value.find((x) => x.name === name);
  return a ? a.full_name || a.name : name;
}
function onAssigneeInput() {
  assigneeOpen.value = true;
}
function onAssigneeFocus() {
  assigneeOpen.value = true;
}
function onAssigneeBlur() {
  // Let a click on an option register first, then close and snap the text back to
  // the actual selection (so the box never shows an unpicked half-typed query).
  setTimeout(() => {
    assigneeOpen.value = false;
    assigneeQuery.value = assigneeLabel(composer.assignee);
  }, 120);
}
function pickAssignee(agent) {
  composer.assignee = agent.name;
  assigneeQuery.value = agent.full_name || agent.name;
  assigneeOpen.value = false;
}
function clearAssignee() {
  composer.assignee = "";
  assigneeQuery.value = "";
  assigneeOpen.value = false;
}

// --- Create-composer test-mail step (verifier chips) ---
function focusCreateTestInput() {
  composerTestInputRef.value?.focus();
}

function onCreateTestEmailInput() {
  clearTimeout(composerTestSuggestTimeout);
  const query = (composerTestQuery.value || "").trim();
  if (query.length < 2) {
    composerTestSuggestions.value = [];
    return;
  }
  composerTestSuggestTimeout = setTimeout(async () => {
    try {
      composerTestSuggestions.value = await searchUsers(query);
    } catch {
      composerTestSuggestions.value = [];
    }
  }, 300);
}

function selectCreateTestUser(user) {
  _addTestRecipient(
    composer.testRecipients,
    user.email || user.name,
    user.full_name || user.name
  );
  composerTestQuery.value = "";
  composerTestSuggestions.value = [];
  composerTestInputRef.value?.focus();
}

function addCreateTestFromInput() {
  if (!composerTestQuery.value || !composerTestQuery.value.trim()) return;
  _addTestRecipientsFromText(composer.testRecipients, composerTestQuery.value);
  composerTestQuery.value = "";
  composerTestSuggestions.value = [];
}

function onCreateTestKeydown(e) {
  if (e.key === "," || e.key === ";") {
    e.preventDefault();
    addCreateTestFromInput();
  } else if (
    e.key === "Backspace" &&
    !composerTestQuery.value &&
    composer.testRecipients.length
  ) {
    composer.testRecipients.pop();
  }
}

function onCreateTestPaste(e) {
  const text = (e.clipboardData || window.clipboardData)?.getData("text") || "";
  if (!/[\s,;]/.test(text)) return; // single token — let default paste + typeahead work
  e.preventDefault();
  _addTestRecipientsFromText(composer.testRecipients, text);
  composerTestQuery.value = "";
  composerTestSuggestions.value = [];
}

function removeCreateTestRecipient(email) {
  composer.testRecipients = composer.testRecipients.filter(
    (r) => r.email !== email
  );
}

// Send ONE test copy of the create-ticket email (rendered like the customer's own
// email, via raised_by) to one or more verifiers — no ticket is created. Unlocks
// the real "Create & Send".
async function sendCreateTestEmail() {
  composerError.value = "";
  if (!composer.subject || !composer.subject.trim()) {
    composerFail("Subject is required before sending a test.");
    return;
  }
  if (!composer.ticket_type) {
    composerFail("Ticket Type is required before sending a test.");
    return;
  }
  if (!composer.message || !composer.message.trim()) {
    composerFail("Message is required before sending a test.");
    return;
  }
  // Fold in any half-typed address still in the input.
  if (composerTestQuery.value.trim()) addCreateTestFromInput();
  const emails = composer.testRecipients.map((r) => r.email);
  if (!emails.length) {
    composerFail("Add at least one valid verifier email for the test copy.");
    return;
  }
  composerTestSending.value = true;
  composerTestSuggestions.value = [];
  try {
    await call("helpdesk.api.unity_helpdesk_ext.send_test_email", {
      subject: composer.subject,
      message: composer.message,
      test_email: emails.join(", "),
      ticket_type: composer.ticket_type,
      raised_by: composer.raised_by,
      attachments: JSON.stringify(
        composer.attachments.map((attachment) => attachment.name)
      ),
    });
    composer.testSent = true;
    showGlobalNotice(
      `Test sent to ${composer.testRecipients
        .map((r) => r.name)
        .join(", ")} — check the inbox, then Create & Send.`,
      "success",
      8000
    );
  } catch (err) {
    composerFail("Test send failed: " + (err?.message || err));
  } finally {
    composerTestSending.value = false;
  }
}

async function handleComposerAttachments(event) {
  const files = Array.from(event.target.files || []);
  if (!files.length) return;
  composerUploading.value = true;
  composerError.value = "";
  try {
    for (const file of files) {
      const uploaded = await uploadAttachment(file);
      composer.attachments.push(uploaded);
    }
  } catch (err) {
    composerFail(err.message);
  } finally {
    composerUploading.value = false;
    if (composerAttachmentInput.value) {
      composerAttachmentInput.value.value = "";
    }
  }
}

function removeComposerAttachment(name) {
  composer.attachments = composer.attachments.filter(
    (attachment) => attachment.name !== name
  );
}

async function createTicket() {
  composerError.value = "";
  composerWarning.value = "";
  if (!composer.subject || !composer.subject.trim()) {
    composerFail("Subject is required.");
    return;
  }
  if (!composer.ticket_type) {
    composerFail("Ticket Type is required.");
    return;
  }
  // Verification gate: if "Send a test copy first" is on, a test must be sent before
  // the real send. Explain rather than silently doing nothing.
  if (composer.testEnabled && !composer.testSent) {
    composerFail(
      "You enabled “Send a test copy first”. Click “Send Test”, verify it in the inbox — or untick that option to send directly."
    );
    return;
  }
  // Snapshot the payload BEFORE closing (closeComposer resets the form). Then close
  // the composer immediately and report progress out-of-band — the request runs in
  // the background so the UI never hangs on a slow send. The new ticket appears in
  // the list once the request finishes (or sooner — it's committed early server-side).
  const payload = {
    subject: composer.subject,
    raised_by: composer.raised_by,
    message: composer.message,
    priority: composer.priority,
    ticket_type: composer.ticket_type,
    assignee: composer.assignee,
    attachments: composer.attachments.map((attachment) => attachment.name),
  };
  closeComposer();
  showGlobalNotice(
    "Sending email… the new ticket will appear in your list shortly.",
    "info"
  );
  try {
    const result = await call(
      "helpdesk.api.unity_helpdesk_ext.create_ticket",
      payload
    );
    signalTicketsRefresh();
    if (result?.warning) {
      showGlobalNotice(result.warning, "error", 9000);
    } else {
      showGlobalNotice("Ticket created and email sent.", "success", 5000);
    }
  } catch (err) {
    showGlobalNotice(
      "Ticket creation failed: " + (err?.message || err),
      "error",
      10000
    );
  }
}

// --- Bulk email ---
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// --- Shared helpers for the "send a test copy first" step (verifier chips) ---
// Split a comma/semicolon/whitespace-separated string into trimmed, lowercased,
// deduped emails (test verifiers are emails, so splitting on spaces is safe here).
function splitTestEmails(str) {
  const out = [];
  const seen = new Set();
  for (const raw of (str || "").split(/[\s,;]+/)) {
    const e = raw.trim().toLowerCase();
    if (e && !seen.has(e)) {
      seen.add(e);
      out.push(e);
    }
  }
  return out;
}
// Add ONE {email, name} verifier chip to a list (validated + deduped). Returns true
// if added.
function _addTestRecipient(list, email, name) {
  const e = (email || "").trim().toLowerCase();
  if (!e || !EMAIL_REGEX.test(e)) return false;
  if (list.some((r) => r.email === e)) return false;
  list.push({ email: e, name: (name || "").trim() || e });
  return true;
}
// Add every valid email found in a typed/pasted string as its own chip.
function _addTestRecipientsFromText(list, text) {
  let added = false;
  for (const email of splitTestEmails(text)) {
    if (_addTestRecipient(list, email)) added = true;
  }
  return added;
}

// Deliverable emails for one student: the student + (optionally) their guardians,
// deduped WITHIN the student. Siblings sharing a guardian still each get their own
// personalised ticket that mails that guardian.
function studentGroupEmails(s, includeStudent, includeGuardians) {
  const out = [];
  const seen = new Set();
  const studentEmail = (s.email || "").toLowerCase().trim();
  const push = (e) => {
    const x = (e || "").toLowerCase().trim();
    if (x && EMAIL_REGEX.test(x) && !seen.has(x)) {
      seen.add(x);
      out.push(x);
    }
  };
  if (includeStudent && studentEmail) push(studentEmail);
  if (includeGuardians) {
    for (const g of s.guardian_emails || []) {
      // Never re-introduce the student's own email via the guardian list, so
      // "Exclude student email" truly excludes it even if the data lists it twice.
      if ((g || "").toLowerCase().trim() === studentEmail) continue;
      push(g);
    }
  }
  return out;
}

// Current recipient selection (applies to both reference and CSV modes).
const recipientFlags = computed(() => ({
  includeStudent: !excludeStudent.value,
  includeGuardians: includeGuardians.value,
}));

// One send group per resolved student (or free email) -> one ticket + one email.
const bulkEmailGroups = computed(() => {
  const { includeStudent, includeGuardians: incG } = recipientFlags.value;
  const groups = [];
  for (const s of bulkEmail.students) {
    if (s.status === "notfound") continue;
    const emails = studentGroupEmails(s, includeStudent, incG);
    if (!emails.length) continue;
    groups.push({ student: s.student || null, emails, data: s.data || {} });
  }
  return groups;
});
const bulkEmailStudentCount = computed(() => bulkEmailGroups.value.length);
const guardianCountLabel = computed(() => {
  if (!includeGuardians.value) return "";
  const total = bulkEmail.students.reduce(
    (n, s) => n + (s.guardian_emails || []).length,
    0
  );
  return total ? `${total} guardian email(s) will be included` : "";
});

// Read-only preview of exactly who gets emailed — one line per student.
const recipientsPreview = computed(() => {
  const { includeStudent, includeGuardians: incG } = recipientFlags.value;
  return bulkEmail.students
    .filter((s) => s.status !== "notfound")
    .map((s) => {
      const emails = studentGroupEmails(s, includeStudent, incG);
      return emails.length ? `${s.name}: ${emails.join(", ")}` : null;
    })
    .filter(Boolean)
    .join("\n");
});

// Tokens ({{x}}) the composed subject/body uses, each flagged recognised or not —
// recognised = a Student fieldname, a CSV column, or a resolved student's data key.
const templateTokens = computed(() => {
  const known = new Set();
  studentMergeFields.value.forEach((f) => known.add(f.fieldname));
  (bulkEmail.mergeFields || []).forEach((f) => known.add(f));
  bulkEmail.students.forEach((s) =>
    Object.keys(s.data || {}).forEach((k) => known.add(k))
  );
  const text = `${bulkEmail.subject || ""} ${bulkEmail.message || ""}`;
  const seen = new Set();
  const out = [];
  // Local regex (matchAll) — no shared lastIndex mutation, so the computed stays
  // side-effect-free.
  for (const match of text.matchAll(/\{\{\s*([\w. ]+?)\s*\}\}/g)) {
    const token = (match[1] || "").trim();
    if (!token || seen.has(token)) continue;
    seen.add(token);
    out.push({ token, recognised: known.has(token) });
  }
  return out;
});
const unknownTokens = computed(() =>
  templateTokens.value.filter((t) => !t.recognised).map((t) => t.token)
);

function setBulkMode(mode) {
  if (bulkEmail.mode === mode) return;
  bulkEmail.mode = mode;
  bulkEmail.tokens = [];
  bulkEmail.students = [];
  bulkEmail.mergeFields = [];
  bulkEmail.csvImported = false;
  bccSearchQuery.value = "";
  bccResults.value = [];
  bulkEmailWarning.value = "";
  bulkEmailError.value = "";
  // CSV imports historically include guardians by default; reference entry starts
  // students-only. The agent can change both via the recipient toggles.
  includeGuardians.value = mode === "csv";
  excludeStudent.value = false;
}

function chipTitle(s) {
  const parts = [s.email || "(no email on file)"];
  if ((s.guardian_emails || []).length) {
    parts.push(`guardians: ${s.guardian_emails.join(", ")}`);
  }
  return parts.join(" · ");
}

function focusCcInput() {
  ccInputRef.value?.focus();
}

function addCcFromInput() {
  const val = ccInputQuery.value.trim().toLowerCase();
  if (!EMAIL_REGEX.test(val)) return;
  if (bulkEmail.cc.find((c) => c.email === val)) {
    ccInputQuery.value = "";
    return;
  }
  bulkEmail.cc.push({ email: val });
  ccInputQuery.value = "";
}

function removeCc(email) {
  bulkEmail.cc = bulkEmail.cc.filter((c) => c.email !== email);
}

function onCcKeydown(e) {
  if (e.key === ",") {
    e.preventDefault();
    addCcFromInput();
  } else if (
    e.key === "Backspace" &&
    !ccInputQuery.value &&
    bulkEmail.cc.length
  ) {
    bulkEmail.cc.pop();
  }
}

function focusBccInput() {
  bccInputRef.value?.focus();
}

// Add a reference number / student name / email the agent typed or picked. The
// backend resolve_bulk_email_students turns each token into a student (with
// guardians + merge data) or a free email.
// Reference numbers / emails are separated by comma, semicolon, newline or tab when
// a list is typed or pasted (e.g. "SHRB72, SHTA94" or an Excel column). Plain spaces
// are NOT separators — a typed student name ("John Smith") must stay one token.
const REF_SPLIT_RE = /[,;\n\r\t]+/;

// Push ONE raw token (case-insensitive dedupe). Returns true if it was added.
function _pushStudentToken(token) {
  const t = (token || "").trim();
  if (!t) return false;
  if (bulkEmail.tokens.some((x) => x.toLowerCase() === t.toLowerCase()))
    return false;
  bulkEmail.tokens.push(t);
  return true;
}

function addStudentToken(token) {
  if (_pushStudentToken(token)) resolveStudents();
}

// Add MANY tokens at once (a pasted/typed list), resolving only ONCE at the end so a
// 180-reference paste is a single backend round-trip, not 180.
function addStudentTokens(list) {
  let added = false;
  for (const raw of list) {
    if (_pushStudentToken(raw)) added = true;
  }
  if (added) resolveStudents();
}

function selectStudent(r) {
  addStudentToken(r.email || r.name || "");
  bccSearchQuery.value = "";
  bccResults.value = [];
  bccInputRef.value?.focus();
}

function addStudentFromInput() {
  const raw = bccSearchQuery.value;
  if (!raw || !raw.trim()) return;
  // Split a typed list (comma/semicolon/newline/tab) into individual references so
  // each becomes its own resolved student chip instead of one "not found" blob.
  const parts = raw.split(REF_SPLIT_RE).filter((p) => p.trim());
  if (parts.length > 1) addStudentTokens(parts);
  else addStudentToken(raw);
  bccSearchQuery.value = "";
  bccResults.value = [];
}

// Paste of a multi-reference list: tokenize immediately (no Enter needed). A single
// pasted value falls through to the default paste so the typeahead still works.
function onBccPaste(e) {
  const text = (e.clipboardData || window.clipboardData)?.getData("text") || "";
  const parts = text.split(REF_SPLIT_RE).filter((p) => p.trim());
  if (parts.length <= 1) return; // let the browser paste it normally
  e.preventDefault();
  addStudentTokens(parts);
  bccSearchQuery.value = "";
  bccResults.value = [];
}

function removeStudent(key) {
  const s = bulkEmail.students.find((x) => x.key === key);
  if (bulkEmail.mode === "reference") {
    if (s && s.token) {
      bulkEmail.tokens = bulkEmail.tokens.filter(
        (t) => t.toLowerCase() !== s.token.toLowerCase()
      );
    }
    resolveStudents();
  } else {
    bulkEmail.students = bulkEmail.students.filter((x) => x.key !== key);
  }
}

function onBccBackspace() {
  if (!bccSearchQuery.value && bulkEmail.students.length) {
    removeStudent(bulkEmail.students[bulkEmail.students.length - 1].key);
  }
}

function onBccSearch() {
  clearTimeout(_bccSearchTimer);
  const q = bccSearchQuery.value.trim();
  if (q.length < 2) {
    bccResults.value = [];
    return;
  }
  _bccSearchTimer = window.setTimeout(async () => {
    try {
      const results = await call(
        "helpdesk.api.unity_helpdesk.search_contacts",
        { query: q }
      );
      bccResults.value = results || [];
    } catch {
      bccResults.value = [];
    }
  }, 280);
}

// Resolve the typed reference numbers / names / emails into students (with their
// guardians + merge data) via the backend. Rebuilds bulkEmail.students from the
// raw token list so add/remove stays in sync.
async function resolveStudents() {
  const tokens = bulkEmail.tokens.slice();
  if (!tokens.length) {
    bulkEmail.students = [];
    bulkEmail.mergeFields = [];
    bulkEmailWarning.value = "";
    return;
  }
  bulkResolving.value = true;
  try {
    const result = await call(
      "helpdesk.api.unity_helpdesk.resolve_bulk_email_students",
      { refs: JSON.stringify(tokens) }
    );
    const students = result?.students || [];
    const out = [];
    const seenStudents = new Set();
    const seenFree = new Set();
    for (const token of tokens) {
      const t = token.toLowerCase();
      const st = students.find(
        (s) =>
          String(s.student || "").toLowerCase() === t ||
          String((s.data && s.data.reference_number) || "").toLowerCase() ===
            t ||
          String(s.email || "").toLowerCase() === t
      );
      if (st) {
        if (seenStudents.has(st.student)) continue;
        seenStudents.add(st.student);
        out.push({
          key: `s:${st.student}`,
          token,
          student: st.student,
          name: st.student_name || st.student,
          email: st.email || "",
          guardian_emails: st.guardian_emails || [],
          data: st.data || {},
          status: st.has_email ? "student" : "noemail",
        });
      } else if (EMAIL_REGEX.test(token)) {
        if (seenFree.has(t)) continue;
        seenFree.add(t);
        out.push({
          key: `f:${t}`,
          token,
          student: null,
          name: token,
          email: t,
          guardian_emails: [],
          data: {},
          status: "free",
        });
      } else {
        out.push({
          key: `n:${t}`,
          token,
          student: null,
          name: token,
          email: "",
          guardian_emails: [],
          data: {},
          status: "notfound",
        });
      }
    }
    bulkEmail.students = out;
    bulkEmail.mergeFields = result?.merge_fields || [];
    const notFound = out
      .filter((s) => s.status === "notfound")
      .map((s) => s.token);
    const noEmail = out
      .filter((s) => s.status === "noemail")
      .map((s) => s.name);
    let warn = "";
    if (notFound.length)
      warn += `Couldn't find ${notFound.length} reference(s): ${notFound
        .slice(0, 5)
        .join(", ")}${notFound.length > 5 ? ", …" : ""}. `;
    if (noEmail.length)
      warn += `${noEmail.length} student(s) have no email on file${
        includeGuardians.value ? " — guardians will still be emailed" : ""
      }.`;
    bulkEmailWarning.value = warn.trim();
  } catch (err) {
    if (err instanceof AuthRedirectError || err?.code === "AUTH_REDIRECT")
      return;
    bulkFail(err?.message || "Couldn't resolve students.");
  } finally {
    bulkResolving.value = false;
  }
}

function openBulkEmailModal() {
  resetBulkEmail();
  openBulkEmail.value = true;
  loadStudentMergeFields();
}

// Fetch the full Student field list once (cached for the session) so the composer
// can show all available {{fields}} and flag unknown template tokens.
async function loadStudentMergeFields() {
  if (studentMergeFields.value.length) return;
  try {
    const fields = await call(
      "helpdesk.api.unity_helpdesk.get_student_merge_fields"
    );
    studentMergeFields.value = Array.isArray(fields) ? fields : [];
  } catch {
    studentMergeFields.value = [];
  }
}

function resetBulkEmail() {
  bulkEmailSending.value = false;
  bulkEmailUploading.value = false;
  bulkResolving.value = false;
  bulkEmailError.value = "";
  bulkEmailWarning.value = "";
  bulkEmail.mode = "reference";
  bulkEmail.subject = "";
  bulkEmail.ticket_type = "";
  bulkEmail.message = "";
  bulkEmail.cc = [];
  bulkEmail.tokens = [];
  bulkEmail.students = [];
  bulkEmail.attachments = [];
  bulkEmail.mergeFields = [];
  bulkEmail.csvImported = false;
  bulkEmail.testEnabled = false;
  bulkEmail.testRecipients = [];
  bulkEmail.testSent = false;
  bulkTestQuery.value = "";
  bulkTestSuggestions.value = [];
  bulkTestSending.value = false;
  ccInputQuery.value = "";
  bccSearchQuery.value = "";
  bccResults.value = [];
  includeGuardians.value = false;
  excludeStudent.value = false;
}

function closeBulkEmail() {
  openBulkEmail.value = false;
  resetBulkEmail();
}

async function handleBulkEmailCsv(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  bulkEmailError.value = "";
  bulkEmailWarning.value = "";
  bulkEmailUploading.value = true;
  try {
    const text = await file.text();
    // Parse on the backend (robust to quoted commas) — returns headers + per-row
    // data so we get the full mail-merge context, not just the email column.
    const result = await call(
      "helpdesk.api.unity_helpdesk_ext.parse_bulk_email_csv",
      { content: text }
    );
    const rows = result?.rows || [];
    if (!rows.length) {
      bulkFail(
        "No students resolved from the CSV. Include an 'id' (student) column — the email, details and guardians are looked up from it."
      );
      return;
    }
    // Reconstruct one entry per student: the student row (name) + its guardian
    // rows ("Name (guardian)"), linked by data._student. Each becomes one ticket.
    const byStudent = {};
    const order = [];
    for (const row of rows) {
      const sid = (row.data && row.data._student) || "";
      const email = (row.email || "").toLowerCase().trim();
      const key = sid ? `s:${sid}` : `f:${email}`;
      if (!byStudent[key]) {
        byStudent[key] = {
          key,
          token: "",
          student: sid || null,
          name: "",
          email: "",
          guardian_emails: [],
          data: {},
          status: sid ? "student" : "free",
        };
        order.push(key);
      }
      const g = byStudent[key];
      const isGuardian = /\(guardian\)\s*$/i.test(row.name || "");
      if (isGuardian) {
        if (email) g.guardian_emails.push(email);
      } else {
        g.email = email;
        g.name = row.name || email;
        const data = { ...(row.data || {}) };
        delete data._student;
        g.data = data;
        if (!email) g.status = "noemail";
      }
      if (!g.name) g.name = row.name || email;
    }
    bulkEmail.students = order.map((k) => byStudent[k]);
    bulkEmail.mergeFields = result.merge_fields || [];
    bulkEmail.csvImported = true;
    let note = `${result.student_count || 0} student${
      (result.student_count || 0) === 1 ? "" : "s"
    } loaded`;
    if (result.guardian_count)
      note += ` + ${result.guardian_count} guardian(s)`;
    note += ".";
    if (result.unmatched_count) note += ` ${result.unmatched_count} not found.`;
    if (result.school_mismatch_count)
      note += ` ${result.school_mismatch_count} wrong-school skipped.`;
    if (result.no_email_count)
      note += ` ${result.no_email_count} without an email.`;
    if (result.duplicate_count)
      note += ` ${result.duplicate_count} duplicate(s) skipped.`;
    if (result.truncated)
      note += ` Only the first ${rows.length} recipients were kept — split the CSV to send the rest.`;
    bulkEmailWarning.value = note;
  } catch (err) {
    bulkFail(err.message || "CSV import failed.");
  } finally {
    bulkEmailUploading.value = false;
    if (bulkEmailCsvInput.value) bulkEmailCsvInput.value.value = "";
  }
}

async function handleBulkEmailAttachments(event) {
  const files = Array.from(event.target.files || []);
  if (!files.length) return;
  bulkEmailUploading.value = true;
  bulkEmailError.value = "";
  try {
    for (const file of files) {
      const uploaded = await uploadAttachment(file);
      bulkEmail.attachments.push(uploaded);
    }
  } catch (err) {
    bulkFail(err.message);
  } finally {
    bulkEmailUploading.value = false;
    if (bulkEmailAttachmentInput.value) {
      bulkEmailAttachmentInput.value.value = "";
    }
  }
}

function removeBulkEmailAttachment(name) {
  bulkEmail.attachments = bulkEmail.attachments.filter(
    (attachment) => attachment.name !== name
  );
}

async function sendBulkEmail() {
  // Guard: never let one submission fire twice (double-click / rapid re-invoke).
  if (bulkSubmitting.value) return;
  bulkEmailError.value = "";
  bulkEmailWarning.value = "";
  // Fold in a CC address that was typed but not yet committed with a comma/Enter, so it
  // isn't silently dropped on Send (same as the test-recipient box does).
  if (ccInputQuery.value.trim()) addCcFromInput();
  // One group per student (or free email): the student and/or their guardians,
  // per the recipient toggles.
  const groups = bulkEmailGroups.value;
  if (!groups.length) {
    const hasStudents = bulkEmail.students.some((s) => s.status !== "notfound");
    if (hasStudents && excludeStudent.value && !includeGuardians.value) {
      bulkFail(
        "Recipient options exclude everyone — enable “Include guardian emails” or uncheck “Exclude student email”."
      );
    } else if (hasStudents) {
      bulkFail(
        "No deliverable email for the selected recipients — check Include guardians / Exclude student."
      );
    } else {
      bulkFail(
        bulkEmail.mode === "csv"
          ? "Import a CSV with at least one student that has an email."
          : "Add at least one student (reference number) or recipient before sending."
      );
    }
    return;
  }
  if (!bulkEmail.subject.trim()) {
    bulkFail("Subject is required.");
    return;
  }
  if (!bulkEmail.ticket_type) {
    bulkFail("Ticket Type is required.");
    return;
  }
  if (!bulkEmail.message.trim()) {
    bulkFail("Message is required.");
    return;
  }
  // Verification gate: if "Send a test copy first" is on, a test must be sent (and
  // eyeballed) before the real send. Tell the user exactly what to do rather than
  // silently doing nothing.
  if (bulkEmail.testEnabled && !bulkEmail.testSent) {
    bulkFail(
      "You enabled “Send a test copy first”. Click “Send Test”, verify it in the inbox — or untick that option to send directly."
    );
    return;
  }
  // Build the payload BEFORE closing the composer (closeBulkEmail resets the form).
  const ccEmails = bulkEmail.cc.map((c) => c.email);
  const payload = {
    subject: bulkEmail.subject,
    message: bulkEmail.message,
    ticket_type: bulkEmail.ticket_type,
    mode: bulkEmail.mode,
    groups: JSON.stringify(groups),
    cc: ccEmails.length ? JSON.stringify(ccEmails) : null,
    attachments: JSON.stringify(
      bulkEmail.attachments.map((attachment) => attachment.name)
    ),
  };

  // Close the composer and hand off to the live progress modal.
  closeBulkEmail();
  await submitBulkSend(payload);
}

// Send (or resend) a bulk-email payload, then either open the live progress modal or,
// if the server flags an accidental duplicate, ask the agent to confirm a resend.
async function submitBulkSend(payload) {
  if (bulkSubmitting.value) return;
  bulkSubmitting.value = true;
  // IMMEDIATE feedback — open the progress modal in a "Preparing…" state the instant Send is
  // clicked, BEFORE the request, so the user never sees a blank moment ("nothing happened").
  // startBulkProgress() swaps in the real batch + live polling once the request returns.
  stopBulkPolling();
  Object.assign(bulkProgress, {
    batchId: "",
    subject: payload?.subject || "",
    status: "Preparing",
    total: 0,
    processed: 0,
    sent: 0,
    failed: 0,
    skipped: 0,
    progress: 0,
    done: false,
    failed_rows: [],
  });
  bulkProgressOpen.value = true;
  try {
    const result = await call(
      "helpdesk.api.unity_helpdesk_ext.bulk_send_email",
      payload
    );
    if (result?.duplicate) {
      // Accidental double-send (second tab, refresh, two agents). Ask before resending.
      bulkProgressOpen.value = false;
      bulkDuplicate.value = {
        message:
          result.message ||
          "You already sent this exact email a moment ago and it is still being processed.",
        payload,
      };
      return;
    }
    bulkDuplicate.value = null;
    if (result?.batch_id) {
      startBulkProgress(
        result.batch_id,
        payload.subject,
        result.student_count || 0,
        result.invalid_count || 0
      );
    } else if (result?.warning) {
      bulkProgressOpen.value = false;
      showGlobalNotice(result.warning, "error", 9000);
    } else {
      bulkProgressOpen.value = false;
      showGlobalNotice("Bulk email started.", "success", 6000);
      signalTicketsRefresh();
    }
  } catch (err) {
    // Surface the REAL server message for a validation error (e.g. a recipient-limit throw) so
    // the user knows exactly why. Keep the vague "don't resend" only for network/timeout/5xx,
    // where the job may actually have started.
    bulkProgressOpen.value = false;
    const msg = err?.message;
    const transient =
      err?.code === "REQUEST_TIMEOUT" ||
      err?.code === "NETWORK_ERROR" ||
      (typeof err?.status === "number" && err.status >= 500);
    if (msg && !transient) {
      showGlobalNotice(msg, "error", 10000);
    } else {
      showGlobalNotice(
        "Couldn't confirm the bulk send started. Do NOT resend — check the tickets list in a minute; if the tickets aren't there, contact support.",
        "error",
        12000
      );
    }
  } finally {
    bulkSubmitting.value = false;
  }
}

// Resend after the duplicate prompt — same payload, with confirm_resend so the server
// skips the guard.
async function confirmResendBulk() {
  const dup = bulkDuplicate.value;
  bulkDuplicate.value = null;
  if (!dup) return;
  await submitBulkSend({ ...dup.payload, confirm_resend: "1" });
}

// Open the progress modal and begin polling the batch record every 1.5s.
function startBulkProgress(batchId, subject, total, invalidCount) {
  stopBulkPolling();
  Object.assign(bulkProgress, {
    batchId,
    subject: subject || "",
    status: "Queued",
    total: total || 0,
    processed: 0,
    sent: 0,
    failed: 0,
    skipped: 0,
    progress: 0,
    done: false,
    failed_rows: [],
  });
  bulkProgressOpen.value = true;
  if (invalidCount) {
    showGlobalNotice(
      `${invalidCount} invalid address(es) were skipped before sending.`,
      "info",
      6000
    );
  }
  pollBulkProgressOnce();
  bulkPollTimer = setInterval(pollBulkProgressOnce, 1500);
}

async function pollBulkProgressOnce() {
  if (!bulkProgress.batchId) return;
  try {
    const s = await call(
      "helpdesk.api.unity_helpdesk_ext.get_bulk_email_batch_status",
      { batch_id: bulkProgress.batchId }
    );
    if (!s || !s.found) return;
    Object.assign(bulkProgress, {
      status: s.status,
      total: s.total,
      processed: s.processed,
      sent: s.sent,
      failed: s.failed,
      skipped: s.skipped,
      progress: s.progress,
      done: s.done,
      failed_rows: s.failed_rows || [],
    });
    // Reflect newly-created tickets in the list as they land.
    signalTicketsRefresh();
    if (s.done) stopBulkPolling();
  } catch (err) {
    // Transient — keep polling; a later tick will succeed.
  }
}

function stopBulkPolling() {
  if (bulkPollTimer) {
    clearInterval(bulkPollTimer);
    bulkPollTimer = null;
  }
}

function closeBulkProgress() {
  stopBulkPolling();
  bulkProgressOpen.value = false;
  signalTicketsRefresh();
}

// Click-away only closes once the send is finished (never mid-send).
function maybeCloseBulkProgress() {
  if (bulkProgress.done) closeBulkProgress();
}

// Download the failed recipients (student, email, reason) as CSV so the agent can fix
// and re-send only the ones that failed.
function exportFailedCsv() {
  const rows = bulkProgress.failed_rows || [];
  if (!rows.length) return;
  const esc = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
  const lines = ["student,email,reason"];
  for (const r of rows) {
    lines.push([esc(r.student), esc(r.email), esc(r.reason)].join(","));
  }
  const blob = new Blob([lines.join("\n")], {
    type: "text/csv;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `bulk-email-failed-${bulkProgress.batchId || "batch"}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

onBeforeUnmount(stopBulkPolling);

// --- Bulk-composer test-mail step (verifier chips) ---
function focusBulkTestInput() {
  bulkTestInputRef.value?.focus();
}

function onBulkTestEmailInput() {
  clearTimeout(bulkTestSuggestTimeout);
  const query = (bulkTestQuery.value || "").trim();
  if (query.length < 2) {
    bulkTestSuggestions.value = [];
    return;
  }
  bulkTestSuggestTimeout = setTimeout(async () => {
    try {
      bulkTestSuggestions.value = await searchUsers(query);
    } catch {
      bulkTestSuggestions.value = [];
    }
  }, 300);
}

function selectBulkTestUser(user) {
  _addTestRecipient(
    bulkEmail.testRecipients,
    user.email || user.name,
    user.full_name || user.name
  );
  bulkTestQuery.value = "";
  bulkTestSuggestions.value = [];
  bulkTestInputRef.value?.focus();
}

function addBulkTestFromInput() {
  if (!bulkTestQuery.value || !bulkTestQuery.value.trim()) return;
  _addTestRecipientsFromText(bulkEmail.testRecipients, bulkTestQuery.value);
  bulkTestQuery.value = "";
  bulkTestSuggestions.value = [];
}

function onBulkTestKeydown(e) {
  if (e.key === "," || e.key === ";") {
    e.preventDefault();
    addBulkTestFromInput();
  } else if (
    e.key === "Backspace" &&
    !bulkTestQuery.value &&
    bulkEmail.testRecipients.length
  ) {
    bulkEmail.testRecipients.pop();
  }
}

function onBulkTestPaste(e) {
  const text = (e.clipboardData || window.clipboardData)?.getData("text") || "";
  if (!/[\s,;]/.test(text)) return; // single token — let default paste + typeahead work
  e.preventDefault();
  _addTestRecipientsFromText(bulkEmail.testRecipients, text);
  bulkTestQuery.value = "";
  bulkTestSuggestions.value = [];
}

function removeBulkTestRecipient(email) {
  bulkEmail.testRecipients = bulkEmail.testRecipients.filter(
    (r) => r.email !== email
  );
}

// Send ONE test copy of the bulk email, rendered with the FIRST recipient's real
// merge data (first group), to one or more verifiers — no tickets are created.
// Unlocks the real "Send to N".
async function sendBulkTestEmail() {
  bulkEmailError.value = "";
  const groups = bulkEmailGroups.value;
  if (!groups.length) {
    bulkFail("Add at least one recipient before sending a test.");
    return;
  }
  if (!bulkEmail.subject.trim()) {
    bulkFail("Subject is required before sending a test.");
    return;
  }
  if (!bulkEmail.ticket_type) {
    bulkFail("Ticket Type is required before sending a test.");
    return;
  }
  if (!bulkEmail.message.trim()) {
    bulkFail("Message is required before sending a test.");
    return;
  }
  // Fold in any half-typed address still in the input.
  if (bulkTestQuery.value.trim()) addBulkTestFromInput();
  const emails = bulkEmail.testRecipients.map((r) => r.email);
  if (!emails.length) {
    bulkFail("Add at least one valid verifier email for the test copy.");
    return;
  }
  bulkTestSending.value = true;
  bulkTestSuggestions.value = [];
  try {
    await call("helpdesk.api.unity_helpdesk_ext.send_test_email", {
      subject: bulkEmail.subject,
      message: bulkEmail.message,
      test_email: emails.join(", "),
      ticket_type: bulkEmail.ticket_type,
      groups: JSON.stringify(groups),
      attachments: JSON.stringify(
        bulkEmail.attachments.map((attachment) => attachment.name)
      ),
    });
    bulkEmail.testSent = true;
    showGlobalNotice(
      `Test sent to ${bulkEmail.testRecipients
        .map((r) => r.name)
        .join(", ")} — check the inbox, then Send to all.`,
      "success",
      8000
    );
  } catch (err) {
    bulkFail("Test send failed: " + (err?.message || err));
  } finally {
    bulkTestSending.value = false;
  }
}

// Re-require a fresh test whenever the content or recipients change — a test that
// was verified for the old copy no longer proves the new one is correct.
watch(
  () => [
    composer.subject,
    composer.message,
    composer.raised_by,
    composer.ticket_type,
    composer.attachments.length,
  ],
  () => {
    if (composer.testSent) composer.testSent = false;
  }
);
watch(
  () => [
    bulkEmail.subject,
    bulkEmail.message,
    bulkEmail.ticket_type,
    bulkEmail.attachments.length,
    bulkEmailStudentCount.value,
    bulkEmailGroups.value[0]?.student,
    bulkEmailGroups.value[0]?.emails?.[0],
  ],
  () => {
    if (bulkEmail.testSent) bulkEmail.testSent = false;
  }
);
</script>
