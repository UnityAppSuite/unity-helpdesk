<template>
  <section class="page">
    <div class="toolbar">
      <!-- Row 1: filters + their Apply/Clear (and the mobile Filters toggle). -->
      <div class="toolbar-top">
        <div class="filter-group" :class="{ open: filtersOpen }">
          <select v-model="filterDraft.status">
            <option value="">Status: All</option>
            <option>Open</option>
            <option>Replied</option>
            <option>On Hold</option>
            <option>Resolved</option>
            <option>Closed</option>
          </select>
          <select v-model="filterDraft.priority">
            <option value="">Priority: All</option>
            <option>High</option>
            <option>Medium</option>
            <option>Low</option>
          </select>
          <select v-model="filterDraft.ticket_type">
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
          <select v-if="props.view === 'all'" v-model="filterDraft.assigned_to">
            <option value="">Assigned: All</option>
            <option value="Unassigned">Unassigned</option>
            <option
              v-for="agent in agents"
              :key="agent.name"
              :value="agent.name"
            >
              {{ agent.full_name || agent.name }}
            </option>
          </select>
          <span v-else class="badge blue">Assigned to me</span>
          <select v-model="filterDraft.agent_group">
            <option value="">Agent Group: All</option>
            <option
              v-for="grp in agentGroups"
              :key="grp.name"
              :value="grp.name"
            >
              {{ grp.name }}
            </option>
          </select>
        </div>
        <button
          type="button"
          class="btn secondary filters-toggle"
          @click="filtersOpen = !filtersOpen"
        >
          Filters<span v-if="activeFilterCount" class="filters-toggle-badge">{{
            activeFilterCount
          }}</span>
        </button>
        <!-- Everything the five primary dropdowns don't cover (Created By, Created
             On, SLA, hold dates, subject/raised-by text …) lives behind this. It sits
             immediately before Apply because it feeds the same draft state and is
             committed by the same click. -->
        <div ref="filterRef" class="filter-wrap">
          <button
            class="btn secondary toolbar-filter"
            type="button"
            title="Filter on any field"
            :aria-expanded="filterOpen"
            @click="toggleFilterPopover"
          >
            Filter<span
              v-if="filterDraft.conditions.length"
              class="sort-count"
              >{{ filterDraft.conditions.length }}</span
            >
          </button>
          <FilterPopover
            v-if="filterOpen"
            :model-value="filterDraft.conditions"
            :fields="filterableFields"
            :options-by-key="filterOptionsByKey"
            :max="MAX_FILTER_CONDITIONS"
            @update:model-value="setConditions"
            @close="closeFilterPopover"
          />
        </div>
        <button
          type="button"
          class="btn apply-filters"
          :disabled="!filtersDirty"
          title="Apply filters and search"
          @click="applyAll"
        >
          Apply<span v-if="filtersDirty" class="apply-dot" aria-hidden="true"
            >•</span
          >
        </button>
        <button
          type="button"
          class="btn secondary toolbar-clear"
          title="Clear all filters and search"
          @click="clearAll"
        >
          Clear
        </button>
      </div>
      <!-- Row 2: search box (grows) + Search beside it; Refresh/Columns as a separate group. -->
      <div class="toolbar-bottom">
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
            v-if="(loading || reloading) && appliedSearch"
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
        </div>
        <button
          class="btn secondary toolbar-search"
          type="button"
          @click="submitSearch"
        >
          Search
        </button>
        <div class="toolbar-actions">
          <button
            class="btn secondary toolbar-refresh"
            type="button"
            @click="refreshList"
          >
            Refresh
          </button>
          <div ref="sortRef" class="sort-wrap">
            <button
              class="btn secondary toolbar-sort"
              type="button"
              title="Sort tickets"
              :aria-expanded="sortOpen"
              @click="toggleSortPopover"
            >
              Sort<span v-if="activeSorts.length" class="sort-count">{{
                activeSorts.length
              }}</span>
            </button>
            <SortPopover
              v-if="sortOpen"
              :model-value="activeSorts"
              :fields="sortableFields"
              :max="MAX_SORT_TERMS"
              @update:model-value="applySort"
              @close="closeSortPopover"
            />
          </div>
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
      <!-- Only when the search actually hit the relevance-candidate cap AND a
           sort is active: ordering is exact for a normal search, approximate
           only when both are true. -->
      <div v-if="showSortTruncationNote" class="sort-truncation-note">
        Sorting the first 1,000 matches — narrow your search for an exact order.
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
                :aria-sort="ariaSortFor(col.key)"
                @dragstart="onColDragStart($event, colIdx)"
                @dragover="onColDragOver($event, colIdx)"
                @dragend="onColDragEnd"
                @drop.prevent
              >
                <span v-if="!col.fixed" class="col-drag-handle">⠿</span>
                <!-- The sort handler lives on a BUTTON, not on the <th>. The
                     resize grabber below is a bare @mousedown span with no
                     .stop, so a resize bubbles a click to the <th> — a th-level
                     handler would re-sort on every column resize. Scoping to a
                     button that doesn't contain the grabber removes that
                     conflict, and gets keyboard access + a focus ring free.
                     (HTML5 drag never emits a click, so :draggable is safe.) -->
                <button
                  v-if="sortableFieldMap[col.key]"
                  class="col-sort-btn"
                  type="button"
                  :title="sortHint(col.key)"
                  @click.stop="toggleSortFromHeader(col.key, $event)"
                >
                  {{ col.label
                  }}<span
                    v-if="sortIndexFor(col.key) >= 0"
                    class="col-sort-ind"
                    aria-hidden="true"
                    >{{ sortDirFor(col.key) === "asc" ? "▲" : "▼"
                    }}<sup v-if="activeSorts.length > 1">{{
                      sortIndexFor(col.key) + 1
                    }}</sup></span
                  >
                </button>
                <template v-else>{{ col.label }}</template>
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
                    @click.stop="toggleAddColumnMenu($event)"
                  >
                    +
                  </button>
                  <!-- Teleported to body + fixed coords so the table's scroll
                       container (.scroll-x, overflow) can't clip it. -->
                  <Teleport to="body">
                    <div
                      v-if="showAddCol"
                      ref="addColMenuRef"
                      class="col-add-dropdown"
                      :style="{
                        top: addColPos.top + 'px',
                        left: addColPos.left + 'px',
                      }"
                      @click.stop
                    >
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
                  </Teleport>
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
                <!-- Stretched link covering the whole row: a real <a href> so native
                     right-click "Open in new tab/window" + middle-click work ANYWHERE
                     on the row. Interactive controls are raised above it via z-index. -->
                <RouterLink
                  class="row-link"
                  :to="ticketTo(ticket.name)"
                  :aria-label="`Open ticket ${ticket.name}`"
                  @click.stop="rememberTicketNav"
                  @auxclick.stop
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
                  <!-- Styled text only; the whole row is a link via the .row-link
                       overlay (native right-click / middle-click work anywhere). -->
                  <span class="link-btn">#{{ ticket.name }}</span>
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
                <template v-else-if="col.key === 'creation_age'">
                  <span
                    v-if="relativeTime(ticket.creation)"
                    :title="formatDateTime(ticket.creation)"
                    >{{ relativeTime(ticket.creation) }}</span
                  >
                  <span v-else class="muted">-</span>
                </template>
                <template v-else-if="col.key === 'modified_age'">
                  <span
                    v-if="relativeTime(ticket.modified)"
                    :title="formatDateTime(ticket.modified)"
                    >{{ relativeTime(ticket.modified) }}</span
                  >
                  <span v-else class="muted">-</span>
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
              <RouterLink
                class="ticket-card-id"
                :to="ticketTo(ticket.name)"
                @click.stop="rememberTicketNav"
                @auxclick.stop
                >#{{ ticket.name }}</RouterLink
              >
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
        <!-- Segmented, always visible — the options are the point, so hiding
             them behind a dropdown costs a click for no benefit.
             NOTE: `.table-header span` is a bare element selector that pill-
             styles ANY span in this footer, so every control here uses <label>
             or <button> with a bare text node, never a nested <span>. -->
        <div
          class="page-size-control"
          role="radiogroup"
          aria-label="Rows per page"
          title="Rows fetched per request"
        >
          <label class="page-size-label">Rows</label>
          <div class="page-size-seg">
            <button
              v-for="(size, sizeIdx) in PAGE_SIZE_OPTIONS"
              :key="size"
              ref="pageSizeBtns"
              type="button"
              role="radio"
              :aria-checked="result.page_length === size"
              :tabindex="result.page_length === size ? 0 : -1"
              :class="[
                'page-size-opt',
                { active: result.page_length === size },
              ]"
              :disabled="loading || loadingMore || reloading"
              @keydown.left.prevent="stepPageSize(sizeIdx, -1)"
              @keydown.right.prevent="stepPageSize(sizeIdx, 1)"
              @click="setPageSize(size)"
            >
              {{ size }}
            </button>
          </div>
        </div>
        <span
          >Showing {{ tickets.length }} of {{ result.total_count || 0 }}</span
        >
        <button
          v-if="canLoadMore"
          class="btn secondary"
          :disabled="loading || loadingMore || reloading"
          @click="loadMore"
        >
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
// Upstream's dayjs wrapper (relativeTime plugin already registered) so the
// relative date columns read exactly like the Frappe Helpdesk list:
// "20 days ago", "2 months ago".
import { dayjs } from "@desk/dayjs";
import FilterPopover from "@/components/FilterPopover.vue";
import SortPopover from "@/components/SortPopover.vue";
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
  listTicketPriorities,
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
  // Persist the user's "Rows per page" choice across reloads/remounts (e.g. opening a
  // ticket and returning). Read back the value the size-watcher stores, and only honor it
  // if it's a valid option; otherwise fall back to the smallest (20) for a fast first paint.
  try {
    const stored = parseInt(
      window.localStorage.getItem(PAGE_SIZE_STORAGE_KEY),
      10
    );
    if (PAGE_SIZE_OPTIONS.includes(stored)) return stored;
  } catch {
    // localStorage unavailable (private mode / quota) — fall through to the default.
  }
  return 20;
}

