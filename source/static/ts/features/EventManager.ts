/**
 * Timeline events and group chats CRUD, rendering, and AI suggestion manager.
 *
 * Handles the full lifecycle of timeline events and group chats within
 * the scenario builder: rendering cards with participant pickers,
 * creating/removing/updating entries, and invoking AI endpoints for
 * automated event and group chat generation. Uses the callback registry
 * to invoke LinkChartManager's render function without a direct import.
 * @module
 */

import type { DeviceContactRef, GroupChat, MessageVolume, TimelineEvent } from '../shared/types.js';

import { api } from '../core/ApiClient.js';
import { DEVICE_COLORS, THEME_LABELS, scenario, syncScenario, uid } from '../core/AppState.js';
import { showToast } from '../core/ToastService.js';

import { esc } from '../shared/html-utils.js';
import { getCallback, registerCallback } from '../shared/render-callbacks.js';

// -----------------------------------------------------------------------
// Local types
// -----------------------------------------------------------------------

/**
 * Mutable fields on {@link TimelineEvent} editable from HTML form inputs.
 * Used for type-safe dynamic field assignment in {@link EventManager.updateEventField}.
 */
type MutableEventField = 'description' | 'date' | 'time' | 'encounter_type';

/**
 * Mutable fields on {@link GroupChat} editable from HTML form inputs.
 * Used for type-safe dynamic field assignment in {@link EventManager.updateGroupChat}.
 */
type MutableGroupChatField = 'name' | 'vibe' | 'message_volume' | 'start_date' | 'end_date';

/** Response payload from the AI full-event suggestion endpoint. */
interface AiSuggestEventsResponse {
  events?: Array<{
    date?: string;
    time?: string | null;
    description?: string;
    device_impacts?: Record<string, string>;
    participants?: DeviceContactRef[];
  }>;
}

/** Response payload from the AI group-chat suggestion endpoint. */
interface AiSuggestGroupChatsResponse {
  group_chats?: Array<{
    name?: string;
    members?: DeviceContactRef[];
    origin_event_id?: string;
    start_date?: string;
    end_date?: string;
    message_volume?: string;
    vibe?: string;
    activation_mode?: string;
    auto_pair_threads?: boolean;
    quality_score?: number;
  }>;
  quality?: {
    findings?: unknown[];
  };
}

/** Device roster entry sent to AI endpoints for context. */
interface RosterContact {
  contact_id: string;
  name: string;
  role: string;
  personality_summary?: string;
}

/** Device summary sent to AI endpoints for context. */
interface RosterDevice {
  device_id: string;
  device_label: string;
  owner_name: string;
  contacts: RosterContact[];
}

/** Structured event summary sent to AI group-chat endpoint. */
interface StructuredEvent {
  event_id: string;
  date: string;
  description: string;
  participant_names: string[];
}

// -----------------------------------------------------------------------
// EventManager
// -----------------------------------------------------------------------

/**
 * Manages timeline events CRUD, group chats CRUD, participant picking,
 * and AI-powered suggestion flows.
 *
 * Registers `renderEvents`, `renderGroupChats`, and `toggleEventParticipant`
 * callbacks at construction time so other modules (primarily LinkChartManager)
 * can invoke them without a direct import.
 */
export class EventManager {
  constructor() {
    registerCallback('renderEvents', () => {
      this.renderEvents();
    });
    registerCallback('renderGroupChats', () => {
      this.renderGroupChats();
    });
    registerCallback('toggleEventParticipant', (evIdx, deviceId, contactId) => {
      this.toggleEventParticipant(evIdx, deviceId, contactId);
    });
  }

  // -------------------------------------------------------------------
  // Event rendering
  // -------------------------------------------------------------------

  /**
   * Render the complete events list and timeline bar into the DOM.
   *
   * Rebuilds the events list from the current scenario state,
   * including the timeline bar and individual event cards.
   * Shows an empty-state message when no events exist.
   */
  public renderEvents(): void {
    const list = document.getElementById('events-list');
    const empty = document.getElementById('events-empty');
    const bar = document.getElementById('timeline-bar');
    if (!list || !empty || !bar) return;

    if (scenario.timeline_events.length === 0) {
      list.innerHTML = '';
      bar.innerHTML = '';
      empty.classList.remove('hidden');
      return;
    }

    empty.classList.add('hidden');
    bar.innerHTML = this.renderTimelineBar();
    list.innerHTML = scenario.timeline_events.map((ev, idx) => this.renderEventCard(ev, idx)).join('');
  }

