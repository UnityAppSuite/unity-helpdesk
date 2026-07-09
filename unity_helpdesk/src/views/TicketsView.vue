<template>
  <section class="page">
    <div class="toolbar">
      <div class="filter-group" :class="{ open: filtersOpen }">
        <select v-model="filters.status" @change="applyFiltersAndReload">
          <option value="">Status: All</option>
          <option>Open</option>
          <option>Replied</option>
          <option>On Hold</option>
          <option>Resolved</option>
          <option>Closed</option>
        </select>
        <select v-model="filters.priority" @change="applyFiltersAndReload">
          <option value="">Priority: All</option>
          <option>High</option>
          <option>Medium</option>
          <option>Low</option>
        </select>
        <select v-model="filters.ticket_type" @change="applyFiltersAndReload">
          <option value="">Ticket Type: All</option>
          <option
            v-for="type in ticketTypes"
            :key="type.name"
            :value="type.name"
          >
            {{ type.name }}
          </option>
        </select>
        <!-- In My Tickets view the backend already filters to the current user — hide Assigned filter -->
        <select
          v-if="props.view === 'all'"
          v-model="filters.assigned_to"
          @change="applyFiltersAndReload"
        >
          <option value="">Assigned: All</option>
          <option value="Unassigned">Unassigned</option>
          <option v-for="agent in agents" :key="agent.name" :value="agent.name">
            {{ agent.full_name || agent.name }}
          </option>
        </select>
        <span v-else class="badge blue">Assigned to me</span>
        <div ref="dateRangeRef" class="date-range-trigger">
          <button
            type="button"
            class="date-range-btn"
            :class="{ 'has-value': filters.created_from || filters.created_to }"
            :title="dateRangeLabel"
            @click="toggleDateRange"
          >
            <span class="date-range-icon" aria-hidden="true">📅</span>
            <span class="date-range-label">{{ dateRangeLabel }}</span>
          </button>
          <div v-if="dateRangeOpen" class="date-range-pop" @click.stop>
            <div class="date-range-pop-row">
              <label class="date-range-field">
                <span>From</span>
                <input
                  v-model="dateRangeDraft.from"
                  type="date"
                  :max="dateRangeDraft.to || undefined"
                />
              </label>
              <label class="date-range-field">
                <span>To</span>
                <input
                  v-model="dateRangeDraft.to"
                  type="date"
                  :min="dateRangeDraft.from || undefined"
                />
              </label>
            </div>
            <div class="date-range-pop-actions">
              <button
                type="button"
                class="btn secondary"
                @click="clearDateRange"
              >
                Clear
              </button>
              <button type="button" class="btn" @click="applyDateRange">
                Apply
              </button>
            </div>
          </div>
        </div>
      </div>
      <div class="search-wrapper">
        <span class="search-icon">🔍</span>
        <input
          ref="searchInput"
          v-model="draftSearch"
          class="search"
          type="text"
          placeholder="Ticket ID, student, ref no., email, subject, mail body…"
          aria-label="Search tickets (press Ctrl+K to focus)"
          autocomplete="off"
          @keydown.enter.prevent="onSearchEnter"
          @keydown.down.prevent="moveSuggestion(1)"
          @keydown.up.prevent="moveSuggestion(-1)"
          @keydown.esc="closeSuggestions"
          @focus="searchFocused = true"
          @blur="onSearchBlur"
        />
        <kbd
          v-if="!draftSearch && !searchFocused"
          class="search-shortcut"
          aria-hidden="true"
          >{{ shortcutLabel }}</kbd
        >
        <span
          v-if="loading && appliedSearch"
          class="search-loading"
          title="Searching…"
          >⏳</span
        >
        <button
          v-if="draftSearch"
          class="search-clear"
          type="button"
          title="Clear search"
          @click="clearSearch"
        >
          ✕
        </button>
        <ul
          v-if="searchFocused && !draftSearch.trim() && recentSearches.length"
          class="recent-searches"
          @mousedown.prevent
        >
          <li class="recent-searches__heading">Recent searches</li>
          <li
            v-for="(item, idx) in recentSearches"
            :key="`${item}-${idx}`"
            class="recent-searches__item"
            @click="useRecentSearch(item)"
          >
            <span class="recent-searches__term">{{ item }}</span>
            <button
              type="button"
              class="recent-searches__remove"
              title="Remove from recent"
              @click.stop="removeRecentSearch(item)"
            >
              ✕
            </button>
          </li>
        </ul>
        <ul
          v-if="showSuggestions"
          class="suggestions"
          role="listbox"
          @mousedown.prevent
        >
          <li
            v-for="(sugg, idx) in suggestions"
            :key="sugg.name"
            class="suggestions__item"
            :class="{ active: idx === suggestionsActiveIdx }"
            role="option"
            :aria-selected="idx === suggestionsActiveIdx"
            @mouseenter="suggestionsActiveIdx = idx"
            @click="selectSuggestion(idx)"
          >
            <div class="suggestions__line">
              <span class="suggestions__id">{{ sugg.name }}</span>
              <span
                class="suggestions__status badge"
                :class="`status-${(sugg.status || '')
                  .toLowerCase()
                  .replace(/\\s+/g, '-')}`"
                >{{ sugg.status }}</span
              >
            </div>
            <div class="suggestions__subject">
              <template
                v-for="(seg, sIdx) in highlightTokens(
                  sugg.subject,
                  appliedSuggestionQuery
                )"
                :key="`${sugg.name}-s-${sIdx}`"
              >
                <mark v-if="seg.mark">{{ seg.text }}</mark>
                <template v-else>{{ seg.text }}</template>
              </template>
            </div>
            <div class="suggestions__meta muted">{{ sugg.raised_by }}</div>
          </li>
          <li
            v-if="!suggestions.length && !suggestionsLoading"
            class="suggestions__empty muted"
          >
            No matches.
          </li>
          <li v-if="suggestionsLoading" class="suggestions__loading muted">
            Searching…
          </li>
        </ul>
      </div>
      <div class="toolbar-actions">
        <button
          type="button"
          class="btn secondary filters-toggle"
          @click="filtersOpen = !filtersOpen"
        >
          Filters<span v-if="activeFilterCount" class="filters-toggle-badge">{{
            activeFilterCount
          }}</span>
        </button>
        <button
          class="btn secondary toolbar-search"
          type="button"
          @click="submitSearch"
        >
          Search
        </button>
        <button
          class="btn secondary toolbar-refresh"
          type="button"
          @click="refreshList"
        >
          Refresh
        </button>
        <button
          class="btn secondary toolbar-columns"
          type="button"
          title="Customize columns"
          @click="openColumnPanel"
        >
          Columns
        </button>
      </div>
    </div>

    <div
      v-if="showColumnPanel"
      class="modal-backdrop"
      @click.self="closeColumnPanel"
    >
      <section class="modal-card column-panel">
        <div class="modal-header">
          <div>
            <strong>Customize columns</strong>
            <span>Show, hide, reorder and resize ticket columns.</span>
          </div>
          <button class="btn secondary" @click="closeColumnPanel">Close</button>
        </div>
        <div class="modal-body stack">
          <ol class="column-list">
            <li
              v-for="(col, index) in draftColumns"
              :key="col.key"
              class="column-list-item"
              draggable="true"
              :class="{ dragging: dragIndex === index }"
              @dragstart="onDragStart(index)"
              @dragover="onDragOver($event, index)"
              @dragend="onDragEnd"
            >
              <span class="drag-handle" title="Drag to reorder">⠿</span>
              <label class="column-list-toggle">
                <input
                  type="checkbox"
                  :checked="col.visible"
                  :disabled="col.fixed"
                  @change="toggleDraftColumn(index)"
                />
                <span>{{ col.label }}</span>
                <small v-if="col.fixed" class="muted">(always shown)</small>
              </label>
              <select
                class="column-list-width"
                :value="pxToScale(col.width)"
                title="Column width: 1 = narrow, 10 = very wide"
                @change="col.width = scaleToPx($event.target.value)"
              >
                <option v-for="step in 10" :key="step" :value="step">
                  {{ step }}
                </option>
              </select>
            </li>
          </ol>
        </div>
        <div class="modal-footer">
          <button
            type="button"
            class="btn secondary"
            @click="resetDraftColumns"
          >
            Reset to defaults
          </button>
          <button
            class="btn"
            :disabled="savingColumns"
            @click="saveColumnPreferences"
          >
            {{ savingColumns ? "Saving…" : "Save" }}
          </button>
        </div>
      </section>
    </div>

    <div
      v-if="bulkModalOpen"
      class="modal-backdrop"
      @click.self="closeBulkModal"
    >
      <section class="modal-card bulk-modal">
        <div class="modal-header">
          <div>
            <strong
              >Bulk edit {{ selectionCount }} ticket{{
                selectionCount === 1 ? "" : "s"
              }}</strong
            >
            <span>Apply one field change to every selected ticket.</span>
          </div>
          <button class="btn secondary" @click="closeBulkModal">Close</button>
        </div>
        <div class="modal-body stack">
          <label class="bulk-field">
            <span>Field</span>
            <select v-model="bulkField" @change="onBulkFieldChange">
              <option
                v-for="(label, key) in BULK_FIELD_LABELS"
                :key="key"
                :value="key"
              >
                {{ label }}
              </option>
            </select>
          </label>
          <label class="bulk-field">
            <span>New value</span>
            <select v-if="bulkField === 'status'" v-model="bulkValue">
              <option value="Open">Open</option>
              <option value="Replied">Replied</option>
              <option value="Resolved">Resolved</option>
              <option value="Closed">Closed</option>
              <option value="On Hold">On Hold</option>
            </select>
            <select v-else-if="bulkField === 'priority'" v-model="bulkValue">
              <option value="">— Clear —</option>
              <option value="High">High</option>
              <option value="Medium">Medium</option>
              <option value="Low">Low</option>
            </select>
            <select v-else-if="bulkField === '_assign'" v-model="bulkValue">
              <option value="">— Unassign —</option>
              <option
                v-for="agent in agents"
                :key="agent.name"
                :value="agent.user || agent.email || agent.name"
              >
                {{
                  agent.full_name ||
                  agent.agent_name ||
                  agent.user ||
                  agent.name
                }}
              </option>
            </select>
            <select v-else-if="bulkField === 'ticket_type'" v-model="bulkValue">
              <option value="">— Clear —</option>
              <option
                v-for="type in ticketTypes"
                :key="type.name"
                :value="type.name"
              >
                {{ type.name }}
              </option>
            </select>
            <select v-else-if="bulkField === 'agent_group'" v-model="bulkValue">
              <option value="">— Clear —</option>
              <option
                v-for="grp in agentGroups"
                :key="grp.name"
                :value="grp.name"
              >
                {{ grp.name }}
              </option>
            </select>
          </label>
          <p v-if="bulkField === '_assign' && bulkValue" class="bulk-note">
            Assignee will be <strong>replaced</strong> on each selected ticket
            (existing assignees are cleared first).
          </p>
          <p
            v-if="bulkResult && bulkResult.failed && bulkResult.failed.length"
            class="bulk-error"
          >
            {{ bulkResult.failed.length }} ticket{{
              bulkResult.failed.length === 1 ? "" : "s"
            }}
            failed ({{ bulkResult.updated.length }} updated). First failure:
            <em>{{ bulkResult.failed[0].name }}</em
            >{{
              bulkResult.failed[0].reason
                ? ` — ${bulkResult.failed[0].reason}`
                : ""
            }}
          </p>
          <div v-if="bulkSaving" class="bulk-progress">
            <div class="bulk-progress-bar">
              <div
                class="bulk-progress-fill"
                :style="{
                  width:
                    (bulkProgress.total
                      ? (bulkProgress.done / bulkProgress.total) * 100
                      : 0) + '%',
                }"
              ></div>
            </div>
            <span class="bulk-progress-label">
              {{ bulkProgress.done }} / {{ bulkProgress.total }} applied
            </span>
          </div>
        </div>
        <div class="modal-footer">
          <button
            type="button"
            class="btn secondary"
            :disabled="bulkSaving"
            @click="closeBulkModal"
          >
            Cancel
          </button>
          <button
            class="btn"
            :disabled="bulkSaving || !selectionCount"
            @click="applyBulkUpdate"
          >
            {{
              bulkSaving
                ? `Applying ${bulkProgress.done}/${bulkProgress.total}…`
                : `Apply to ${selectionCount}`
            }}
          </button>
        </div>
      </section>
    </div>

    <div class="metrics" :class="{ 'metrics-stale': showFilteringBanner }">
      <div class="metric">
        <b>{{
          showFilteringBanner || summaryPending ? "…" : result.total_count || 0
        }}</b>
        <span>Total Tickets</span>
      </div>
      <div class="metric">
        <b>{{
          showFilteringBanner || summaryPending ? "…" : cards.pending || 0
        }}</b>
        <span>Pending</span>
      </div>
      <div class="metric">
        <b>{{
          showFilteringBanner || summaryPending ? "…" : cards.on_hold || 0
        }}</b>
        <span>On Hold</span>
      </div>
      <div class="metric">
        <b>{{
          showFilteringBanner || summaryPending ? "…" : cards.resolved || 0
        }}</b>
        <span>Resolved</span>
      </div>
      <div class="metric">
        <b>{{
          showFilteringBanner || summaryPending ? "…" : cards.closed || 0
        }}</b>
        <span>Closed</span>
      </div>
    </div>

    <div class="table-shell">
      <div class="table-header">
        <strong>{{ title }}</strong>
        <span>{{
          showFilteringBanner || summaryPending
            ? "…"
            : `${result.total_count || 0} tickets`
        }}</span>
      </div>
      <div v-if="selectionCount > 0" class="bulk-action-bar">
        <span class="bulk-action-count">
          <strong>{{ selectionCount }}</strong> selected
        </span>
        <button type="button" class="btn" @click="openBulkModal">
          Bulk edit
        </button>
        <button type="button" class="btn secondary" @click="clearSelection">
          Clear
        </button>
      </div>
      <div
        v-if="showFilteringBanner && tickets.length"
        class="filtering-banner"
      >
        <span class="filtering-spinner" aria-hidden="true"></span>
        <span
          >Filtering tickets… results below are from the previous request.</span
        >
      </div>
      <div v-if="reloadPrompt" class="reload-prompt">
        <span>Couldn't load tickets.</span>
        <button type="button" class="btn secondary" @click="load()">
          Retry
        </button>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
      <!-- First-paint skeleton: shown only while the very first page-load query
           is in flight. Replaces the bare "Searching…" text that previously
           sat unchanged for the full 30 s timeout, making slow loads look
           like a frozen UI. We render the skeleton for empty-search loads;
           a search keystroke keeps the existing inline "Searching…" hint. -->
      <div
        v-else-if="loading && !tickets.length && !appliedSearch"
        class="skeleton-rows"
        aria-busy="true"
        aria-label="Loading tickets"
      >
        <div v-for="i in 5" :key="i" class="skeleton-row">
          <span /><span /><span /><span /><span /><span /><span />
        </div>
      </div>
      <p v-else-if="loading && !tickets.length" class="empty">Searching…</p>
      <p v-else-if="!tickets.length && appliedSearch" class="empty">
        No tickets found for <strong>{{ activeFilterSummary }}</strong> — try a
        shorter or different term.
      </p>
      <p v-else-if="!tickets.length" class="empty">{{ emptyMessage }}</p>
      <div
        v-else
        class="scroll-x"
        :class="{ 'table-dimmed': showFilteringBanner }"
      >
        <table class="ticket-table">
          <thead>
            <tr>
              <th
                class="checkbox-cell"
                :title="
                  allOnPageSelected ? 'Clear all on page' : 'Select all on page'
                "
              >
                <input
                  type="checkbox"
                  :checked="allOnPageSelected"
                  @click.stop="toggleAllOnPage"
                />
              </th>
              <th
                v-for="(col, colIdx) in visibleColumns"
                :key="col.key"
                :style="{ width: col.width + 'px', minWidth: col.width + 'px' }"
                :class="{
                  'col-dragging': colDragIdx === colIdx,
                  'col-draggable': !col.fixed,
                }"
                :draggable="!col.fixed"
                @dragstart="onColDragStart($event, colIdx)"
                @dragover="onColDragOver($event, colIdx)"
                @dragend="onColDragEnd"
                @drop.prevent
              >
                <span v-if="!col.fixed" class="col-drag-handle">⠿</span>
                {{ col.label }}
                <button
                  v-if="!col.fixed"
                  class="col-remove-btn"
                  title="Remove column"
                  type="button"
                  @click.stop="removeColumn(col.key)"
                >
                  ×
                </button>
                <span
                  v-if="!col.fixed"
                  class="col-resize-grabber"
                  title="Drag to resize"
                  @mousedown="startColumnResize($event, col)"
                ></span>
              </th>
              <!-- Add Column -->
              <th class="col-add-th">
                <div class="col-add-wrap">
                  <button
                    class="col-add-btn"
                    type="button"
                    title="Add column"
                    @click.stop="toggleAddColumnMenu"
                  >
                    +
                  </button>
                  <div v-if="showAddCol" class="col-add-dropdown">
                    <button
                      v-for="c in hiddenColumns"
                      :key="c.key"
                      class="col-add-item"
                      type="button"
                      @click.stop="addColumn(c.key)"
                    >
                      {{ c.label }}
                    </button>
                    <span v-if="!hiddenColumns.length" class="col-add-empty"
                      >All columns shown</span
                    >
                  </div>
                </div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="ticket in tickets"
              :key="ticket.name"
              :class="{
                'portal-ticket':
                  ticket.custom_via_unity_portal &&
                  !ticket.custom_is_bulk_email,
                'bulk-email-ticket': ticket.custom_is_bulk_email,
                'row-selected': isSelected(ticket.name),
              }"
              @click="openTicket(ticket.name, $event)"
              @auxclick.middle="openTicket(ticket.name, $event)"
            >
              <td class="checkbox-cell" @click.stop>
                <input
                  type="checkbox"
                  :checked="isSelected(ticket.name)"
                  @click.stop="toggleRow(ticket.name)"
                />
              </td>
              <td
                v-for="col in visibleColumns"
                :key="col.key"
                :class="{ 'cell-edit': isEditableColumn(col.key) }"
                :style="{
                  minWidth: col.width + 'px',
                  maxWidth: col.width + 'px',
                  width: col.width + 'px',
                }"
                @click="onCellClick($event, col.key)"
              >
                <template v-if="col.key === 'name'">
                  <button
                    class="link-btn"
                    type="button"
                    @click.stop="openTicket(ticket.name, $event)"
                    @auxclick.middle.stop="openTicket(ticket.name, $event)"
                  >
                    #{{ ticket.name }}
                  </button>
                </template>
                <template v-else-if="col.key === 'subject'">
                  <div class="subject">
                    {{ ticket.subject || "No subject" }}
                  </div>
                  <small class="muted">{{ ticket.raised_by }}</small>
                  <small
                    v-if="ticket.custom_search_student_names"
                    class="student-names"
                  >
                    {{ ticket.custom_search_student_names }}
                  </small>
                </template>
                <template v-else-if="col.key === 'summary'">
                  <div class="summary-cell">
                    <div class="summary-top">
                      <span class="summary-subject">{{
                        ticket.subject || "No subject"
                      }}</span>
                      <span
                        v-if="ticket.ticket_type"
                        class="badge summary-type"
                        :class="
                          !ticketTypeStyle(ticket.ticket_type) &&
                          ticketTypeClass(ticket.ticket_type)
                        "
                        :style="ticketTypeStyle(ticket.ticket_type) || null"
                        >{{ ticket.ticket_type }}</span
                      >
                    </div>
                    <div class="summary-meta">
                      <span
                        class="badge"
                        :class="ticket.status_indicator?.color"
                        >{{
                          ticket.status_indicator?.label || ticket.status
                        }}</span
                      >
                      <span class="muted">{{
                        ticket.assignee?.full_name ||
                        ticket.assignee?.name ||
                        "Unassigned"
                      }}</span>
                      <span class="muted">{{
                        formatDateTime(ticket.creation)
                      }}</span>
                    </div>
                  </div>
                </template>
                <template v-else-if="col.key === 'ticket_type'">
                  <select
                    v-model="editState[ticket.name].ticket_type"
                    :class="[
                      'select-chip',
                      !ticketTypeStyle(editState[ticket.name].ticket_type) &&
                        ticketTypeClass(editState[ticket.name].ticket_type),
                    ]"
                    :style="
                      ticketTypeStyle(editState[ticket.name].ticket_type) ||
                      null
                    "
                    :disabled="isSaving(ticket.name)"
                    @change="
                      quickUpdate(
                        ticket,
                        'ticket_type',
                        editState[ticket.name].ticket_type
                      )
                    "
                  >
                    <option value="">Not set</option>
                    <!-- Ensure current value is always an option even while ticketTypes loads -->
                    <option
                      v-if="
                        editState[ticket.name].ticket_type &&
                        !ticketTypes.find(
                          (t) => t.name === editState[ticket.name].ticket_type
                        )
                      "
                      :value="editState[ticket.name].ticket_type"
                    >
                      {{ editState[ticket.name].ticket_type }}
                    </option>
                    <option
                      v-for="type in ticketTypes"
                      :key="type.name"
                      :value="type.name"
                    >
                      {{ type.name }}
                    </option>
                  </select>
                </template>
                <template v-else-if="col.key === 'priority'">
                  <select
                    v-model="editState[ticket.name].priority"
                    :class="[
                      'select-chip',
                      priorityClass(editState[ticket.name].priority),
                    ]"
                    :disabled="isSaving(ticket.name)"
                    @change="
                      quickUpdate(
                        ticket,
                        'priority',
                        editState[ticket.name].priority
                      )
                    "
                  >
                    <option value="">Not set</option>
                    <option>High</option>
                    <option>Medium</option>
                    <option>Low</option>
                  </select>
                </template>
                <template v-else-if="col.key === 'status'">
                  <select
                    v-model="editState[ticket.name].status"
                    :class="[
                      'select-chip',
                      statusClass(ticket, editState[ticket.name].status),
                    ]"
                    :disabled="isSaving(ticket.name)"
                    @change="
                      quickUpdate(
                        ticket,
                        'status',
                        editState[ticket.name].status
                      )
                    "
                  >
                    <option>On Hold</option>
                    <option>Open</option>
                    <option>Replied</option>
                    <option>Resolved</option>
                    <option>Closed</option>
                  </select>
                </template>
                <template v-else-if="col.key === '_assign'">
                  <!-- Opens a searchable dropdown (teleported to body so it escapes
                       the scroll container's clipping). -->
                  <button
                    type="button"
                    class="select-chip assign-trigger"
                    :class="assignmentClass(editState[ticket.name].assignee)"
                    :disabled="isSaving(ticket.name)"
                    @click.stop="openAssign(ticket, $event)"
                  >
                    <span class="assign-trigger-label">{{
                      assigneeLabel(editState[ticket.name].assignee) ||
                      "Unassigned"
                    }}</span>
                    <span class="assign-caret" aria-hidden="true">▾</span>
                  </button>
                </template>
                <template v-else-if="col.key === 'creation'">
                  {{ formatDateTime(ticket.creation) }}
                </template>
                <template v-else-if="col.key === 'owner'">
                  <span
                    v-if="ticket.created_by || ticket.owner"
                    :title="ticket.created_by?.email || ticket.owner"
                  >
                    {{ ticket.created_by?.full_name || ticket.owner }}
                  </span>
                  <span v-else class="muted">-</span>
                </template>
                <template v-else-if="col.key === 'custom_is_on_hold'">
                  <span v-if="ticket.custom_is_on_hold">
                    {{ formatHoldWindow(ticket) }}
                  </span>
                  <span v-else class="muted">-</span>
                </template>
                <template v-else-if="col.key === 'custom_hold_reason'">
                  <input
                    v-model="editState[ticket.name].hold_reason"
                    class="table-input"
                    type="text"
                    :disabled="isSaving(ticket.name)"
                    placeholder="Add hold reason"
                    @blur="saveHoldReason(ticket)"
                    @keyup.enter="saveHoldReason(ticket)"
                  />
                </template>
                <template v-else-if="col.key === 'custom_primary_message_text'">
                  <span
                    v-if="ticket.custom_primary_message_text"
                    class="cell-mail-body"
                    :title="ticket.custom_primary_message_text"
                  >
                    {{ truncateBody(ticket.custom_primary_message_text) }}
                  </span>
                  <span v-else class="muted">-</span>
                </template>
                <template v-else>
                  <span>{{ formatCellValue(ticket, col.key) }}</span>
                </template>
              </td>
              <td class="col-add-td"></td>
            </tr>
          </tbody>
        </table>
        <!-- Searchable "Assigned To" dropdown, teleported to <body> so the table's
             overflow can never clip it. Only one is open at a time. -->
        <Teleport to="body">
          <template v-if="assignOpen">
            <div class="assign-popover-backdrop" @click="closeAssign"></div>
            <div
              ref="assignPopoverRef"
              class="assign-popover"
              :style="assignPopoverStyle"
            >
              <input
                ref="assignSearchRef"
                v-model="assignQuery"
                class="assign-search"
                type="text"
                placeholder="Search agent…"
                autocomplete="off"
                @keydown.escape="closeAssign"
              />
              <ul class="assign-options">
                <li class="assign-option" @click="pickAssign('')">
                  Unassigned
                </li>
                <li
                  v-for="agent in assignMatches"
                  :key="agent.name"
                  class="assign-option"
                  @click="pickAssign(agent.name)"
                >
                  {{ agentDisplay(agent) || agent.name }}
                </li>
                <li v-if="!assignMatches.length" class="assign-option disabled">
                  No agents match
                </li>
              </ul>
            </div>
          </template>
        </Teleport>
        <!-- Mobile-only: compact summary cards (the wide table is hidden ≤640px) -->
        <div class="ticket-cards">
          <article
            v-for="ticket in tickets"
            :key="`card-${ticket.name}`"
            class="ticket-card"
            :class="{
              'portal-ticket':
                ticket.custom_via_unity_portal && !ticket.custom_is_bulk_email,
              'bulk-email-ticket': ticket.custom_is_bulk_email,
              'row-selected': isSelected(ticket.name),
            }"
            @click="openTicket(ticket.name, $event)"
            @auxclick.middle="openTicket(ticket.name, $event)"
          >
            <div class="ticket-card-top">
              <label class="ticket-card-check" @click.stop>
                <input
                  type="checkbox"
                  :checked="isSelected(ticket.name)"
                  @click.stop="toggleRow(ticket.name)"
                />
              </label>
              <span class="ticket-card-id">#{{ ticket.name }}</span>
              <span class="badge" :class="ticket.status_indicator?.color">{{
                ticket.status_indicator?.label || ticket.status
              }}</span>
            </div>
            <div class="ticket-card-subject">
              {{ ticket.subject || "No subject" }}
            </div>
            <div class="ticket-card-meta">
              <span
                v-if="ticket.ticket_type"
                class="badge"
                :class="
                  !ticketTypeStyle(ticket.ticket_type) &&
                  ticketTypeClass(ticket.ticket_type)
                "
                :style="ticketTypeStyle(ticket.ticket_type) || null"
                >{{ ticket.ticket_type }}</span
              >
              <span class="muted">{{
                ticket.assignee?.full_name ||
                ticket.assignee?.name ||
                "Unassigned"
              }}</span>
              <span class="muted">{{ formatDateTime(ticket.creation) }}</span>
            </div>
            <small
              v-if="ticket.custom_search_student_names"
              class="student-names"
              >{{ ticket.custom_search_student_names }}</small
            >
          </article>
        </div>
      </div>
      <div class="table-header">
        <label class="page-size-control" title="Rows fetched per request">
          Rows per page
          <select v-model.number="result.page_length" @change="reload">
            <option v-for="size in PAGE_SIZE_OPTIONS" :key="size" :value="size">
              {{ size }}
            </option>
          </select>
        </label>
        <span
          >Showing {{ tickets.length }} of {{ result.total_count || 0 }}</span
        >
        <button v-if="canLoadMore" class="btn secondary" @click="loadMore">
          Load more
        </button>
      </div>
    </div>
  </section>
</template>

<script setup>
import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  AuthRedirectError,
  bulkUpdateTickets,
  call,
  callWithRetry,
  formatDate,
  formatDateTime,
  getAgents,
  getTicketTypes,
  listAgentGroups,
} from "../api";

const props = defineProps({ view: { type: String, default: "my" } });
const emit = defineEmits(["title"]);
const route = useRoute();
const router = useRouter();
const unitySession = inject("unitySession", null);
const refreshUnitySession = inject("refreshUnitySession", null);
// Agents + ticket types are loaded once at the app level — reuse those refs
// instead of issuing duplicate get_agents / get_ticket_types calls per view.
const injectedAgents = inject("unityAgents", null);
const injectedTicketTypes = inject("unityTicketTypes", null);
// Bumped by App.vue when a non-blocking send finishes — pull the new ticket in.
const ticketsRefreshSignal = inject("unityTicketsRefresh", null);
if (ticketsRefreshSignal) {
  watch(
    () => ticketsRefreshSignal.value,
    () => {
      reload();
    }
  );
}

const draftSearch = ref("");
const appliedSearch = ref("");
const loading = ref(false);
const loadingMore = ref(false);
const reloading = ref(false);
const reloadPrompt = ref(false);
const error = ref("");
const emptyMessage = ref("No tickets found.");
const agents = ref([]);
const ticketTypes = ref([]);
const rowSaving = reactive({});
const editState = reactive({});
const PAGE_SIZE_OPTIONS = [20, 50, 100, 500];
const PAGE_SIZE_STORAGE_KEY = "unity_helpdesk_page_size";

function _initialPageSize() {
  // Always start at the smallest page size on load/reload so the first paint is
  // fast and never inherits a large page (e.g. 500) that slows the first render.
  // Changing "Rows per page" to 500 still works for the rest of the session (it
  // refetches immediately); it just resets to 20 on the next reload/remount.
  return 20;
}

const result = reactive({
  data: [],
  total_count: 0,
  cards: {},
  start: 0,
  page_length: _initialPageSize(),
});

watch(
  () => result.page_length,
  (size) => {
    try {
      window.localStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(size));
    } catch {
      // Quota / private mode — non-fatal.
    }
  }
);
// Bulk-edit selection + dialog state.
const selectedIds = ref(new Set());
const bulkModalOpen = ref(false);
const bulkField = ref("status");
const bulkValue = ref("");
const bulkSaving = ref(false);
const bulkResult = ref(null); // { updated: [...], failed: [...] }
const bulkProgress = ref({ done: 0, total: 0 });
const BULK_CHUNK_SIZE = 100;
const agentGroups = ref([]);
const BULK_FIELD_LABELS = {
  status: "Status",
  priority: "Priority",
  _assign: "Assignee",
  ticket_type: "Ticket Type",
  agent_group: "Agent Group",
};
const dateRangeOpen = ref(false);
const dateRangeRef = ref(null);
const dateRangeDraft = reactive({ from: "", to: "" });
// Debounced "is something loading" flag for the table-dim + filtering banner.
// Goes true only after ~120ms of continuous loading so quick (<120ms) loads
// don't flicker the UI on top of fast post-index responses.
const showFilteringBanner = ref(false);
let filteringBannerTimer = null;
// True from request start until the get_tickets_summary response lands.
// Drives "…" placeholders on the KPI cards so the user doesn't stare at
// stale sessionStorage-cached numbers while the dashboard aggregate
// refreshes in the background. Independent of the row-skeleton `loading`
// flag because rows usually render seconds before cards.
const summaryPending = ref(false);
const filters = reactive({
  status: "",
  priority: "",
  ticket_type: "",
  assigned_to: "",
  created_from: "",
  created_to: "",
});
// Mobile: the filter row collapses behind a "Filters" toggle. On desktop the
// filter group is always shown via CSS (display:contents), so this only gates mobile.
const filtersOpen = ref(false);
const activeFilterCount = computed(
  () =>
    [
      filters.status,
      filters.priority,
      filters.ticket_type,
      filters.assigned_to,
      filters.created_from || filters.created_to,
    ].filter(Boolean).length
);
let activeController = null;
let activeRequestId = 0;

// --- Column customization ---
const showColumnPanel = ref(false);
const draftColumns = ref([]); // popover working copy: [{ key, visible, width }]
const savingColumns = ref(false);

const availableColumns = computed(() => unitySession?.available_columns || []);
const availableColumnMap = computed(() => {
  const map = {};
  for (const col of availableColumns.value) map[col.key] = col;
  return map;
});
const visibleColumns = computed(() => {
  const prefs =
    (unitySession?.settings?.column_preferences || []).filter(
      (p) => availableColumnMap.value[p.key]
    ) || [];
  return prefs.map((p) => ({
    ...availableColumnMap.value[p.key],
    width: p.width,
  }));
});

function buildDraftColumns() {
  const prefs = unitySession?.settings?.column_preferences || [];
  const selectedSet = new Set(prefs.map((p) => p.key));
  // Selected first (preserves user order), unselected appended at the bottom.
  const draft = prefs
    .map((p) => {
      const def = availableColumnMap.value[p.key];
      if (!def) return null;
      return {
        key: p.key,
        label: def.label,
        fixed: def.fixed,
        width: p.width || def.width || 140,
        visible: true,
      };
    })
    .filter(Boolean);
  for (const col of availableColumns.value) {
    if (selectedSet.has(col.key)) continue;
    draft.push({
      key: col.key,
      label: col.label,
      fixed: col.fixed,
      width: col.width || 140,
      visible: false,
    });
  }
  return draft;
}

function openColumnPanel() {
  draftColumns.value = buildDraftColumns();
  showColumnPanel.value = true;
}

function closeColumnPanel() {
  showColumnPanel.value = false;
}

const dragIndex = ref(null);

function onDragStart(index) {
  dragIndex.value = index;
}
function onDragOver(e, index) {
  e.preventDefault();
  if (dragIndex.value === null || dragIndex.value === index) return;
  const arr = draftColumns.value.slice();
  const [item] = arr.splice(dragIndex.value, 1);
  arr.splice(index, 0, item);
  draftColumns.value = arr;
  dragIndex.value = index;
}
function onDragEnd() {
  dragIndex.value = null;
}

function toggleDraftColumn(index) {
  const col = draftColumns.value[index];
  if (col.fixed) return;
  draftColumns.value = draftColumns.value.map((c, i) =>
    i === index ? { ...c, visible: !c.visible } : c
  );
}

function resetDraftColumns() {
  draftColumns.value = (availableColumns.value || []).map((col) => ({
    key: col.key,
    label: col.label,
    fixed: col.fixed,
    width: col.width || 140,
    visible: !!col.default,
  }));
}

async function saveColumnPreferences() {
  const payload = draftColumns.value
    .filter((c) => c.visible)
    .map((c) => ({ key: c.key, width: clampColumnWidth(c.width) }));
  savingColumns.value = true;
  try {
    await call("helpdesk.api.unity_helpdesk.update_column_preferences", {
      column_preferences: JSON.stringify(payload),
    });
    if (refreshUnitySession) await refreshUnitySession();
    showColumnPanel.value = false;
    await load();
  } catch (err) {
    error.value = err.message;
  } finally {
    savingColumns.value = false;
  }
}

// Shared helper: persist a raw prefs array and refresh
async function persistColumnPrefs(prefs) {
  try {
    await call("helpdesk.api.unity_helpdesk.update_column_preferences", {
      column_preferences: JSON.stringify(prefs),
    });
    if (refreshUnitySession) await refreshUnitySession();
    await load();
  } catch (err) {
    error.value = err.message;
  }
}

// Inline column management (header drag / remove / add)
const colDragIdx = ref(null);
const showAddCol = ref(false);

const hiddenColumns = computed(() => {
  const visible = new Set(
    (unitySession?.settings?.column_preferences || []).map((p) => p.key)
  );
  return (availableColumns.value || []).filter((c) => !visible.has(c.key));
});

function toggleAddColumnMenu() {
  showAddCol.value = !showAddCol.value;
}

function onColDragStart(e, idx) {
  colDragIdx.value = idx;
  e.dataTransfer.effectAllowed = "move";
}

function onColDragOver(e, idx) {
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  if (colDragIdx.value === null || colDragIdx.value === idx) return;
  const prefs = (unitySession?.settings?.column_preferences || []).slice();
  const [moved] = prefs.splice(colDragIdx.value, 1);
  prefs.splice(idx, 0, moved);
  if (unitySession) unitySession.settings.column_preferences = prefs;
  colDragIdx.value = idx;
}

// Column reorder / add / remove is purely visual for data that's already loaded —
// only a column whose field wasn't in the last fetch actually needs a refetch.
// So apply the change locally (instant) and save prefs in the background, and skip
// the full ticket-list reload unless the new column genuinely needs data. This
// keeps column tweaks snappy instead of round-tripping the whole list every time.
function setColumnPrefsLocal(prefs) {
  if (unitySession) unitySession.settings.column_preferences = prefs;
}

function persistColumnPrefsQuiet(prefs) {
  // Fire-and-forget save; the table already reflects `prefs` locally.
  call("helpdesk.api.unity_helpdesk.update_column_preferences", {
    column_preferences: JSON.stringify(prefs),
  }).catch((err) => {
    error.value = err.message;
  });
}

function columnNeedsFetch(def) {
  // Virtual columns are composed client-side from already-fetched fields.
  if (!def || def.virtual) return false;
  const rows = tickets.value;
  if (!rows.length) return false; // nothing rendered yet — nothing to backfill
  // If the field key is absent from the loaded rows, the backend didn't SELECT it
  // for the current column set, so one reload is needed to fetch it.
  return !(def.key in rows[0]);
}

async function onColDragEnd() {
  colDragIdx.value = null;
  // Reorder never changes which data is needed — persist quietly, no reload.
  persistColumnPrefsQuiet(unitySession?.settings?.column_preferences || []);
}

function removeColumn(key) {
  const prefs = (unitySession?.settings?.column_preferences || []).filter(
    (p) => p.key !== key
  );
  setColumnPrefsLocal(prefs);
  persistColumnPrefsQuiet(prefs); // removing a column never needs new data
}

async function addColumn(key) {
  const def = availableColumnMap.value[key];
  if (!def) return;
  const prefs = [
    ...(unitySession?.settings?.column_preferences || []),
    { key, width: def.width || 140 },
  ];
  showAddCol.value = false;
  setColumnPrefsLocal(prefs); // column shows instantly using already-loaded data
  if (columnNeedsFetch(def)) {
    // Only the few extra-field columns (SLA dates, mail body) hit this.
    await persistColumnPrefs(prefs);
  } else {
    persistColumnPrefsQuiet(prefs);
  }
}

// Close add-column dropdown on outside click
function onDocClick() {
  showAddCol.value = false;
}
onMounted(() => document.addEventListener("click", onDocClick));
onBeforeUnmount(() => document.removeEventListener("click", onDocClick));

// Column widths are stored as pixel values but presented to the user on a
// friendly 1-10 scale. Each scale step maps to a fixed pixel width below.
// Step 10 (1400px) is wide enough for the Mail Body preview to show 1-2
// full lines of text without wrapping; step 1 (80px) is for icon-only columns.
const COLUMN_WIDTH_SCALE_PX = [
  80, 140, 200, 280, 380, 500, 660, 860, 1100, 1400,
];
const COLUMN_WIDTH_MIN_PX = COLUMN_WIDTH_SCALE_PX[0];
const COLUMN_WIDTH_MAX_PX =
  COLUMN_WIDTH_SCALE_PX[COLUMN_WIDTH_SCALE_PX.length - 1];

function pxToScale(width) {
  const px = Number(width) || 0;
  let bestIdx = 0;
  let bestDelta = Infinity;
  for (let i = 0; i < COLUMN_WIDTH_SCALE_PX.length; i += 1) {
    const delta = Math.abs(COLUMN_WIDTH_SCALE_PX[i] - px);
    if (delta < bestDelta) {
      bestDelta = delta;
      bestIdx = i;
    }
  }
  return bestIdx + 1;
}

function scaleToPx(scale) {
  const idx =
    Math.min(
      COLUMN_WIDTH_SCALE_PX.length,
      Math.max(1, Number.parseInt(scale, 10) || 1)
    ) - 1;
  return COLUMN_WIDTH_SCALE_PX[idx];
}

function clampColumnWidth(width) {
  const n = Number.parseInt(width, 10);
  if (Number.isNaN(n)) return 280;
  return Math.min(COLUMN_WIDTH_MAX_PX, Math.max(COLUMN_WIDTH_MIN_PX, n));
}

// Column resize (drag right edge of <th>)
const resizingKey = ref(null);
let resizeStartX = 0;
let resizeStartWidth = 0;

let pendingResizeX = 0;
let resizeRaf = 0;

function startColumnResize(event, col) {
  if (!col || col.fixed === undefined) return;
  resizingKey.value = col.key;
  resizeStartX = event.clientX;
  resizeStartWidth = col.width || 140;
  pendingResizeX = event.clientX;
  document.body.classList.add("col-resizing");
  window.addEventListener("mousemove", handleColumnResize);
  window.addEventListener("mouseup", endColumnResize, { once: true });
  event.preventDefault();
  event.stopPropagation();
}

function applyResizeFrame() {
  resizeRaf = 0;
  if (!resizingKey.value) return;
  const delta = pendingResizeX - resizeStartX;
  const next = clampColumnWidth(resizeStartWidth + delta);
  const prefs = (unitySession?.settings?.column_preferences || []).map((p) =>
    p.key === resizingKey.value ? { ...p, width: next } : p
  );
  if (unitySession) unitySession.settings.column_preferences = prefs;
}

function handleColumnResize(event) {
  if (!resizingKey.value) return;
  pendingResizeX = event.clientX;
  // Coalesce mousemove updates into one paint per frame — keeps the table
  // from re-rendering 100+ rows per pixel of mouse travel.
  if (!resizeRaf) {
    resizeRaf = requestAnimationFrame(applyResizeFrame);
  }
}

function endColumnResize() {
  window.removeEventListener("mousemove", handleColumnResize);
  document.body.classList.remove("col-resizing");
  if (resizeRaf) {
    cancelAnimationFrame(resizeRaf);
    resizeRaf = 0;
    // Flush any final pending position so the released width matches the cursor.
    applyResizeFrame();
  }
  const key = resizingKey.value;
  resizingKey.value = null;
  if (!key) return;
  // Persist new width
  const payload = (unitySession?.settings?.column_preferences || []).map(
    (p) => ({
      key: p.key,
      width: p.width,
    })
  );
  call("helpdesk.api.unity_helpdesk.update_column_preferences", {
    column_preferences: JSON.stringify(payload),
  }).catch(() => {
    /* width is best-effort; ignore transient failures */
  });
}

// --- Search UX helpers ---
const RECENT_SEARCH_KEY = "unity-helpdesk:recent-searches";
const RECENT_SEARCH_LIMIT = 8;
const SUGGESTION_DEBOUNCE_MS = 250;
const SUGGESTION_MIN_LENGTH = 2;
const isMac =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad/.test(navigator.platform);
const shortcutLabel = isMac ? "⌘K" : "Ctrl+K";

const searchInput = ref(null);
const searchFocused = ref(false);
const recentSearches = ref([]);
const suggestions = ref([]);
const suggestionsLoading = ref(false);
const suggestionsActiveIdx = ref(-1);
const appliedSuggestionQuery = ref("");
let suggestionsController = null;
let suggestionsTimer = null;
let suggestionsRequestId = 0;

const showSuggestions = computed(
  () =>
    searchFocused.value &&
    draftSearch.value.trim().length >= SUGGESTION_MIN_LENGTH &&
    (suggestions.value.length > 0 || suggestionsLoading.value)
);

function loadRecentSearches() {
  try {
    const raw = localStorage.getItem(RECENT_SEARCH_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    recentSearches.value = Array.isArray(parsed)
      ? parsed
          .filter((s) => typeof s === "string" && s.trim())
          .slice(0, RECENT_SEARCH_LIMIT)
      : [];
  } catch {
    recentSearches.value = [];
  }
}

function persistRecentSearches() {
  try {
    localStorage.setItem(
      RECENT_SEARCH_KEY,
      JSON.stringify(recentSearches.value)
    );
  } catch {
    /* storage unavailable — ignore */
  }
}

function rememberSearch(term) {
  const value = (term || "").trim();
  if (!value) return;
  const next = [value, ...recentSearches.value.filter((s) => s !== value)];
  recentSearches.value = next.slice(0, RECENT_SEARCH_LIMIT);
  persistRecentSearches();
}

function removeRecentSearch(term) {
  recentSearches.value = recentSearches.value.filter((s) => s !== term);
  persistRecentSearches();
}

function useRecentSearch(term) {
  draftSearch.value = term;
  searchFocused.value = false;
  closeSuggestions();
  submitSearch();
}

// --- Live suggestions ---

function closeSuggestions() {
  if (suggestionsTimer) {
    clearTimeout(suggestionsTimer);
    suggestionsTimer = null;
  }
  suggestionsController?.abort();
  suggestionsController = null;
  suggestions.value = [];
  suggestionsLoading.value = false;
  suggestionsActiveIdx.value = -1;
}

async function fetchSuggestions(query) {
  const requestId = ++suggestionsRequestId;
  suggestionsController?.abort();
  suggestionsController = new AbortController();
  suggestionsLoading.value = true;
  try {
    const data = await call(
      "helpdesk.api.unity_helpdesk.get_ticket_suggestions",
      { search: query, view: props.view },
      { signal: suggestionsController.signal, timeoutMs: 8000 }
    );
    if (requestId !== suggestionsRequestId) return;
    suggestions.value = Array.isArray(data?.data) ? data.data : [];
    appliedSuggestionQuery.value = query;
    suggestionsActiveIdx.value = suggestions.value.length ? 0 : -1;
  } catch (err) {
    if (requestId !== suggestionsRequestId) return;
    // Aborted / timed-out keystrokes are not user-visible errors.
    if (err?.code !== "REQUEST_ABORTED" && err?.code !== "REQUEST_TIMEOUT") {
      console.warn("[unity-helpdesk] suggestion fetch failed:", err);
    }
    suggestions.value = [];
    suggestionsActiveIdx.value = -1;
  } finally {
    if (requestId === suggestionsRequestId) {
      suggestionsLoading.value = false;
    }
  }
}

watch(draftSearch, (next) => {
  const q = (next || "").trim();
  if (suggestionsTimer) {
    clearTimeout(suggestionsTimer);
    suggestionsTimer = null;
  }
  if (q.length < SUGGESTION_MIN_LENGTH) {
    suggestionsController?.abort();
    suggestions.value = [];
    suggestionsLoading.value = false;
    suggestionsActiveIdx.value = -1;
    return;
  }
  suggestionsTimer = setTimeout(
    () => fetchSuggestions(q),
    SUGGESTION_DEBOUNCE_MS
  );
});

function moveSuggestion(delta) {
  if (!suggestions.value.length) return;
  const len = suggestions.value.length;
  const current = suggestionsActiveIdx.value;
  const next = (((current + delta) % len) + len) % len;
  suggestionsActiveIdx.value = next;
}

function selectSuggestion(idx) {
  const sugg = suggestions.value[idx];
  if (!sugg) return;
  closeSuggestions();
  searchFocused.value = false;
  openTicket(sugg.name);
}

function onSearchEnter() {
  if (
    suggestionsActiveIdx.value >= 0 &&
    suggestions.value[suggestionsActiveIdx.value]
  ) {
    selectSuggestion(suggestionsActiveIdx.value);
    return;
  }
  submitSearch();
}

// Escape highlightTokens-safe regex specials in token strings
function _escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightTokens(text, query) {
  const raw = String(text == null ? "" : text);
  const q = String(query == null ? "" : query).trim();
  if (!raw || !q) return [{ text: raw, mark: false }];
  const tokens = q.toLowerCase().match(/[a-z0-9@._-]{2,}/g) || [];
  if (!tokens.length) return [{ text: raw, mark: false }];
  const re = new RegExp("(" + tokens.map(_escapeRegex).join("|") + ")", "ig");
  const out = [];
  let last = 0;
  let match;
  while ((match = re.exec(raw)) !== null) {
    if (match.index > last) {
      out.push({ text: raw.slice(last, match.index), mark: false });
    }
    out.push({ text: match[0], mark: true });
    last = match.index + match[0].length;
    if (match.index === re.lastIndex) re.lastIndex += 1; // zero-width safety
  }
  if (last < raw.length) {
    out.push({ text: raw.slice(last), mark: false });
  }
  return out;
}

function onSearchBlur() {
  // Delay so a click on a recent-search or suggestion item registers before the dropdown closes.
  setTimeout(() => {
    searchFocused.value = false;
    closeSuggestions();
  }, 120);
}

function focusSearchInput() {
  const el = searchInput.value;
  if (el && typeof el.focus === "function") {
    el.focus();
    el.select?.();
  }
}

function onGlobalKeydown(event) {
  // Ctrl/Cmd+K → focus search; Esc with input focused → blur it.
  const isShortcut =
    (event.ctrlKey || event.metaKey) &&
    !event.shiftKey &&
    !event.altKey &&
    (event.key === "k" || event.key === "K");
  if (isShortcut) {
    event.preventDefault();
    focusSearchInput();
    return;
  }
  if (event.key === "Escape" && document.activeElement === searchInput.value) {
    searchInput.value?.blur();
  }
}

const title = computed(() =>
  props.view === "my" ? "My Tickets" : "All Tickets"
);
const tickets = computed(() => result.data || []);
const canLoadMore = computed(
  () => tickets.value.length < (result.total_count || 0)
);
const cards = computed(() => result.cards || {});
const activeFilterSummary = computed(() => {
  return `search: "${appliedSearch.value.trim()}"`;
});

watch(
  () => [props.view, route.fullPath],
  async () => {
    emit("title", title.value, "Search, edit, and open tickets");
    applyRouteState();
    await reload();
  },
  { immediate: true }
);

// Show the "Filtering…" banner only after a brief delay so fast loads don't
// flash it on/off and create visual noise.
watch(
  () => loading.value || reloading.value,
  (isLoading) => {
    if (isLoading) {
      if (filteringBannerTimer) return;
      filteringBannerTimer = setTimeout(() => {
        showFilteringBanner.value = true;
        filteringBannerTimer = null;
      }, 120);
    } else {
      if (filteringBannerTimer) {
        clearTimeout(filteringBannerTimer);
        filteringBannerTimer = null;
      }
      showFilteringBanner.value = false;
    }
  }
);

onMounted(async () => {
  applyRouteState();
  loadRecentSearches();
  if (typeof window !== "undefined") {
    window.addEventListener("keydown", onGlobalKeydown);
  }
  await loadLookups();
});

onBeforeUnmount(() => {
  if (typeof window !== "undefined") {
    window.removeEventListener("keydown", onGlobalKeydown);
  }
  document.removeEventListener("mousedown", onDateRangeOutsideClick);
  if (filteringBannerTimer) {
    clearTimeout(filteringBannerTimer);
    filteringBannerTimer = null;
  }
});

function applyRouteState() {
  const routeSearch = String(
    route.query.search || route.query.message_body || ""
  );
  draftSearch.value = routeSearch;
  appliedSearch.value = routeSearch;
  filters.status = String(route.query.status || "");
  filters.priority = String(route.query.priority || "");
  filters.ticket_type = String(route.query.ticket_type || "");
  filters.assigned_to = String(route.query.assigned_to || "");
  filters.created_from = String(route.query.created_from || "");
  filters.created_to = String(route.query.created_to || "");
}

function syncEditState(rows) {
  rows.forEach((ticket) => {
    editState[ticket.name] = {
      ticket_type: ticket.ticket_type || "",
      priority: ticket.priority || "",
      status: ticket.custom_is_on_hold ? "On Hold" : ticket.status || "Open",
      assignee: ticket.assignee?.name || "",
      hold_reason: ticket.custom_hold_reason || "",
    };
  });
}

// Keep local lookups in sync with App.vue's once they finish loading — saves
// the duplicate get_agents / get_ticket_types round-trips on every navigation.
// Sort agents A→Z by display name (case-insensitive) — used everywhere agents are
// listed so the order is always alphabetical regardless of backend/insertion order.
function _agentSortKey(a) {
  // Trim so a stray leading/trailing space in an agent's name (a data-entry quirk
  // on some User records) can't sort it ahead of everyone else.
  return (a.full_name || a.name || "").trim();
}
function _byAgentName(a, b) {
  return _agentSortKey(a).localeCompare(_agentSortKey(b), undefined, {
    sensitivity: "base",
    numeric: true,
  });
}
if (injectedAgents) {
  watch(
    injectedAgents,
    (val) => {
      if (Array.isArray(val) && val.length)
        agents.value = [...val].sort(_byAgentName);
    },
    { immediate: true }
  );
}
if (injectedTicketTypes) {
  watch(
    injectedTicketTypes,
    (val) => {
      if (Array.isArray(val) && val.length) ticketTypes.value = val;
    },
    { immediate: true }
  );
}

async function loadLookups() {
  // If the parent already provided lookups, we're already in sync via the
  // watchers above — no need to round-trip the network again.
  if (
    (injectedAgents?.value?.length || 0) > 0 ||
    (injectedTicketTypes?.value?.length || 0) > 0
  ) {
    return;
  }
  const [agentResult, typeResult] = await Promise.allSettled([
    getAgents(),
    getTicketTypes(),
  ]);
  agents.value = (
    agentResult.status === "fulfilled" ? agentResult.value || [] : []
  )
    .slice()
    .sort(_byAgentName);
  ticketTypes.value =
    typeResult.status === "fulfilled" ? typeResult.value || [] : [];
}

function isSaving(name) {
  return !!rowSaving[name];
}

function cleanFilters() {
  return Object.fromEntries(
    Object.entries(filters).filter(([, value]) => value)
  );
}

// --- Date range picker helpers ---
function formatShortDate(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}
const dateRangeLabel = computed(() => {
  const from = filters.created_from;
  const to = filters.created_to;
  if (!from && !to) return "Ticket created";
  if (from && to)
    return `Created: ${formatShortDate(from)} → ${formatShortDate(to)}`;
  if (from) return `Created: from ${formatShortDate(from)}`;
  return `Created: until ${formatShortDate(to)}`;
});
function toggleDateRange() {
  if (dateRangeOpen.value) {
    closeDateRange();
    return;
  }
  dateRangeDraft.from = filters.created_from || "";
  dateRangeDraft.to = filters.created_to || "";
  dateRangeOpen.value = true;
  // Defer the listener attach so the click that opened the popover doesn't
  // immediately match the outside-click handler and close it.
  setTimeout(() => {
    document.addEventListener("mousedown", onDateRangeOutsideClick);
  }, 0);
}
function closeDateRange() {
  dateRangeOpen.value = false;
  document.removeEventListener("mousedown", onDateRangeOutsideClick);
}
function onDateRangeOutsideClick(event) {
  if (!dateRangeRef.value) return;
  if (!dateRangeRef.value.contains(event.target)) {
    closeDateRange();
  }
}
async function applyDateRange() {
  filters.created_from = dateRangeDraft.from || "";
  filters.created_to = dateRangeDraft.to || "";
  closeDateRange();
  await applyFiltersAndReload();
}
async function clearDateRange() {
  dateRangeDraft.from = "";
  dateRangeDraft.to = "";
  filters.created_from = "";
  filters.created_to = "";
  closeDateRange();
  await applyFiltersAndReload();
}

// --- Bulk edit helpers ---
const selectionCount = computed(() => selectedIds.value.size);
const allOnPageSelected = computed(() => {
  const rows = tickets.value;
  if (!rows.length) return false;
  return rows.every((t) => selectedIds.value.has(t.name));
});
function toggleRow(name) {
  // Re-assign the ref so reactivity picks up Set mutations.
  const next = new Set(selectedIds.value);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  selectedIds.value = next;
}
function isSelected(name) {
  return selectedIds.value.has(name);
}
function toggleAllOnPage() {
  const next = new Set(selectedIds.value);
  if (allOnPageSelected.value) {
    for (const t of tickets.value) next.delete(t.name);
  } else {
    for (const t of tickets.value) next.add(t.name);
  }
  selectedIds.value = next;
}
function clearSelection() {
  selectedIds.value = new Set();
}
function defaultBulkValueFor(field) {
  if (field === "status") return "Open";
  if (field === "priority") return "Medium";
  return "";
}
async function openBulkModal() {
  bulkResult.value = null;
  bulkField.value = "status";
  bulkValue.value = defaultBulkValueFor(bulkField.value);
  bulkModalOpen.value = true;
  // Lazy-load agent groups the first time the dialog opens.
  if (!agentGroups.value.length) {
    try {
      agentGroups.value = await listAgentGroups();
    } catch (e) {
      // Non-fatal — the field stays empty.
      agentGroups.value = [];
    }
  }
}
function closeBulkModal() {
  bulkModalOpen.value = false;
}
function onBulkFieldChange() {
  bulkValue.value = defaultBulkValueFor(bulkField.value);
}
async function applyBulkUpdate() {
  if (!selectedIds.value.size) return;
  bulkSaving.value = true;
  bulkResult.value = null;
  const allNames = Array.from(selectedIds.value);
  bulkProgress.value = { done: 0, total: allNames.length };
  const updated = [];
  const failed = [];
  try {
    // Chunk so the user sees progress and a single chunk-timeout doesn't lose
    // the whole batch. Server-side fast path makes each chunk cheap.
    for (let i = 0; i < allNames.length; i += BULK_CHUNK_SIZE) {
      const chunk = allNames.slice(i, i + BULK_CHUNK_SIZE);
      try {
        const res = await bulkUpdateTickets(
          chunk,
          bulkField.value,
          bulkValue.value
        );
        updated.push(...(res.updated || []));
        failed.push(...(res.failed || []));
      } catch (err) {
        // Whole chunk blew up — record each row as failed and continue.
        for (const name of chunk) {
          failed.push({ name, reason: err.message || "request failed" });
        }
      }
      bulkProgress.value = {
        done: Math.min(i + chunk.length, allNames.length),
        total: allNames.length,
      };
    }
    bulkResult.value = { updated, failed };
    // Optimistically reflect the change on the affected rows so the list updates
    // instantly instead of repainting stale values until the refresh lands.
    if (updated.length) {
      const updatedNames = new Set(
        updated.map((u) => (u && u.name != null ? u.name : u))
      );
      const patched = [];
      for (const row of result.data) {
        if (updatedNames.has(row.name)) {
          row[bulkField.value] = bulkValue.value;
          patched.push(row);
        }
      }
      if (patched.length) syncEditState(patched);
    }
    if (!failed.length) {
      closeBulkModal();
      clearSelection();
    }
    // Fresh refresh (bypass stale cache) so the just-updated rows don't flash back
    // to their old state (the original "close shows Open then closes later" bug).
    await reload({ fresh: true });
  } finally {
    bulkSaving.value = false;
  }
}

async function reload(opts = {}) {
  result.start = 0;
  await load({ append: false, ...opts });
}

function routeQueryFromState() {
  return {
    ...route.query,
    message_body: undefined,
    status: filters.status || undefined,
    priority: filters.priority || undefined,
    ticket_type: filters.ticket_type || undefined,
    assigned_to: filters.assigned_to || undefined,
    created_from: filters.created_from || undefined,
    created_to: filters.created_to || undefined,
    search: appliedSearch.value.trim() || undefined,
  };
}

function compactQuery(query) {
  return Object.fromEntries(
    Object.entries(query || {}).filter(([, value]) => value !== undefined)
  );
}

function sameQuery(left, right) {
  return (
    JSON.stringify(compactQuery(left)) === JSON.stringify(compactQuery(right))
  );
}

async function replaceRouteOrReload() {
  const nextQuery = routeQueryFromState();
  if (sameQuery(route.query, nextQuery)) {
    await reload();
    return;
  }
  await router.replace({ query: nextQuery });
}

async function applyFiltersAndReload() {
  await replaceRouteOrReload();
}

async function submitSearch() {
  appliedSearch.value = draftSearch.value.trim();
  rememberSearch(appliedSearch.value);
  searchFocused.value = false;
  closeSuggestions();
  await replaceRouteOrReload();
}

async function clearSearch() {
  draftSearch.value = "";
  appliedSearch.value = "";
  closeSuggestions();
  await replaceRouteOrReload();
}

function refreshList() {
  reload();
}

function resetResults() {
  result.data = [];
  result.total_count = 0;
  result.cards = {};
  result.start = 0;
}

// Stale-while-revalidate cache for the ticket list so a return visit paints
// instantly with the last seen rows, then quietly refreshes. Scoped to
// sessionStorage so we don't carry data across browser sessions.
const LIST_CACHE_TTL_MS = 60 * 1000;
function _listCacheKey() {
  const sig = {
    view: props.view,
    filters: cleanFilters(),
    search: appliedSearch.value,
    page_length: result.page_length,
  };
  return "unity_helpdesk_tickets_cache:" + JSON.stringify(sig);
}
function _readListCache() {
  try {
    const raw = window.sessionStorage.getItem(_listCacheKey());
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    if (Date.now() - (parsed.ts || 0) > LIST_CACHE_TTL_MS) return null;
    return parsed.data || null;
  } catch {
    return null;
  }
}
function _writeListCache(data) {
  try {
    window.sessionStorage.setItem(
      _listCacheKey(),
      JSON.stringify({ ts: Date.now(), data })
    );
  } catch {
    // Quota exceeded / disabled — silently ignore.
  }
}

async function load({ append = false, fresh = false } = {}) {
  const requestId = activeRequestId + 1;
  activeRequestId = requestId;
  activeController?.abort();
  activeController = new AbortController();

  // Stale-while-revalidate: if we have a fresh cache entry for this exact
  // filter set, paint it immediately and treat the API call as a background
  // refresh (no "Searching…" spinner). Cache only applies to first-page,
  // non-append loads.
  // `fresh` (post-mutation refresh) skips the stale cache so an inline/bulk edit
  // can't be repainted with pre-edit values.
  let usedCache = false;
  if (!append && !fresh) {
    const cached = _readListCache();
    if (cached && Array.isArray(cached.data)) {
      result.data = cached.data;
      result.total_count = cached.total_count || 0;
      result.cards = cached.cards || {};
      result.start = cached.start || 0;
      syncEditState(cached.data);
      usedCache = true;
    }
  }

  if (append) {
    loadingMore.value = true;
  } else if (usedCache || (fresh && result.data.length)) {
    // Cache hit, OR a post-mutation fresh refresh with rows already on screen —
    // keep the list visible and show only the unobtrusive reload indicator
    // (never blank to the skeleton after an edit).
    reloading.value = true;
  } else {
    loading.value = true;
  }
  error.value = "";
  reloadPrompt.value = false;
  emptyMessage.value = "No tickets found.";
  try {
    const params = {
      view: props.view,
      filters: cleanFilters(),
      search: appliedSearch.value,
      page_length: result.page_length,
      start: append ? tickets.value.length : 0,
    };
    const callOptions = {
      signal: activeController.signal,
      timeoutMs: appliedSearch.value.trim() ? 20000 : 30000,
      idempotent: true,
      onAttempt: () => {
        if (requestId === activeRequestId) reloading.value = true;
      },
    };
    // Fire the two split endpoints in parallel. The page response paints the
    // list immediately; the summary response fills in the KPI cards a beat
    // later. Both share the same per-request server-side context so there's
    // no duplicate query work, just two TCP roundtrips overlapping.
    const pagePromise = callWithRetry(
      "helpdesk.api.unity_helpdesk.get_tickets_page",
      params,
      callOptions
    );
    const summaryPromise = callWithRetry(
      "helpdesk.api.unity_helpdesk.get_tickets_summary",
      {
        view: params.view,
        filters: params.filters,
        search: params.search,
      },
      callOptions
    );
    // Don't leave the unhandled-rejection warning if summary throws before
    // we await it (e.g. page errors out first, we return early below).
    summaryPromise.catch(() => undefined);

    // Mark cards as pending until the summary lands. UI swaps stale
    // cached values for "…" placeholders so the user doesn't see an
    // out-of-date count for the 10+ seconds the dashboard aggregate
    // can take on a cold buffer pool.
    summaryPending.value = true;

    const pageData = await pagePromise;
    if (requestId !== activeRequestId) return;
    if (append) {
      result.data = [...result.data, ...(pageData.data || [])];
      result.start = pageData.start || result.start;
      // Do not overwrite result.page_length — user selection is source of truth.
    } else {
      result.data = pageData.data || [];
      result.start = pageData.start || 0;
      // Do not overwrite result.page_length — user selection is source of truth.
    }
    syncEditState(pageData.data || []);

    // Page is on screen — drop the "Loading…" indicator now so the cards
    // refresh feels independent of the row render.
    if (requestId === activeRequestId && !append) {
      loading.value = false;
    }

    // Cards arrive in their own beat. If they error, keep whatever cards we
    // were showing rather than zero-ing them out.
    let summaryData = null;
    try {
      summaryData = await summaryPromise;
    } catch (summaryErr) {
      if (
        summaryErr instanceof AuthRedirectError ||
        summaryErr?.code === "AUTH_REDIRECT" ||
        summaryErr?.code === "REQUEST_ABORTED"
      ) {
        // Auth/abort already handled upstream — just bail.
        return;
      }
      // Non-fatal: keep stale cards, don't blank the UI.
      console.warn("[unity-helpdesk] cards summary failed:", summaryErr);
    }
    if (requestId !== activeRequestId) return;
    if (summaryData) {
      result.total_count = summaryData.total_count || 0;
      result.cards = summaryData.cards || {};
    }
    summaryPending.value = false;

    if (!append) {
      _writeListCache({
        data: result.data,
        total_count: result.total_count,
        cards: result.cards,
        start: result.start,
      });
    }
  } catch (err) {
    if (requestId !== activeRequestId || err.code === "REQUEST_ABORTED") {
      return;
    }
    if (err.code === "REQUEST_TIMEOUT") {
      resetResults();
      emptyMessage.value = appliedSearch.value.trim()
        ? `Search timed out. Try a more specific query — e.g. a ticket ID or exact name.`
        : "Loading timed out. Please refresh and try again.";
      return;
    }
    // Session expired — api.js already kicked off the login redirect.
    // Show a friendly placeholder for the millisecond before navigation.
    if (err instanceof AuthRedirectError || err.code === "AUTH_REDIRECT") {
      error.value = "Session expired — redirecting to login…";
      return;
    }
    // Network/5xx exhausted retries → unobtrusive reload prompt.
    if (err.code === "NETWORK_ERROR" || (err.status && err.status >= 500)) {
      reloadPrompt.value = true;
    } else {
      // Real 4xx / app error — surface message.
      error.value = err.message;
    }
  } finally {
    if (requestId === activeRequestId) {
      if (append) {
        loadingMore.value = false;
      } else {
        loading.value = false;
      }
      reloading.value = false;
      // Always clear summaryPending — without this an early-return after
      // page-resolved would leave the cards stuck at "…" forever.
      summaryPending.value = false;
      activeController = null;
    }
  }
}

async function loadMore() {
  if (loading.value || loadingMore.value) return;
  await load({ append: true });
}

async function quickUpdate(ticket, field, value) {
  rowSaving[ticket.name] = true;
  error.value = "";
  const payload = {
    name: ticket.name,
    [field]: value,
  };
  if (field === "status") {
    payload.is_on_hold = value === "On Hold" ? 1 : 0;
    if (value === "On Hold") {
      payload.hold_from = ticket.custom_hold_from || todayString();
      payload.hold_reason = editState[ticket.name].hold_reason || "";
    }
  } else if (field === "hold_reason" && value) {
    // Typing a Reason Of Hold puts the ticket On Hold: co-send the flag + a
    // hold-from date so the "Issues On Hold" indicator reflects immediately.
    payload.is_on_hold = 1;
    payload.hold_from = ticket.custom_hold_from || todayString();
  }
  // Reflect the change on the row immediately — BEFORE the POST — so it never
  // flashes back to the old value while the (heavy) save runs.
  const index = result.data.findIndex((row) => row.name === ticket.name);
  if (index >= 0) {
    result.data[index][field] = value;
    if (payload.is_on_hold != null)
      result.data[index].custom_is_on_hold = payload.is_on_hold;
    if (payload.hold_from)
      result.data[index].custom_hold_from = payload.hold_from;
    syncEditState([result.data[index]]);
  }
  try {
    const updated = await call(
      "helpdesk.api.unity_helpdesk_ext.update_ticket",
      payload
    );
    if (index >= 0) {
      // Replace with the fresh server row, then quietly reconcile the list
      // (fresh = bypass the stale cache so nothing repaints pre-edit values).
      result.data[index] = updated;
      syncEditState([updated]);
    }
    await reload({ fresh: true });
  } catch (err) {
    error.value = err.message;
    await load({ fresh: true });
  } finally {
    rowSaving[ticket.name] = false;
  }
}

async function saveHoldReason(ticket) {
  const value = editState[ticket.name]?.hold_reason || "";
  if ((ticket.custom_hold_reason || "") === value) {
    return;
  }
  await quickUpdate(ticket, "hold_reason", value);
}

function todayString() {
  return new Date().toISOString().slice(0, 10);
}

// --- Cell rendering helpers (used by the dynamic <td v-for> in the table) ---
const EDITABLE_COLUMN_KEYS = new Set([
  "ticket_type",
  "priority",
  "status",
  "_assign",
  "custom_hold_reason",
]);

function isEditableColumn(key) {
  return EDITABLE_COLUMN_KEYS.has(key);
}

function onCellClick(event, key) {
  if (EDITABLE_COLUMN_KEYS.has(key)) event.stopPropagation();
}

const DATE_COLUMN_KEYS = new Set([
  "creation",
  "custom_hold_from",
  "custom_hold_to",
]);
const DATETIME_COLUMN_KEYS = new Set([
  "modified",
  "response_by",
  "resolution_by",
  "first_responded_on",
  "resolution_date",
]);

const MAIL_BODY_PREVIEW_CHARS = 210;

function truncateBody(text) {
  const value = String(text || "")
    .replace(/\s+/g, " ")
    .trim();
  if (value.length <= MAIL_BODY_PREVIEW_CHARS) return value;
  return value.slice(0, MAIL_BODY_PREVIEW_CHARS).trim() + "…";
}

function formatCellValue(ticket, key) {
  const raw = ticket?.[key];
  if (raw == null || raw === "") return "-";
  if (DATE_COLUMN_KEYS.has(key)) return formatDate(raw);
  if (DATETIME_COLUMN_KEYS.has(key)) return formatDateTime(raw);
  if (key === "_assign") {
    return ticket.assignee || "Unassigned";
  }
  return String(raw);
}

function formatHoldWindow(ticket) {
  if (!ticket.custom_hold_from && !ticket.custom_hold_to) {
    return "On hold";
  }
  if (ticket.custom_hold_from && ticket.custom_hold_to) {
    return `${formatDate(ticket.custom_hold_from)} - ${formatDate(
      ticket.custom_hold_to
    )}`;
  }
  return formatDate(ticket.custom_hold_from || ticket.custom_hold_to);
}

function openTicket(name, event) {
  const target = {
    path: `/tickets/${name}`,
    query: {
      ...routeQueryFromState(),
      list_view: props.view,
    },
  };
  // Ctrl / Cmd / middle-click (or Shift) → open in a new browser tab, like a real
  // link. `event.button === 1` covers middle-click via @auxclick.middle.
  if (
    event &&
    (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1)
  ) {
    window.open(router.resolve(target).href, "_blank", "noopener");
    return;
  }
  // Plain click → same-tab SPA navigation. Store the current list for prev/next.
  sessionStorage.setItem(
    "unity:ticket_nav",
    JSON.stringify({
      ids: tickets.value.map((t) => String(t.name)),
      view: props.view,
    })
  );
  router.push(target);
}

function assignmentClass(assignee) {
  return assignee ? "blue" : "pink";
}

// --- Searchable "Assigned To" cell ---
// agents.value is already sorted A→Z at the source (see _byAgentName).
const agentsAsc = computed(() => agents.value);
function agentDisplay(agent) {
  return (agent.full_name || agent.name || "").trim();
}
function assigneeLabel(name) {
  if (!name) return "";
  const a = agents.value.find((x) => x.name === name);
  return a ? agentDisplay(a) || name : name;
}

// Searchable assignee dropdown state (one open at a time, teleported to body).
const assignOpen = ref(null); // ticket.name currently open, or null
const assignQuery = ref("");
const assignSearchRef = ref(null);
const assignPopoverRef = ref(null);
const assignPos = reactive({ top: 0, left: 0, width: 220 });
let _assignTicket = null;

// Close on scroll/resize so the fixed popover doesn't detach from its trigger — but
// IGNORE scrolls that happen INSIDE the popover's own option list (else scrolling
// the agent list would collapse it). Capture phase is required to catch scrolls on
// nested scroll containers (which don't bubble).
function _onOutsideScroll(e) {
  const pop = assignPopoverRef.value;
  if (pop && e.target instanceof Node && pop.contains(e.target)) return;
  closeAssign();
}

const assignPopoverStyle = computed(() => ({
  top: `${assignPos.top}px`,
  left: `${assignPos.left}px`,
  width: `${assignPos.width}px`,
}));
const assignMatches = computed(() => {
  const q = assignQuery.value.trim().toLowerCase();
  if (!q) return agentsAsc.value;
  return agentsAsc.value.filter((a) =>
    [a.full_name, a.name, a.email].some((v) =>
      String(v || "")
        .toLowerCase()
        .includes(q)
    )
  );
});

function openAssign(ticket, ev) {
  _assignTicket = ticket;
  assignOpen.value = ticket.name;
  assignQuery.value = "";
  const rect = ev.currentTarget.getBoundingClientRect();
  // Position (fixed / viewport coords) just under the trigger.
  assignPos.top = Math.round(rect.bottom + 4);
  assignPos.left = Math.round(rect.left);
  assignPos.width = Math.round(Math.max(rect.width, 200));
  window.addEventListener("scroll", _onOutsideScroll, true);
  window.addEventListener("resize", closeAssign, true);
  nextTick(() => assignSearchRef.value?.focus());
}
function closeAssign() {
  if (assignOpen.value === null) return;
  assignOpen.value = null;
  _assignTicket = null;
  assignQuery.value = "";
  window.removeEventListener("scroll", _onOutsideScroll, true);
  window.removeEventListener("resize", closeAssign, true);
}
function pickAssign(name) {
  const ticket = _assignTicket;
  if (
    ticket &&
    editState[ticket.name] &&
    editState[ticket.name].assignee !== name
  ) {
    editState[ticket.name].assignee = name;
    quickUpdate(ticket, "assignee", name);
  }
  closeAssign();
}
onBeforeUnmount(closeAssign);

function statusClass(ticket, selectedStatus) {
  if (selectedStatus === "On Hold") return "yellow";
  if (selectedStatus === "Resolved") return "green";
  if (selectedStatus === "Closed") return "grey";
  return editState[ticket.name]?.assignee ? "blue" : "pink";
}

function priorityClass(priority) {
  const value = (priority || "").toLowerCase();
  if (value === "high") return "pink";
  if (value === "medium") return "yellow";
  if (value === "low") return "green";
  return "grey";
}

function ticketTypeClass(ticketType) {
  const value = (ticketType || "").toLowerCase();
  if (!value) return "grey";
  if (["app", "tech"].includes(value)) return "blue";
  if (["calling", "result"].includes(value)) return "pink";
  if (["walmiki"].includes(value)) return "yellow";
  return "green";
}

// Returns an inline style override when the HD Ticket Type has a
// custom_color picked in Settings. Falls back to null so the caller
// keeps the existing hardcoded ticketTypeClass tint for types
// without a configured colour. The "+1a" suffix (~10% opacity) gives
// a subtle background tint while the full colour drives the border
// and text — same visual rhythm as the hardcoded classes.
function ticketTypeStyle(ticketType) {
  if (!ticketType) return null;
  const match = (ticketTypes.value || []).find(
    (t) => t && t.name === ticketType
  );
  const color = match?.custom_color;
  if (!color || !/^#[0-9a-fA-F]{6}$/.test(color)) return null;
  return {
    color: color,
    background: color + "1a",
    borderColor: color,
  };
}
</script>