const result = reactive({
  data: [],
  total_count: 0,
  cards: {},
  start: 0,
  page_length: _initialPageSize(),
  search_truncated: false,
});

// ---- Sorting -------------------------------------------------------------
// Sort is applied by the SERVER (an `order_by` string over all ~67k tickets) —
// reordering only the rows already on screen would sort 20 of 67,000 and be
// quietly wrong. What makes it feel instant is that we ALSO reorder the loaded
// rows locally the moment the user clicks, then let the real result swap in
// silently. See applySort().
const SORT_STORAGE_KEY = "unity_helpdesk_sort";
const MAX_SORT_TERMS = 3;

function _initialSorts() {
  // Shape: [{ key: "status", direction: "asc" }] — `key` is a STRING. (Upstream's
  // Sort.vue stores the whole field object here, which is why its list can't be
  // keyed or spliced correctly; we don't copy that.)
  try {
    const raw = JSON.parse(
      window.localStorage.getItem(SORT_STORAGE_KEY) || "[]"
    );
    if (!Array.isArray(raw)) return [];
    return raw
      .filter(
        (s) =>
          s &&
          typeof s.key === "string" &&
          (s.direction === "asc" || s.direction === "desc")
      )
      .slice(0, MAX_SORT_TERMS);
  } catch {
    return [];
  }
}