  /**
   * Build the HTML for timeline bar event markers.
   *
   * Positions each event as a percentage along the scenario date range.
   *
   * @returns HTML string for all timeline bar markers.
   */
  private renderTimelineBar(): string {
    const start = new Date(scenario.generation_settings.date_start).getTime();
    const end = new Date(scenario.generation_settings.date_end).getTime();
    const range = end - start || 1;

    return scenario.timeline_events
      .map((ev, idx) => {
        const evDate = new Date(ev.date).getTime();
        const pct = Math.max(0, Math.min(100, ((evDate - start) / range) * 100));
        return `<div class="timeline-event" style="left:${String(pct)}%" onclick="scrollToEvent(${String(idx)})" title="${ev.date}">
        <div class="timeline-event-label">${(ev.date || '').substring(5)}</div></div>`;
      })
      .join('');
  }

  /**
   * Build the HTML for a single event card.
   *
   * Includes participant picker, description textarea, date/time inputs,
   * encounter type select, per-device impact textareas, and AI fill button.
   *
   * @param ev - The timeline event to render.
   * @param idx - Zero-based index of the event in the scenario.
   * @returns HTML string for the event card.
   */
  private renderEventCard(ev: TimelineEvent, idx: number): string {
    const timeDisplay = ev.time ? ` at ${esc(ev.time)}` : '';
    const idxStr = String(idx);

    const deviceImpacts = scenario.devices
      .map(
        (dev) => `
      <div class="form-group" style="margin-top:0.375rem;">
        <label>${esc(dev.device_label)}</label>
        <textarea placeholder="How this event appears on ${esc(dev.device_label)}..."
          oninput="updateEventDeviceImpact(${idxStr},'${dev.id}',this.value)">${esc(ev.device_impacts[dev.id] || '')}</textarea>
      </div>`
      )
      .join('');

    return `
      <div class="card event-card" id="event-${idxStr}">
        <div class="card-header">
          <h3>${esc(ev.date || 'No date')}${timeDisplay}</h3>
          <button class="btn btn-danger btn-sm" onclick="removeEvent(${idxStr})">Remove</button>
        </div>
        <div style="margin-bottom:0.75rem;">
          <label style="font-size:0.75rem;font-weight:500;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.03125rem;">
            Participants (select people involved in this event)
          </label>
          <div style="display:flex;flex-wrap:wrap;gap:0.375rem;margin-top:0.375rem;">
            ${this.renderParticipantPicker(ev, idx)}
          </div>
        </div>
        <div class="form-group"><label>Description</label>
          <textarea oninput="updateEventField(${idxStr},'description',this.value)">${esc(ev.description || '')}</textarea></div>
        <div class="row">
          <div class="form-group"><label>Date</label>
            <input type="date" value="${ev.date || ''}" onchange="updateEventField(${idxStr},'date',this.value)"></div>
          <div class="form-group"><label>Time (optional)</label>
            <input type="text" value="${esc(ev.time || '')}" placeholder="HH:MM"
              onchange="updateEventField(${idxStr},'time',this.value||null)"></div>
          <div class="form-group"><label>Encounter Type</label>
            <select onchange="updateEventField(${idxStr},'encounter_type',this.value)">
              <option value="planned" ${ev.encounter_type === 'planned' ? 'selected' : ''}>Planned Meeting</option>
              <option value="chance_encounter" ${ev.encounter_type === 'chance_encounter' ? 'selected' : ''}>Chance Encounter</option>
              <option value="near_miss" ${ev.encounter_type === 'near_miss' ? 'selected' : ''}>Near Miss</option>
            </select></div>
        </div>
        <div style="margin-top:0.5rem;">
          <label style="font-size:0.75rem;font-weight:500;color:var(--text-secondary);text-transform:uppercase;">Per-Device Impact</label>
          ${deviceImpacts}
        </div>
        <div style="margin-top:0.5rem;">
          <button class="btn btn-ai btn-sm" onclick="aiSuggestEventDetails(${idxStr})">AI Fill Details</button>
        </div>
      </div>`;
  }

  /**
   * Build the HTML for participant picker buttons within an event card.
   *
   * Shows one button per device owner and contact, highlighting those
   * already selected as participants.
   *
   * @param ev - The timeline event to render participants for.
   * @param evIdx - Zero-based index of the event.
   * @returns HTML string for the participant picker buttons.
   */
  private renderParticipantPicker(ev: TimelineEvent, evIdx: number): string {
    const participants = ev.participants;
    let html = '';

    for (const [devIdx, dev] of scenario.devices.entries()) {
      const color = DEVICE_COLORS[devIdx % DEVICE_COLORS.length];
      const evIdxStr = String(evIdx);

      const ownerSelected = participants.some((p) => p.device_id === dev.id && p.contact_id === '__owner__');
      html += `<button class="btn btn-sm ${ownerSelected ? 'btn-primary' : ''}"
        style="${ownerSelected ? '' : `border-color:${color};color:${color}`}"
        onclick="toggleEventParticipant(${evIdxStr},'${dev.id}','__owner__')">
        ${esc(dev.owner_name || dev.device_label)}</button>`;

      for (const c of dev.contacts) {
        const selected = participants.some((p) => p.device_id === dev.id && p.contact_id === c.id);
        html += `<button class="btn btn-sm ${selected ? 'btn-primary' : ''}"
          style="${selected ? '' : `border-color:${color};color:${color}`}"
          onclick="toggleEventParticipant(${evIdxStr},'${dev.id}','${c.id}')">
          ${esc(c.name || c.actor_id)}</button>`;
      }
    }

    return html;
  }