const sortOpen = ref(false);
const sortRef = ref(null);
const pageSizeBtns = ref([]);

function setPageSize(size) {
  if (result.page_length === size) return;
  result.page_length = size; // watcher persists it to localStorage
  reload(); // resets start to 0, exactly as the old <select> @change did
}

// Roving tabindex: a radiogroup is one tab stop, arrows move within it.
function stepPageSize(index, delta) {
  const next =
    (index + delta + PAGE_SIZE_OPTIONS.length) % PAGE_SIZE_OPTIONS.length;
  setPageSize(PAGE_SIZE_OPTIONS[next]);
  nextTick(() => pageSizeBtns.value?.[next]?.focus());
}

const sorts = ref(_initialSorts());
watch(
  sorts,
  (value) => {
    try {
      window.localStorage.setItem(SORT_STORAGE_KEY, JSON.stringify(value));
    } catch {
      // Quota / private mode — non-fatal.
    }
  },
  { deep: true }
);

const sortableFields = computed(() => unitySession?.sortable_fields || []);
const sortableFieldMap = computed(() =>
  Object.fromEntries(sortableFields.value.map((f) => [f.key, f]))
);
// Drop persisted keys the backend no longer offers — but only once the registry
// has actually arrived, or the first render (session still loading) would wipe a
// perfectly good saved sort. This is what lets the backend reject unknown sort
// fields outright instead of silently ignoring them.
const activeSorts = computed(() =>
  sortableFields.value.length
    ? sorts.value.filter((s) => sortableFieldMap.value[s.key])
    : sorts.value
);
const orderByString = computed(() =>
  activeSorts.value.map((s) => `${s.key} ${s.direction}`).join(", ")
);
// Under search the backend can only order the top 1,000 relevance candidates,
// so a sorted broad search is approximate. Say so — but only when it bites.
const showSortTruncationNote = computed(
  () => !!result.search_truncated && activeSorts.value.length > 0
);

// ---- Generic filters ------------------------------------------------------
// The curated registry the backend ships (unitySession.filterable_fields), the
// exact counterpart of sortable_fields above. Same contract, too: we drop any
// condition whose field/operator the backend no longer offers BEFORE sending,
// but only once the registry has actually arrived — otherwise the first render
// (session still loading) would wipe a perfectly good filter from the URL.
// That client-side pruning is what lets _parse_filter_conditions throw on
// anything unknown instead of silently ignoring it.
const filterableFields = computed(() => unitySession?.filterable_fields || []);
const filterableFieldMap = computed(() =>
  Object.fromEntries(filterableFields.value.map((f) => [f.key, f]))
);
const MAX_FILTER_CONDITIONS = computed(
  () => unitySession?.max_filter_conditions || 8
);
function validConditions(rows) {
  if (!filterableFields.value.length) return rows;
  return rows.filter((row) => {
    const spec = filterableFieldMap.value[row.key];
    return !!spec && (spec.operators || []).includes(row.operator);
  });
}
// Link-field choices for the popover, from lookups the view already loads for
// the primary controls. A field absent here falls back to a text input.
const filterOptionsByKey = computed(() => ({
  ticket_type: ticketTypes.value.map((t) => ({
    value: t.name,
    label: t.name,
  })),
  agent_group: agentGroups.value.map((g) => ({
    value: g.name,
    label: g.name,
  })),
  owner: agents.value.map((a) => ({
    value: a.name,
    label: a.full_name || a.name,
  })),
  priority: ticketPriorities.value.map((p) => ({
    value: p.name,
    label: p.name,
  })),
}));
const filterOpen = ref(false);
const filterRef = ref(null);

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
// Link-field choices for the generic filter popover. Loaded lazily when the
// popover first opens, the same way agentGroups is for the bulk-edit dialog.
const ticketPriorities = ref([]);
const BULK_FIELD_LABELS = {
  status: "Status",
  priority: "Priority",
  _assign: "Assignee",
  ticket_type: "Ticket Type",
  agent_group: "Agent Group",
};
// Debounced "is something loading" flag for the table-dim + filtering banner.
// Goes true only after ~120ms of continuous loading so quick (<120ms) loads
// don't flicker the UI on top of fast post-index responses.
const showFilteringBanner = ref(false);
let filteringBannerTimer = null;
// Number of in-flight loads the user should not perceive as loading (sorting).
// While > 0 the dim/banner watcher below stays quiet. See load({silent}).
const silentLoads = ref(0);
// The relative-time columns are derived from the wall clock, so they have to be
// re-derived or a tab left open overnight shows yesterday's wording. The text
// changes slowly, so a coarse tick is plenty; the visibilitychange listener covers
// laptop sleep, where interval timers get throttled or skipped entirely.
const NOW_TICK_INTERVAL_MS = 10 * 60 * 1000;
const nowTick = ref(Date.now());
let nowTickTimer = null;
function bumpNowTick() {
  nowTick.value = Date.now();
}
// True from request start until the get_tickets_summary response lands.
// Drives "…" placeholders on the KPI cards so the user doesn't stare at
// stale sessionStorage-cached numbers while the dashboard aggregate
// refreshes in the background. Independent of the row-skeleton `loading`
// flag because rows usually render seconds before cards.
const summaryPending = ref(false);
// The five PRIMARY filters, always visible as dropdowns in the toolbar. Everything
// else — Created By, Created On, SLA status, hold dates, subject/raised-by text —
// is reachable through the Filter popover instead (`conditions` below), which
// offers richer operators than a dropdown can.
const filters = reactive({
  status: "",
  priority: "",
  ticket_type: "",
  assigned_to: "",
  agent_group: "",
  // Generic field/operator/value rows from the Filter popover, ANDed onto the
  // fixed dropdowns above. Shape: [{ key, operator, value }].
  conditions: [],
});
// Draft mirror of `filters`. The UI binds to this; nothing fetches until the user
// clicks Apply, which copies the draft into `filters` (the committed snapshot read
// by cleanFilters/routeQueryFromState/refreshSummary) and reloads once.
const filterDraft = reactive({
  status: "",
  priority: "",
  ticket_type: "",
  assigned_to: "",
  agent_group: "",
  conditions: [],
});
const FILTER_KEYS = [
  "status",
  "priority",
  "ticket_type",
  "assigned_to",
  "agent_group",
];
// `conditions` is deliberately NOT in FILTER_KEYS: it's an array, so it needs
// structural compare and deep copy rather than the scalar `!==` / assignment
// the other keys use. Kept separate so nobody adds it to the list by reflex.
function conditionsSignature(rows) {
  return JSON.stringify(rows || []);
}
function cloneConditions(rows) {
  // Deep enough for the shape we store — `value` can be an array (in / between),
  // so a shallow map would still share that inner reference.
  return (rows || []).map((r) => ({
    ...r,
    value: Array.isArray(r.value) ? [...r.value] : r.value,
  }));
}
// Conditions travel in ONE url param as JSON. Filters are URL-only in this view
// (unlike sort, which persists to localStorage), so links stay shareable and
// back/forward keeps working — encoding them as JSON avoids inventing a
// delimiter that a subject/email value could contain.
const CONDITIONS_QUERY_KEY = "fc";
function encodeConditions(rows) {
  return rows && rows.length ? JSON.stringify(rows) : undefined;
}
function decodeConditions(raw) {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(String(raw));
    if (!Array.isArray(parsed)) return [];
    // Hand-edited URLs land here, so keep only well-formed rows. Unknown
    // fields/operators are pruned later by validConditions, once the registry
    // has arrived.
    return parsed
      .filter((r) => r && typeof r === "object" && r.key && r.operator)
      .slice(0, MAX_FILTER_CONDITIONS.value)
      .map((r) => ({
        key: String(r.key),
        operator: String(r.operator),
        value: Array.isArray(r.value)
          ? r.value.map(String)
          : String(r.value ?? ""),
      }));
  } catch {
    return [];
  }
}
// True when the draft filters or the typed search differ from what's applied, so
// the Apply button can enable/highlight only when there's something to commit.
const filtersDirty = computed(
  () =>
    FILTER_KEYS.some((k) => filterDraft[k] !== filters[k]) ||
    conditionsSignature(filterDraft.conditions) !==
      conditionsSignature(filters.conditions) ||
    draftSearch.value.trim() !== appliedSearch.value
);
// Mobile: the filter row collapses behind a "Filters" toggle. On desktop the
// filter group is always shown via CSS (display:contents), so this only gates mobile.
const filtersOpen = ref(false);