  // -------------------------------------------------------------------
  // Event CRUD
  // -------------------------------------------------------------------

  /**
   * Toggle a participant's inclusion in a timeline event.
   *
   * If the participant is already present, they are removed.
   * Otherwise they are added. Re-renders events and link chart afterward.
   *
   * @param evIdx - Zero-based index of the timeline event.
   * @param deviceId - The device ID of the participant.
   * @param contactId - The contact ID of the participant.
   */
  public toggleEventParticipant(evIdx: number, deviceId: string, contactId: string): void {
    const ev = scenario.timeline_events[evIdx];
    if (!ev) return;

    const existing = ev.participants.findIndex((p) => p.device_id === deviceId && p.contact_id === contactId);

    if (existing >= 0) {
      ev.participants.splice(existing, 1);
    } else {
      ev.participants.push({
        device_id: deviceId,
        contact_id: contactId,
      });
    }

    this.renderEvents();
    getCallback('renderLinkChart')?.();
    void syncScenario();
  }

  /**
   * Scroll the page to bring a specific event card into view.
   *
   * @param idx - Zero-based index of the event to scroll to.
   */
  public scrollToEvent(idx: number): void {
    const el = document.getElementById(`event-${String(idx)}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  /**
   * Add a new empty timeline event with default values.
   *
   * The event is appended to the scenario and the events list is
   * re-rendered. The link chart is not updated since the new event
   * has no participants.
   */
  public addEvent(): void {
    scenario.timeline_events.push({
      id: uid(),
      date: scenario.generation_settings.date_start,
      time: null,
      description: '',
      encounter_type: 'planned',
      device_impacts: {},
      involved_contacts: {},
      participants: [],
    });
    this.renderEvents();
    void syncScenario();
  }

  /**
   * Remove a timeline event by index.
   *
   * Re-renders both the events list and the link chart to
   * reflect the removal of any co-presence edges.
   *
   * @param idx - Zero-based index of the event to remove.
   */
  public removeEvent(idx: number): void {
    scenario.timeline_events.splice(idx, 1);
    this.renderEvents();
    getCallback('renderLinkChart')?.();
    void syncScenario();
  }

  /**
   * Update a single field on a timeline event.
   *
   * Performs dynamic property assignment for fields edited via inline
   * HTML inputs. When the date field changes, the events list is
   * re-rendered to update timeline bar positions.
   *
   * @param idx - Zero-based index of the event.
   * @param field - The event property to modify.
   * @param value - The new value from the form input.
   */
  public updateEventField(idx: number, field: MutableEventField, value: string | null): void {
    const ev = scenario.timeline_events[idx];
    if (!ev) return;

    const record = ev as unknown as Record<string, string | null>;
    record[field] = value;

    if (field === 'date') this.renderEvents();
    getCallback('renderLinkChart')?.();
    void syncScenario();
  }

  /**
   * Update the per-device impact text for a timeline event.
   *
   * @param idx - Zero-based index of the event.
   * @param deviceId - The device whose impact text to set.
   * @param value - The new impact description.
   */
  public updateEventDeviceImpact(idx: number, deviceId: string, value: string): void {
    const ev = scenario.timeline_events[idx];
    if (!ev) return;
    ev.device_impacts[deviceId] = value;
    void syncScenario();
  }

  // -------------------------------------------------------------------
  // AI event suggestion
  // -------------------------------------------------------------------

  /**
   * Open the AI events suggestion modal with pre-filled defaults.
   *
   * Populates the modal form with the current scenario's theme label,
   * date range, and a default event count of 6.
   */
  public aiSuggestEvents(): void {
    const themeLabel = THEME_LABELS[scenario.theme] || scenario.theme || 'Slice of Life';

    const themeLabelEl = document.getElementById('modal-theme-label');
    if (themeLabelEl) themeLabelEl.textContent = `${themeLabel} theme`;

    const startEl = document.getElementById('modal-ev-start') as HTMLInputElement | null;
    if (startEl) startEl.value = scenario.generation_settings.date_start;

    const endEl = document.getElementById('modal-ev-end') as HTMLInputElement | null;
    if (endEl) endEl.value = scenario.generation_settings.date_end;

    const countEl = document.getElementById('modal-ev-count') as HTMLInputElement | null;
    if (countEl) countEl.value = '6';

    const modal = document.getElementById('ai-events-modal');
    if (modal) modal.classList.remove('hidden');
  }

  /**
   * Close the AI events suggestion modal.
   */
  public closeEventsModal(): void {
    const modal = document.getElementById('ai-events-modal');
    if (modal) modal.classList.add('hidden');
  }

  /**
   * Submit the AI events suggestion request and apply the results.
   *
   * Reads form values from the modal, calls the API endpoint,
   * and appends generated events to the scenario's timeline.
   */
  public async confirmAiSuggestEvents(): Promise<void> {
    const startEl = document.getElementById('modal-ev-start') as HTMLInputElement | null;
    const endEl = document.getElementById('modal-ev-end') as HTMLInputElement | null;
    const countEl = document.getElementById('modal-ev-count') as HTMLInputElement | null;

    const dateStart = startEl?.value ?? '';
    const dateEnd = endEl?.value ?? '';
    const count = parseInt(countEl?.value ?? '6', 10) || 6;
    this.closeEventsModal();

    const rosterDevices = this.buildRosterDevices();
    const existingDescriptions = scenario.timeline_events.map((e) => e.description).filter(Boolean);

    showToast(`Generating ${String(count)} events (${dateStart} \u2192 ${dateEnd})...`);

    try {
      const res = await api<AiSuggestEventsResponse>('POST', '/api/ai/suggest-full-events', {
        devices: rosterDevices,
        date_start: dateStart,
        date_end: dateEnd,
        count,
        existing_descriptions: existingDescriptions,
        theme: scenario.theme || 'slice-of-life',
        culture: scenario.culture || 'american',
        story_arc: scenario.story_arc || '',
      });

      if (res.events && Array.isArray(res.events)) {
        for (const ev of res.events) {
          scenario.timeline_events.push({
            id: uid(),
            date: ev.date ?? '',
            time: ev.time ?? null,
            description: ev.description ?? '',
            encounter_type: 'planned',
            device_impacts: ev.device_impacts ?? {},
            involved_contacts: {},
            participants: ev.participants ?? [],
          });
        }
        this.renderEvents();
        getCallback('renderLinkChart')?.();
        void syncScenario();
        showToast(`Added ${String(res.events.length)} fully-detailed events`);
      }
    } catch {
      // Toast shown by api()
    }
  }

  /**
   * AI-fill details for a single existing timeline event.
   *
   * Builds a roster of only the devices/contacts involved in the event,
   * calls the AI endpoint for a single event, and merges the suggested
   * description and per-device impacts into the existing event.
   *
   * @param evIdx - Zero-based index of the event to fill.
   */
  public async aiSuggestEventDetails(evIdx: number): Promise<void> {
    const ev = scenario.timeline_events[evIdx];
    if (!ev) return;

    if (ev.participants.length === 0) {
      showToast('Select participants first');
      return;
    }

    const rosterDevices = this.buildEventRoster(ev);
    showToast('AI filling event details...');

    try {
      const res = await api<AiSuggestEventsResponse>('POST', '/api/ai/suggest-full-events', {
        devices: rosterDevices,
        date_start: ev.date || scenario.generation_settings.date_start,
        date_end: ev.date || scenario.generation_settings.date_end,
        count: 1,
        existing_descriptions: [],
        theme: scenario.theme || 'slice-of-life',
        culture: scenario.culture || 'american',
        story_arc: scenario.story_arc || '',
      });

      this.applyEventDetails(ev, res);
    } catch {
      // Toast shown by api()
    }
  }

  // -------------------------------------------------------------------
  // Group chat rendering
  // -------------------------------------------------------------------

  /**
   * Render the complete group chats list into the DOM.
   *
   * Rebuilds the group chats panel from the current scenario state.
   * Shows an empty-state message when no group chats exist.
   */
  public renderGroupChats(): void {
    const list = document.getElementById('group-chats-list');
    const empty = document.getElementById('group-chats-empty');
    if (!list) return;

    const groups = scenario.group_chats;

    if (groups.length === 0) {
      list.innerHTML = '';
      if (empty) empty.classList.remove('hidden');
      return;
    }

    if (empty) empty.classList.add('hidden');
    list.innerHTML = groups.map((gc, idx) => this.renderGroupChatCard(gc, idx)).join('');
  }

  /**
   * Build the HTML for a single group chat card.
   *
   * Includes name input, vibe input, volume select, date range,
   * and a member picker with toggle buttons.
   *
   * @param gc - The group chat to render.
   * @param idx - Zero-based index of the group chat.
   * @returns HTML string for the group chat card.
   */
  private renderGroupChatCard(gc: GroupChat, idx: number): string {
    const idxStr = String(idx);
    const memberNames = this.resolveGroupMemberNames(gc);

    return `
      <div class="card" style="border-left:3px solid var(--purple);margin-bottom:0.625rem;">
        <div class="card-header">
          <h4 style="margin:0;">
            <input type="text" value="${esc(gc.name)}" placeholder="Group name (e.g. The Crew)"
              style="background:transparent;border:none;color:var(--text-primary);font-weight:600;font-size:0.875rem;width:12.5rem;"
              oninput="updateGroupChat(${idxStr},'name',this.value)">
          </h4>
          <button class="btn btn-danger btn-icon btn-sm" onclick="removeGroupChat(${idxStr})">&times;</button>
        </div>
        <div class="row">
          <div class="form-group" style="flex:1;">
            <label>Vibe / Dynamic</label>
            <input type="text" value="${esc(gc.vibe || '')}" placeholder="e.g. casual banter, work coordination"
              oninput="updateGroupChat(${idxStr},'vibe',this.value)">
          </div>
          <div class="form-group">
            <label>Volume</label>
            <select onchange="updateGroupChat(${idxStr},'message_volume',this.value)">
              <option value="heavy" ${gc.message_volume === 'heavy' ? 'selected' : ''}>Heavy</option>
              <option value="regular" ${gc.message_volume === 'regular' ? 'selected' : ''}>Regular</option>
              <option value="light" ${gc.message_volume === 'light' ? 'selected' : ''}>Light</option>
              <option value="minimal" ${gc.message_volume === 'minimal' ? 'selected' : ''}>Minimal</option>
            </select>
          </div>
          <div class="form-group">
            <label>Start Date</label>
            <input type="date" value="${gc.start_date || ''}" onchange="updateGroupChat(${idxStr},'start_date',this.value)">
          </div>
          <div class="form-group">
            <label>End Date</label>
            <input type="date" value="${gc.end_date || ''}" placeholder="ongoing" onchange="updateGroupChat(${idxStr},'end_date',this.value)">
          </div>
        </div>
        <div class="form-group">
          <label>Members (${String(memberNames.length)}): ${esc(memberNames.join(', '))}</label>
          <div style="display:flex;flex-wrap:wrap;gap:0.25rem;margin-top:0.375rem;">
            ${this.renderGroupMemberPicker(idx)}
          </div>
        </div>
      </div>`;
  }

  /**
   * Resolve member references to display names for a group chat.
   *
   * @param gc - The group chat whose members to resolve.
   * @returns Array of display names for all members.
   */
  private resolveGroupMemberNames(gc: GroupChat): string[] {
    return gc.members.map((m) => {
      for (const dev of scenario.devices) {
        if (dev.id !== m.device_id) continue;
        if (m.contact_id === '__owner__') return dev.owner_name || `${dev.device_label} Owner`;
        const c = dev.contacts.find((x) => x.id === m.contact_id);
        if (c) return c.name || 'Unnamed';
      }
      return '?';
    });
  }

  /**
   * Build the HTML for group member picker buttons.
   *
   * Shows one button per device owner and contact, highlighting those
   * already selected as members.
   *
   * @param gcIdx - Zero-based index of the group chat.
   * @returns HTML string for the member picker buttons.
   */
  private renderGroupMemberPicker(gcIdx: number): string {
    const gc = scenario.group_chats[gcIdx];
    if (!gc) return '';

    const members = gc.members;
    let html = '';
    const gcIdxStr = String(gcIdx);

    for (const [devIdx, dev] of scenario.devices.entries()) {
      const color = DEVICE_COLORS[devIdx % DEVICE_COLORS.length];

      const ownerIn = members.some((m) => m.device_id === dev.id && m.contact_id === '__owner__');
      html += `<button class="btn btn-sm ${ownerIn ? 'btn-success' : ''}"
        style="font-size:0.6875rem;border-color:${color};" title="${esc(dev.device_label)} owner"
        onclick="toggleGroupMember(${gcIdxStr},'${dev.id}','__owner__')">
        ${esc(dev.owner_name || dev.device_label)} \uD83D\uDCF1</button>`;

      for (const c of dev.contacts) {
        const isIn = members.some((m) => m.device_id === dev.id && m.contact_id === c.id);
        html += `<button class="btn btn-sm ${isIn ? 'btn-success' : ''}"
          style="font-size:0.6875rem;border-color:${color};"
          onclick="toggleGroupMember(${gcIdxStr},'${dev.id}','${c.id}')">
          ${esc(c.name || 'Unnamed')}</button>`;
      }
    }

    return html;
  }

  // -------------------------------------------------------------------
  // Group chat CRUD
  // -------------------------------------------------------------------

  /**
   * Toggle a member's inclusion in a group chat.
   *
   * If the member is already present, they are removed.
   * Otherwise they are added. Re-renders group chats and link chart.
   *
   * @param gcIdx - Zero-based index of the group chat.
   * @param deviceId - The device ID of the member.
   * @param contactId - The contact ID of the member.
   */
  public toggleGroupMember(gcIdx: number, deviceId: string, contactId: string): void {
    const gc = scenario.group_chats[gcIdx];
    if (!gc) return;

    const existing = gc.members.findIndex((m) => m.device_id === deviceId && m.contact_id === contactId);

    if (existing >= 0) {
      gc.members.splice(existing, 1);
    } else {
      gc.members.push({
        device_id: deviceId,
        contact_id: contactId,
      });
    }

    this.renderGroupChats();
    getCallback('renderLinkChart')?.();
    void syncScenario();
  }

  /**
   * Add a new empty group chat with default values.
   *
   * The group chat is appended to the scenario and the list is re-rendered.
   */
  public addGroupChat(): void {
    scenario.group_chats.push({
      id: uid(),
      name: '',
      members: [],
      origin_event_id: '',
      start_date: scenario.generation_settings.date_start || '2025-01-01',
      end_date: '',
      message_volume: 'regular',
      vibe: '',
    });
    this.renderGroupChats();
    void syncScenario();
  }

  /**
   * Remove a group chat by index.
   *
   * Re-renders both the group chats list and the link chart.
   *
   * @param idx - Zero-based index of the group chat to remove.
   */
  public removeGroupChat(idx: number): void {
    scenario.group_chats.splice(idx, 1);
    this.renderGroupChats();
    getCallback('renderLinkChart')?.();
    void syncScenario();
  }

  /**
   * Update a single field on a group chat.
   *
   * Performs dynamic property assignment for fields edited via inline
   * HTML inputs.
   *
   * @param idx - Zero-based index of the group chat.
   * @param field - The group chat property to modify.
   * @param value - The new value from the form input.
   */
  public updateGroupChat(idx: number, field: MutableGroupChatField, value: string): void {
    const gc = scenario.group_chats[idx];
    if (!gc) return;

    const record = gc as unknown as Record<string, string>;
    record[field] = value;
    void syncScenario();
  }

  /**
   * AI-suggest group chats based on the scenario's devices, events, and theme.
   *
   * Calls the AI endpoint with a summary of the cast, events, and devices,
   * then appends the generated group chats to the scenario.
   */
  public async aiSuggestGroupChats(): Promise<void> {
    if (scenario.devices.length === 0) {
      showToast('Add devices and contacts first');
      return;
    }

    const castSummary = this.buildCastSummary();
    const eventsSummary = this.buildEventsSummary();
    const eventsStructured = this.buildStructuredEvents();

    showToast('AI suggesting group chats...');

    try {
      const res = await api<AiSuggestGroupChatsResponse>('POST', '/api/ai/suggest-group-chats', {
        theme: scenario.theme || 'slice-of-life',
        culture: scenario.culture || 'american',
        story_arc: scenario.story_arc || '',
        cast_summary: castSummary,
        events_summary: eventsSummary,
        events: eventsStructured,
        devices: this.buildRosterDevicesSimple(),
      });

      this.processGroupChatResponse(res);
    } catch {
      // Toast shown by api()
    }
  }

  // -------------------------------------------------------------------
  // Private: AI helpers
  // -------------------------------------------------------------------

  /**
   * Build the full device roster with personality summaries for AI endpoints.
   *
   * @returns Array of roster entries for all devices and their contacts.
   */
  private buildRosterDevices(): RosterDevice[] {
    return scenario.devices.map((dev) => ({
      device_id: dev.id,
      device_label: dev.device_label,
      owner_name: dev.owner_name || '',
      contacts: dev.contacts.map((c) => ({
        contact_id: c.id,
        name: c.name || '',
        role: c.role || '',
        personality_summary: c.personality?.personality_summary || '',
      })),
    }));
  }

  /**
   * Build a simplified device roster without personality data.
   *
   * Used by the group chat suggestion endpoint which doesn't need
   * per-contact personality summaries.
   *
   * @returns Array of simplified roster entries.
   */
  private buildRosterDevicesSimple(): RosterDevice[] {
    return scenario.devices.map((d) => ({
      device_id: d.id,
      device_label: d.device_label,
      owner_name: d.owner_name || '',
      contacts: d.contacts.map((c) => ({
        contact_id: c.id,
        name: c.name || '',
        role: c.role || '',
      })),
    }));
  }

  /**
   * Build a focused roster of only the devices/contacts involved in one event.
   *
   * @param ev - The event whose participants determine the roster scope.
   * @returns Array of roster entries for involved devices only.
   */
  private buildEventRoster(ev: TimelineEvent): RosterDevice[] {
    const involvedDeviceIds = [...new Set(ev.participants.map((p) => p.device_id))];
    const result: RosterDevice[] = [];

    for (const devId of involvedDeviceIds) {
      const dev = scenario.devices.find((d) => d.id === devId);
      if (!dev) continue;

      const relevantContacts = ev.participants
        .filter((p) => p.device_id === devId && p.contact_id !== '__owner__')
        .map((p) => {
          const c = dev.contacts.find((x) => x.id === p.contact_id);
          return {
            contact_id: c?.id ?? p.contact_id,
            name: c?.name ?? '',
            role: c?.role ?? '',
            personality_summary: c?.personality?.personality_summary ?? '',
          };
        });

      const includesOwner = ev.participants.some((p) => p.device_id === devId && p.contact_id === '__owner__');

      result.push({
        device_id: dev.id,
        device_label: dev.device_label,
        owner_name: includesOwner ? dev.owner_name || '' : '',
        contacts: relevantContacts,
      });
    }

    return result;
  }

  /**
   * Apply AI-suggested details to an existing timeline event.
   *
   * Merges the first suggested event's description and device impacts
   * into the target event without overwriting existing values.
   *
   * @param ev - The timeline event to update.
   * @param res - The API response containing suggestions.
   */
  private applyEventDetails(ev: TimelineEvent, res: AiSuggestEventsResponse): void {
    const suggested = res.events?.[0];
    if (!suggested) return;

    if (suggested.description) ev.description = suggested.description;
    if (!ev.date && suggested.date) ev.date = suggested.date;
    if (!ev.time && suggested.time) ev.time = suggested.time;

    if (suggested.device_impacts) {
      for (const [devId, impact] of Object.entries(suggested.device_impacts)) {
        ev.device_impacts[devId] = impact;
      }
    }

    this.renderEvents();
    getCallback('renderLinkChart')?.();
    void syncScenario();
    showToast('Event details filled (description + device impacts)');
  }

  /**
   * Build a human-readable cast summary for AI context.
   *
   * @returns Multi-line string describing each device's owner and contacts.
   */
  private buildCastSummary(): string {
    return scenario.devices
      .map((dev) => {
        const contacts = dev.contacts.map((c) => `${c.name || 'unnamed'} (${c.role || 'contact'})`).join(', ');
        return `${dev.owner_name || 'unnamed'}'s phone: contacts are ${contacts}`;
      })
      .join('\n');
  }

  /**
   * Build a date-prefixed event summary for AI context.
   *
   * @returns Multi-line string of "date: description" entries.
   */
  private buildEventsSummary(): string {
    return scenario.timeline_events
      .filter((e) => e.description)
      .map((e) => `${e.date}: ${e.description}`)
      .join('\n');
  }

  /**
   * Build structured event data for the AI group-chat endpoint.
   *
   * Resolves participant references to display names for richer context.
   *
   * @returns Array of structured event summaries.
   */
  private buildStructuredEvents(): StructuredEvent[] {
    return scenario.timeline_events
      .filter((e) => e.description)
      .map((e) => {
        const participantNames = e.participants
          .map((p) => {
            const pd = scenario.devices.find((d) => d.id === p.device_id);
            if (!pd) return '';
            if (p.contact_id === '__owner__') return pd.owner_name || pd.device_label;
            const pc = pd.contacts.find((c) => c.id === p.contact_id);
            return pc ? pc.name || pc.actor_id : '';
          })
          .filter(Boolean);

        return {
          event_id: e.id || '',
          date: e.date || '',
          description: e.description || '',
          participant_names: participantNames,
        };
      });
  }

  /**
   * Process the AI group-chat suggestion response and apply results.
   *
   * Appends generated group chats to the scenario, shows quality
   * warnings if present, and re-renders.
   *
   * @param res - The API response containing group chat suggestions.
   */
  private processGroupChatResponse(res: AiSuggestGroupChatsResponse): void {
    if (!res.group_chats || !Array.isArray(res.group_chats)) return;

    for (const gc of res.group_chats) {
      scenario.group_chats.push({
        id: uid(),
        name: gc.name ?? '',
        members: gc.members ?? [],
        origin_event_id: gc.origin_event_id ?? '',
        start_date: gc.start_date ?? scenario.generation_settings.date_start,
        end_date: gc.end_date ?? '',
        message_volume: (gc.message_volume || 'regular') as MessageVolume,
        vibe: gc.vibe ?? '',
        activation_mode: gc.activation_mode ?? 'event_time',
        auto_pair_threads: gc.auto_pair_threads !== false,
        quality_score: gc.quality_score ?? 1.0,
      });
    }

    if (res.quality?.findings?.length) {
      showToast(`Group/event quality warnings: ${String(res.quality.findings.length)}`);
    }

    this.renderGroupChats();
    getCallback('renderLinkChart')?.();
    void syncScenario();
    showToast(`Added ${String(res.group_chats.length)} group chats`);
  }
}