// Preview the count from the DRAFT so the badge reflects what's about to apply.
const activeFilterCount = computed(
  () =>
    FILTER_KEYS.filter((k) => filterDraft[k]).length +
    (filterDraft.conditions?.length || 0)
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
// Fixed/viewport coords for the teleported add-column menu (anchored to the + btn).
const addColPos = reactive({ top: 0, left: 0 });
const addColMenuRef = ref(null);

// Close on scroll/resize so the fixed menu can't detach from the button — but IGNORE
// scrolls INSIDE the menu's own list (capture phase catches those), else scrolling
// the column list would collapse it.
function _onAddColScroll(e) {
  const el = addColMenuRef.value;
  if (el && e.target instanceof Node && el.contains(e.target)) return;
  closeAddColMenu();
}

const hiddenColumns = computed(() => {
  const visible = new Set(
    (unitySession?.settings?.column_preferences || []).map((p) => p.key)
  );
  return (availableColumns.value || []).filter((c) => !visible.has(c.key));
});

function toggleAddColumnMenu(ev) {
  if (showAddCol.value) {
    closeAddColMenu();
    return;
  }
  const rect = ev.currentTarget.getBoundingClientRect();
  // Right-align to the button (CSS translateX(-100%)), opening just below it.
  addColPos.top = Math.round(rect.bottom + 6);
  addColPos.left = Math.round(rect.right);
  showAddCol.value = true;
  window.addEventListener("scroll", _onAddColScroll, true);
  window.addEventListener("resize", closeAddColMenu, true);
}
function closeAddColMenu() {
  if (!showAddCol.value) return;
  showAddCol.value = false;
  window.removeEventListener("scroll", _onAddColScroll, true);
  window.removeEventListener("resize", closeAddColMenu, true);
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
  closeAddColMenu();
  setColumnPrefsLocal(prefs); // column shows instantly using already-loaded data
  if (columnNeedsFetch(def)) {
    // Only the few extra-field columns (SLA dates, mail body) hit this.
    await persistColumnPrefs(prefs);
  } else {
    persistColumnPrefsQuiet(prefs);
  }
}

// Close add-column dropdown on outside click (menu items + button are @click.stop,
// so this only fires for genuine outside clicks).
function onDocClick() {
  closeAddColMenu();
}
onMounted(() => document.addEventListener("click", onDocClick));
onBeforeUnmount(() => {
  document.removeEventListener("click", onDocClick);
  closeAddColMenu();
});

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
const isMac =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad/.test(navigator.platform);
const shortcutLabel = isMac ? "⌘K" : "Ctrl+K";

const searchInput = ref(null);
const searchFocused = ref(false);
const recentSearches = ref([]);

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

// --- Search: only commits on Search button / Enter (never as-you-type) ---

// Close the recent-searches dropdown. (Kept for the commit/clear/blur/esc paths.)
function closeSuggestions() {
  searchFocused.value = false;
}

function onSearchEnter() {
  submitSearch();
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
  () => (loading.value || reloading.value) && silentLoads.value === 0,
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
    nowTickTimer = window.setInterval(bumpNowTick, NOW_TICK_INTERVAL_MS);
  }
  document.addEventListener("visibilitychange", bumpNowTick);
  await loadLookups();
});

onBeforeUnmount(() => {
  if (typeof window !== "undefined") {
    window.removeEventListener("keydown", onGlobalKeydown);
  }
  document.removeEventListener("mousedown", onSortOutsideClick);
  document.removeEventListener("mousedown", onFilterOutsideClick);
  document.removeEventListener("visibilitychange", bumpNowTick);
  if (nowTickTimer) {
    clearInterval(nowTickTimer);
    nowTickTimer = null;
  }
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
  filters.agent_group = String(route.query.agent_group || "");
  filters.conditions = decodeConditions(route.query[CONDITIONS_QUERY_KEY]);
  // Mirror the applied snapshot into the draft so the dropdowns reflect the URL
  // state on first load, shared links, and back/forward nav.
  FILTER_KEYS.forEach((k) => {
    filterDraft[k] = filters[k];
  });
  filterDraft.conditions = cloneConditions(filters.conditions);
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
  // Agent Group is a PRIMARY dropdown now, so its options have to be there on
  // first paint — unlike the Filter popover's link choices, which can stay lazy.
  // Fired unconditionally (and not awaited below) because the parent never
  // provides teams, only agents and ticket types.
  loadAgentGroups();
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
  const out = Object.fromEntries(
    Object.entries(filters).filter(
      ([key, value]) => key !== "conditions" && !!value
    )
  );
  // Only send conditions the shipped registry still recognises (see
  // validConditions), and OMIT the key entirely when there are none.
  //
  // The omission is load-bearing: an EMPTY ARRAY IS TRUTHY, so leaving
  // `conditions: []` in would make the filter payload permanently non-empty,
  // and the backend's `if not filters` fast path — six index-only COUNTs
  // instead of a full-table SUM(CASE) aggregate — would never fire again.
  const conditions = validConditions(filters.conditions || []);
  if (conditions.length) out.conditions = conditions;
  return out;
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
  // Normally already loaded for the Agent Group filter; this covers the case
  // where that fetch failed. Idempotent.
  await loadAgentGroups();
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
      // For _assign, resolve the chosen agent to the row's assignee shape so the
      // "Assigned To" cell reflects the change without a refetch.
      const assignAgent =
        bulkField.value === "_assign" && bulkValue.value
          ? agents.value.find((a) => a.name === bulkValue.value) || {
              name: bulkValue.value,
            }
          : null;
      const patched = [];
      for (const row of result.data) {
        if (!updatedNames.has(row.name)) continue;
        if (bulkField.value === "status") {
          if (bulkValue.value === "On Hold") {
            row.custom_is_on_hold = 1;
          } else {
            row.status = bulkValue.value;
            row.custom_is_on_hold = 0;
          }
        } else if (bulkField.value === "_assign") {
          row.assignee = assignAgent
            ? {
                name: assignAgent.name,
                full_name: assignAgent.full_name || assignAgent.name,
                user_image: assignAgent.user_image,
                email: assignAgent.email,
              }
            : null;
        } else {
          row[bulkField.value] = bulkValue.value;
        }
        patched.push(row);
      }
      if (patched.length) syncEditState(patched);
    }
    if (!failed.length) {
      closeBulkModal();
      clearSelection();
    }
    // Rows are patched locally; only the KPI counts need refreshing, so skip the
    // full-list refetch (which flashed/churned the whole table).
    refreshSummary();
  } finally {
    bulkSaving.value = false;
  }
}

async function reload(opts = {}) {
  result.start = 0;
  await load({ append: false, ...opts });
}

// ---- Sorting: instant local reorder, then a silent server refetch ----------
// The comparators below deliberately mirror the SQL the backend builds, so the
// optimistic paint lands on the same order the server is about to return and
// the rows don't visibly jump. Ranks come from the registry the backend ships
// (spec.rank), so "Urgent > High > Medium > Low" has exactly one definition.
function _compareValues(a, b, spec) {
  if (spec.rank) {
    // indexOf -> -1 for unknown/blank, which sorts FIRST ascending — identical
    // to MariaDB's FIELD() returning 0. Free agreement; don't "fix" it.
    return spec.rank.indexOf(a) - spec.rank.indexOf(b);
  }
  if (spec.type === "int") return (Number(a) || 0) - (Number(b) || 0);
  if (spec.type === "datetime" || spec.type === "date") {
    // Frappe datetimes are "YYYY-MM-DD HH:MM:SS[.ffffff]", so lexicographic
    // order IS chronological order — no Date parsing, no timezone hazard.
    const x = String(a || "");
    const y = String(b || "");
    return x < y ? -1 : x > y ? 1 : 0;
  }
  return String(a || "").localeCompare(String(b || ""), undefined, {
    sensitivity: "base",
  });
}

function sortRowsLocally(rows, terms) {
  const out = rows.slice();
  out.sort((x, y) => {
    for (const term of terms) {
      const spec = sortableFieldMap.value[term.key];
      if (!spec) continue;
      // spec.field, not term.key — "creation_age" has to read row.creation.
      const cmp = _compareValues(x[spec.field], y[spec.field], spec);
      if (cmp) return term.direction === "desc" ? -cmp : cmp;
    }
    // Same `name` tiebreaker the server appends, in the same direction.
    const dir = terms.length ? terms[terms.length - 1].direction : "desc";
    const diff = Number(x.name) - Number(y.name);
    return dir === "desc" ? -diff : diff;
  });
  return out;
}

async function applySort(nextSorts) {
  sorts.value = (nextSorts || []).slice(0, MAX_SORT_TERMS);
  // Reorder what's already in memory so the header indicator and the rows
  // change in the same frame — same trick quickUpdate() uses to patch a row
  // before its POST so the value never flashes back.
  result.data = sortRowsLocally(result.data, activeSorts.value);
  syncEditState(result.data);
  // "Select all on page" now means a different set of rows; a later bulk edit
  // could silently hit rows the user can no longer see.
  clearSelection();
  // A changed sort invalidates every offset — page 1 under the new order is not
  // the rows we hold. Reset and replace; never append.
  result.start = 0;
  // fresh: skip the SWR cache read. Returning to a previously-used sort inside
  // the 60s TTL would otherwise paint a cached page OVER the optimistic order,
  // producing a visible double-jump. The local reorder already gave us the
  // instant paint, so the cache read buys nothing here.
  await load({ append: false, fresh: true, silent: true });
}

// Mirrors the date-range popover: plain absolute positioning + a deferred
// outside-click listener. The setTimeout is load-bearing — without it the very
// click that opens the popover matches the handler and closes it again.
function toggleSortPopover() {
  if (sortOpen.value) {
    closeSortPopover();
    return;
  }
  sortOpen.value = true;
  setTimeout(() => {
    document.addEventListener("mousedown", onSortOutsideClick);
  }, 0);
}
function closeSortPopover() {
  sortOpen.value = false;
  document.removeEventListener("mousedown", onSortOutsideClick);
}
function onSortOutsideClick(event) {
  if (!sortRef.value) return;
  if (!sortRef.value.contains(event.target)) closeSortPopover();
}