// -----------------------------------------------------------------------
// Module singleton & convenience exports
// -----------------------------------------------------------------------

/** Default singleton used by the convenience exports. */
const defaultManager = new EventManager();

/**
 * Render the events list and timeline bar.
 * Delegates to the default {@link EventManager} singleton.
 */
export function renderEvents(): void {
  defaultManager.renderEvents();
}

/**
 * Render the group chats list.
 * Delegates to the default {@link EventManager} singleton.
 */
export function renderGroupChats(): void {
  defaultManager.renderGroupChats();
}

/**
 * Toggle a participant in a timeline event.
 * Delegates to the default {@link EventManager} singleton.
 *
 * @param evIdx - Zero-based index of the timeline event.
 * @param deviceId - The device ID of the participant.
 * @param contactId - The contact ID of the participant.
 */
export function toggleEventParticipant(evIdx: number, deviceId: string, contactId: string): void {
  defaultManager.toggleEventParticipant(evIdx, deviceId, contactId);
}

/**
 * Scroll the page to a specific event card.
 * Delegates to the default {@link EventManager} singleton.
 *
 * @param idx - Zero-based index of the event.
 */
export function scrollToEvent(idx: number): void {
  defaultManager.scrollToEvent(idx);
}

/**
 * Add a new empty timeline event.
 * Delegates to the default {@link EventManager} singleton.
 */
export function addEvent(): void {
  defaultManager.addEvent();
}

/**
 * Remove a timeline event by index.
 * Delegates to the default {@link EventManager} singleton.
 *
 * @param idx - Zero-based index of the event.
 */
export function removeEvent(idx: number): void {
  defaultManager.removeEvent(idx);
}

/**
 * Update a field on a timeline event.
 * Delegates to the default {@link EventManager} singleton.
 *
 * @param idx - Zero-based index of the event.
 * @param field - The event property to modify.
 * @param value - The new value from the form input.
 */
export function updateEventField(idx: number, field: MutableEventField, value: string | null): void {
  defaultManager.updateEventField(idx, field, value);
}

/**
 * Update per-device impact text for a timeline event.
 * Delegates to the default {@link EventManager} singleton.
 *
 * @param idx - Zero-based index of the event.
 * @param deviceId - The device whose impact text to set.
 * @param value - The new impact description.
 */
export function updateEventDeviceImpact(idx: number, deviceId: string, value: string): void {
  defaultManager.updateEventDeviceImpact(idx, deviceId, value);
}

/**
 * Open the AI events suggestion modal.
 * Delegates to the default {@link EventManager} singleton.
 */
export function aiSuggestEvents(): void {
  defaultManager.aiSuggestEvents();
}