// Filter popover — same open/close shape as sort, including the setTimeout(0)
// before attaching the listener, without which the click that OPENED the
// popover is itself seen as an outside click and closes it again.
function toggleFilterPopover() {
  if (filterOpen.value) {
    closeFilterPopover();
    return;
  }
  filterOpen.value = true;
  loadFilterLookups();
  setTimeout(() => {
    document.addEventListener("mousedown", onFilterOutsideClick);
  }, 0);
}
function closeFilterPopover() {
  filterOpen.value = false;
  document.removeEventListener("mousedown", onFilterOutsideClick);
}
function onFilterOutsideClick(event) {
  if (!filterRef.value) return;
  if (!filterRef.value.contains(event.target)) closeFilterPopover();
}

// Editing a condition only touches the DRAFT — nothing fetches until Apply,
// exactly like the primary dropdowns. That is deliberate: a filter change is
// two backend queries over ~67k rows, and the user is usually still building
// the row (field, then operator, then value) when the first edit lands.
function setConditions(rows) {
  filterDraft.conditions = cloneConditions(rows);
}

// Teams back both the primary Agent Group dropdown and the popover's link
// choices, so this is fetched on mount. Idempotent and never throws — an empty
// list just means the dropdown has only "All".
async function loadAgentGroups() {
  if (agentGroups.value.length) return;
  try {
    agentGroups.value = (await listAgentGroups()) || [];
  } catch {
    agentGroups.value = [];
  }
}

// Priorities are only needed by the Filter popover, so they stay lazy — most
// sessions never open it.
async function loadFilterLookups() {
  await loadAgentGroups();
  if (!ticketPriorities.value.length) {
    try {
      ticketPriorities.value = await listTicketPriorities();
    } catch {
      ticketPriorities.value = [];
    }
  }
}

function sortIndexFor(key) {
  return activeSorts.value.findIndex((s) => s.key === key);
}
function sortDirFor(key) {
  const index = sortIndexFor(key);
  return index >= 0 ? activeSorts.value[index].direction : "";
}
function ariaSortFor(key) {
  const dir = sortDirFor(key);
  return dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none";
}
function sortHint(key) {
  const label = sortableFieldMap.value[key]?.label || key;
  const dir = sortDirFor(key);
  if (dir === "asc") return `${label}: ascending — click for descending`;
  if (dir === "desc") return `${label}: descending — click to clear`;
  return `Sort by ${label}`;
}

function toggleSortFromHeader(key, event) {
  if (!sortableFieldMap.value[key]) return;
  const current = activeSorts.value;
  // Shift-click appends, so a second key can be added without opening the
  // popover. A plain click replaces — what every list UI does.
  if (event?.shiftKey) {
    const existing = current.findIndex((s) => s.key === key);
    if (existing >= 0) {
      const next = current.map((s, i) =>
        i === existing
          ? { key, direction: s.direction === "asc" ? "desc" : "asc" }
          : s
      );
      return applySort(next);
    }
    if (current.length >= MAX_SORT_TERMS) return;
    return applySort([...current, { key, direction: "asc" }]);
  }
  // Sole active sort on this column → cycle none -> asc -> desc -> none.
  if (current.length === 1 && current[0].key === key) {
    if (current[0].direction === "asc") {
      return applySort([{ key, direction: "desc" }]);
    }
    return applySort([]);
  }
  return applySort([{ key, direction: "asc" }]);
}

// Refresh ONLY the KPI cards / total_count — not the whole page. Used after an
// inline or bulk edit, where the affected rows are already patched + reconciled
// locally, so a full get_tickets_page refetch would just churn the table.
async function refreshSummary() {
  try {
    const summaryData = await callWithRetry(
      "helpdesk.api.unity_helpdesk.get_tickets_summary",
      {
        view: props.view,
        filters: cleanFilters(),
        search: appliedSearch.value,
      },
      { idempotent: true }
    );
    if (summaryData) {
      result.total_count = summaryData.total_count || 0;
      result.cards = summaryData.cards || {};
    }
  } catch {
    // Non-fatal: keep the current cards rather than blanking them.
  }
}