/**
 * Close the AI events suggestion modal.
 * Delegates to the default {@link EventManager} singleton.
 */
export function closeEventsModal(): void {
  defaultManager.closeEventsModal();
}

/**
 * Submit the AI events suggestion request.
 * Delegates to the default {@link EventManager} singleton.
 *
 * @returns Promise that resolves when the suggestion completes.
 */
export function confirmAiSuggestEvents(): Promise<void> {
  return defaultManager.confirmAiSuggestEvents();
}

/**
 * AI-fill details for a single event.
 * Delegates to the default {@link EventManager} singleton.
 *
 * @param evIdx - Zero-based index of the event.
 * @returns Promise that resolves when the fill completes.
 */
export function aiSuggestEventDetails(evIdx: number): Promise<void> {
  return defaultManager.aiSuggestEventDetails(evIdx);
}

/**
 * Toggle a member in a group chat.
 * Delegates to the default {@link EventManager} singleton.
 *
 * @param gcIdx - Zero-based index of the group chat.
 * @param deviceId - The device ID of the member.
 * @param contactId - The contact ID of the member.
 */
export function toggleGroupMember(gcIdx: number, deviceId: string, contactId: string): void {
  defaultManager.toggleGroupMember(gcIdx, deviceId, contactId);
}

/**
 * Add a new empty group chat.
 * Delegates to the default {@link EventManager} singleton.
 */
export function addGroupChat(): void {
  defaultManager.addGroupChat();
}

/**
 * Remove a group chat by index.
 * Delegates to the default {@link EventManager} singleton.
 *
 * @param idx - Zero-based index of the group chat.
 */
export function removeGroupChat(idx: number): void {
  defaultManager.removeGroupChat(idx);
}

/**
 * Update a field on a group chat.
 * Delegates to the default {@link EventManager} singleton.
 *
 * @param idx - Zero-based index of the group chat.
 * @param field - The group chat property to modify.
 * @param value - The new value from the form input.
 */
export function updateGroupChat(idx: number, field: MutableGroupChatField, value: string): void {
  defaultManager.updateGroupChat(idx, field, value);
}

/**
 * AI-suggest group chats based on scenario context.
 * Delegates to the default {@link EventManager} singleton.
 *
 * @returns Promise that resolves when the suggestion completes.
 */
export function aiSuggestGroupChats(): Promise<void> {
  return defaultManager.aiSuggestGroupChats();
}