function routeQueryFromState() {
  return {
    ...route.query,
    message_body: undefined,
    status: filters.status || undefined,
    priority: filters.priority || undefined,
    ticket_type: filters.ticket_type || undefined,
    assigned_to: filters.assigned_to || undefined,
    agent_group: filters.agent_group || undefined,
    // Stale keys from links shared before Created By / Created Date moved into the
    // Filter popover. Explicitly undefined so compactQuery strips them instead of
    // the `...route.query` spread carrying a filter the UI can no longer show.
    created_by: undefined,
    created_from: undefined,
    created_to: undefined,
    [CONDITIONS_QUERY_KEY]: encodeConditions(filters.conditions),
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

// Commit the draft filters AND any typed-but-unsubmitted search in one fetch.
async function applyAll() {
  Object.assign(filters, filterDraft);
  // Object.assign copies the array by REFERENCE, which would make the draft and
  // the committed snapshot the same object: every later edit in the popover
  // would silently mutate `filters` too, so filtersDirty could never be true
  // and Apply would stay disabled. Snapshot it instead.
  filters.conditions = cloneConditions(filterDraft.conditions);
  const q = draftSearch.value.trim();
  if (q !== appliedSearch.value) {
    appliedSearch.value = q;
    rememberSearch(q);
  }
  searchFocused.value = false;
  closeSuggestions();
  await replaceRouteOrReload();
}

// Wipe every filter + the search, then reload the base list.
async function clearAll() {
  FILTER_KEYS.forEach((k) => {
    filterDraft[k] = "";
    filters[k] = "";
  });
  filterDraft.conditions = [];
  filters.conditions = [];
  closeFilterPopover();
  draftSearch.value = "";
  appliedSearch.value = "";
  searchFocused.value = false;
  closeSuggestions();
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
    // Without this, returning to the list repaints the PREVIOUS sort order from
    // cache before the fetch corrects it.
    sort: orderByString.value,
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

async function load({ append = false, fresh = false, silent = false } = {}) {
  const requestId = activeRequestId + 1;
  activeRequestId = requestId;
  activeController?.abort();
  activeController = new AbortController();
  // `silent` suppresses the dim/banner for loads the user shouldn't perceive as
  // loading at all (sorting). Counted, not boolean: a second sort click landing
  // while the first refetch is in flight must not un-suppress when the first
  // one settles. Released unconditionally in the finally below.
  if (silent) silentLoads.value += 1;

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
  } else if (usedCache || result.data.length) {
    // Rows already on screen (cache hit, a live-search keystroke, a filter change,
    // or a post-edit refresh) — keep the current list visible and show only the
    // unobtrusive "searching…" indicator. Never blank to the skeleton once
    // populated; that's the stale-while-revalidate feel live search needs.
    reloading.value = true;
  } else {
    // First load only (empty table) → show the skeleton.
    loading.value = true;
  }
  error.value = "";
  reloadPrompt.value = false;
  emptyMessage.value = appliedSearch.value.trim()
    ? `No tickets match “${appliedSearch.value.trim()}”.`
    : "No tickets found.";
  try {
    const params = {
      view: props.view,
      filters: cleanFilters(),
      search: appliedSearch.value,
      page_length: result.page_length,
      start: append ? tickets.value.length : 0,
      order_by: orderByString.value,
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
    // Whether the search hit the relevance-candidate cap; drives the
    // "sorting the first 1,000 matches" note.
    result.search_truncated = !!pageData.search_truncated;
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
    // Released unconditionally — NOT inside the requestId guard below. An
    // aborted silent load would otherwise leak an increment and the loading
    // banner would stay suppressed for the rest of the session.
    if (silent && silentLoads.value > 0) silentLoads.value -= 1;
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
  // `reloading` matters here: a silent sort refetch sets it (not `loading`), and
  // appending page 2 of the new order onto page 1 of the old one would interleave
  // rows that were never a contiguous page.
  if (loading.value || loadingMore.value || reloading.value) return;
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
    // Row is already reconciled from the server response — only the KPI counts
    // need refreshing, so skip the full-list refetch (avoids a whole-table churn).
    refreshSummary();
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

// Elapsed time worded like the upstream Helpdesk list ("20 days ago",
// "2 months ago") — backs the "Created" and "Last Modified" columns. Returns null
// for missing/unparseable values so the cell falls back to a muted dash. dayjs is
// used rather than `new Date()` because it parses Frappe's
// "2026-06-16 19:41:29.810407" shape reliably on every engine.
function relativeTime(value) {
  void nowTick.value; // re-render this cell when the tick advances
  if (!value) return null;
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.fromNow() : null;
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

// Route target for a ticket, carrying the current list filters + which view.
function ticketTo(name) {
  return {
    path: `/tickets/${name}`,
    query: {
      ...routeQueryFromState(),
      list_view: props.view,
    },
  };
}

// Remember the current list order so the detail view's prev/next navigation works.
function rememberTicketNav() {
  sessionStorage.setItem(
    "unity:ticket_nav",
    JSON.stringify({
      ids: tickets.value.map((t) => String(t.name)),
      view: props.view,
    })
  );
}

// Used for clicks on non-link parts of a row. The ticket-id and subject are real
// <RouterLink>s (so native right-click / middle-click work); this covers the rest.
function openTicket(name, event) {
  const target = ticketTo(name);
  // Ctrl / Cmd / middle-click (or Shift) → open in a new browser tab, like a real
  // link. `event.button === 1` covers middle-click via @auxclick.middle.
  if (
    event &&
    (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1)
  ) {
    window.open(router.resolve(target).href, "_blank", "noopener");
    return;
  }
  rememberTicketNav();
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
// Outside-CLICK close (bubble phase) — replaces the full-screen backdrop, which was
// overlaying the page's scroll container and blocking the main scrollbar/wheel. The
// trigger button is @click.stop, so its own click never reaches here.
function _onAssignDocClick(e) {
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
  // Clicking the trigger again closes it (toggle).
  if (assignOpen.value === ticket.name) {
    closeAssign();
    return;
  }
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
  document.addEventListener("click", _onAssignDocClick);
  nextTick(() => assignSearchRef.value?.focus());
}
function closeAssign() {
  if (assignOpen.value === null) return;
  assignOpen.value = null;
  _assignTicket = null;
  assignQuery.value = "";
  window.removeEventListener("scroll", _onOutsideScroll, true);
  window.removeEventListener("resize", closeAssign, true);
  document.removeEventListener("click", _onAssignDocClick);
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
