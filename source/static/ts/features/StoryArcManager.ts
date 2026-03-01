/**
 * Story arc UI, AI story arc generation, and character arc management.
 *
 * Renders the global story arc textarea and per-character arc fields,
 * provides a modal for configuring AI story arc generation, and handles
 * both global and per-character arc generation via the backend API.
 * @module
 */

import { DEVICE_COLORS, scenario, syncScenario, THEME_LABELS } from '../core/AppState.js';
import { api } from '../core/ApiClient.js';
import { showToast } from '../core/ToastService.js';
import { esc } from '../shared/html-utils.js';

// ---------------------------------------------------------------------------
// Supporting types
// ---------------------------------------------------------------------------

/** Response shape from the `/api/ai/generate-story-arc` endpoint. */
interface StoryArcResponse {
  readonly story_arc: unknown;
}

/** Response shape from the `/api/ai/generate-character-arcs` endpoint. */
interface CharacterArcsResponse {
  readonly arcs: Record<string, unknown>;
}

/** Collected standalone and required character name lists. */
interface CharacterNameSets {
  readonly standalone: string[];
  readonly required: string[];
}

// ---------------------------------------------------------------------------
// Module-level helpers
// ---------------------------------------------------------------------------

/**
 * Read the `.value` of an input, select, or textarea element by DOM ID.
 *
 * @param id - The element's DOM ID.
 * @returns The current value, or empty string when not found.
 */
function getElementValue(id: string): string {
  const el = document.getElementById(id);
  if (el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement) {
    return el.value;
  }
  return '';
}

/**
 * Set the `.value` of an input, select, or textarea element by DOM ID.
 *
 * @param id - The element's DOM ID.
 * @param value - The value to assign.
 */
function setElementValue(id: string, value: string): void {
  const el = document.getElementById(id);
  if (el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement) {
    el.value = value;
  }
}

/**
 * Coerce an unknown API value into a display string.
 *
 * Objects are JSON-serialised with indentation; primitives are
 * stringified directly, falling back to empty string for nullish values.
 *
 * @param v - The value to coerce.
 * @returns A human-readable string representation.
 */
function coerceToString(v: unknown): string {
  if (typeof v === 'object' && v !== null) {
    return JSON.stringify(v, null, 2);
  }
  return typeof v === 'string' ? v : String(v ?? '');
}

/**
 * Look up a character arc by name from the primary and lowercase-keyed maps.
 *
 * @param arcs - The primary arcs record keyed by exact character name.
 * @param byLower - The same arcs record re-keyed by lowercased name.
 * @param name - The character name to look up.
 * @returns The matching arc value, or empty string when not found.
 */
function lookupArc(arcs: Record<string, unknown>, byLower: Record<string, unknown>, name: string): unknown {
  const key = String(name || '').trim();
  if (!key) return '';
  return arcs[key] ?? byLower[key.toLowerCase()] ?? '';
}

// ---------------------------------------------------------------------------
// StoryArcManager
// ---------------------------------------------------------------------------

/**
 * Manages the story arc panel, AI generation modal, and character arcs.
 *
 * Provides rendering of the global story arc textarea and per-character
 * arc fields, modal lifecycle management for the AI story arc generator,
 * and integration with the backend for both global and character-level
 * arc generation.
 */
export class StoryArcManager {
  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  /**
   * Render the global story arc textarea and all character arc fields.
   *
   * Populates the global textarea with {@link scenario.story_arc} and
   * builds per-device / per-contact arc fields in the character arcs list.
   * Shows or hides the empty-state placeholder as needed.
   */
  public renderStoryArc(): void {
    const globalBox = document.getElementById('story-arc-global') as HTMLTextAreaElement | null;
    if (globalBox) globalBox.value = scenario.story_arc || '';

    const list = document.getElementById('character-arcs-list');
    const empty = document.getElementById('character-arcs-empty');
    if (!list) return;

    if (scenario.devices.length === 0) {
      list.innerHTML = '';
      if (empty) empty.classList.remove('hidden');
      return;
    }
    if (empty) empty.classList.add('hidden');

    const parts: string[] = [];
    scenario.devices.forEach((dev, devIdx) => {
      const color = DEVICE_COLORS[devIdx % DEVICE_COLORS.length];
      let card =
        `<div class="card" style="border-left:3px solid ${color};margin-bottom:12px;">` +
        `<h4 style="margin:0 0 8px 0;">` +
        `<span class="device-number" style="background:${color}">${String(devIdx + 1)}</span> ` +
        `${esc(dev.owner_name || dev.device_label)} ` +
        `<span style="color:var(--text-muted);font-size:12px;">(owner)</span></h4>` +
        `<div class="form-group">` +
        `<textarea rows="2" placeholder="This person's narrative arc..." ` +
        `oninput="updateDeviceField('${dev.id}','owner_story_arc',this.value)">` +
        `${esc(dev.owner_story_arc || '')}</textarea></div>`;

      dev.contacts.forEach((c) => {
        card +=
          `<div style="margin-left:16px;margin-bottom:8px;">` +
          `<label style="font-size:13px;font-weight:500;">${esc(c.name || 'Unnamed')} ` +
          `<span style="color:var(--text-muted);font-size:11px;">(${esc(c.role || 'contact')})</span></label>` +
          `<textarea rows="2" placeholder="This contact's narrative arc..." ` +
          `style="margin-top:4px;" ` +
          `oninput="updateContactStoryArc('${dev.id}','${c.id}',this.value)">` +
          `${esc(c.story_arc || '')}</textarea></div>`;
      });

      card += '</div>';
      parts.push(card);
    });

    list.innerHTML = parts.join('');
  }

  /**
   * Update a single contact's story arc text and persist.
   *
   * @param deviceId - The parent device ID.
   * @param contactId - The contact ID to update.
   * @param value - The new story arc text.
   */
  public updateContactStoryArc(deviceId: string, contactId: string, value: string): void {
    const dev = scenario.devices.find((d) => d.id === deviceId);
    if (!dev) return;
    const c = dev.contacts.find((x) => x.id === contactId);
    if (c) c.story_arc = value;
    void syncScenario();
  }

  // -----------------------------------------------------------------------
  // Story arc modal
  // -----------------------------------------------------------------------

  /**
   * Open the AI story arc generation modal.
   *
   * Pre-fills the modal fields with the current theme label, date range,
   * a default event count of 6, and auto-detects the matching duration preset.
   */
  public aiGenerateStoryArc(): void {
    const themeLabel = THEME_LABELS[scenario.theme] || scenario.theme || 'Slice of Life';
    const labelEl = document.getElementById('modal-arc-theme-label');
    if (labelEl) labelEl.textContent = themeLabel + ' theme';

    setElementValue('modal-arc-start', scenario.generation_settings.date_start);
    setElementValue('modal-arc-end', scenario.generation_settings.date_end);
    setElementValue('modal-arc-events', '6');
    setElementValue('modal-arc-preset', 'custom');
    this.detectArcPreset();

    const modal = document.getElementById('ai-storyarc-modal');
    if (modal) modal.classList.remove('hidden');
  }

  /**
   * Close the AI story arc generation modal.
   */
  public closeStoryArcModal(): void {
    const modal = document.getElementById('ai-storyarc-modal');
    if (modal) modal.classList.add('hidden');
  }

  /**
   * Apply a duration preset to the story arc modal date fields.
   *
   * Calculates the end date by adding the given number of months to
   * the start date, and suggests an event count based on the duration.
   * No-ops when the preset is `"custom"`.
   *
   * @param months - A numeric string for the month count, or `"custom"`.
   */
  public applyArcPreset(months: string): void {
    if (months === 'custom') return;
    const m = parseInt(months, 10);
    const start = new Date(scenario.generation_settings.date_start || '2025-01-01');
    const end = new Date(start);
    end.setMonth(end.getMonth() + m);

    setElementValue('modal-arc-start', start.toISOString().slice(0, 10));
    setElementValue('modal-arc-end', end.toISOString().slice(0, 10));
    const suggestedEvents = Math.max(3, Math.min(20, Math.round(m * 1.5)));
    setElementValue('modal-arc-events', String(suggestedEvents));
  }

  /**
   * Auto-detect the closest duration preset from the current modal dates.
   *
   * Compares the month difference between start and end dates against
   * the available presets (1, 3, 6, 12, 24) and selects the closest
   * match within a one-month tolerance.
   */
  private detectArcPreset(): void {
    const start = getElementValue('modal-arc-start');
    const end = getElementValue('modal-arc-end');
    if (!start || !end) return;

    const diffMs = new Date(end).getTime() - new Date(start).getTime();
    const diffMonths = Math.round(diffMs / (1000 * 60 * 60 * 24 * 30.44));
    const match = [1, 3, 6, 12, 24].find((v) => Math.abs(v - diffMonths) <= 1);
    setElementValue('modal-arc-preset', match ? String(match) : 'custom');
  }

  /**
   * Confirm and execute the AI story arc generation request.
   *
   * Reads configuration from the modal, closes it, sends the request
   * to the backend, and applies the generated arc to the scenario.
   */
  public async confirmAiGenerateStoryArc(): Promise<void> {
    const dateStart = getElementValue('modal-arc-start');
    const dateEnd = getElementValue('modal-arc-end');
    const numEvents = parseInt(getElementValue('modal-arc-events'), 10) || 6;
    this.closeStoryArcModal();

    scenario.generation_settings.date_start = dateStart;
    scenario.generation_settings.date_end = dateEnd;

    const castSummary = scenario.devices
      .map((dev) => {
        const contacts = dev.contacts.map((c) => `${c.name || 'unnamed'} (${c.role || 'contact'})`).join(', ');
        return `${dev.owner_name || 'unnamed owner'}'s phone: contacts are ${contacts}`;
      })
      .join('\n');

    const existingEvents = scenario.timeline_events
      .filter((e) => e.description)
      .map((e) => `${e.date}: ${e.description}`)
      .join('\n');

    showToast(`Generating story arc (${dateStart} \u2192 ${dateEnd})...`);

    try {
      const res = await api<StoryArcResponse>('POST', '/api/ai/generate-story-arc', {
        theme: scenario.theme || 'slice-of-life',
        culture: scenario.culture || 'american',
        cast_summary: castSummary,
        existing_events: existingEvents,
        date_start: dateStart,
        date_end: dateEnd,
        num_events: numEvents,
      });

      const arc = coerceToString(res.story_arc);
      if (arc) {
        scenario.story_arc = arc;
        this.renderStoryArc();
        void syncScenario();
        showToast('Story arc generated');
      }
    } catch {
      // Error toast already shown by api()
    }
  }

  // -----------------------------------------------------------------------
  // Character arcs AI
  // -----------------------------------------------------------------------

  /**
   * Collect standalone and required character name sets from the scenario.
   *
   * Standalone names come from devices using `"standalone"` generation
   * mode. Required names include all owners and contacts across every
   * device regardless of mode.
   *
   * @returns Deduplicated name arrays for both categories.
   */
  private collectCharacterNames(): CharacterNameSets {
    const standalone: string[] = [];
    const required: string[] = [];

    for (const dev of scenario.devices) {
      if (dev.owner_name) required.push(dev.owner_name);

      if ((dev.generation_mode || 'story') === 'standalone') {
        if (dev.owner_name) standalone.push(dev.owner_name);
        for (const c of dev.contacts) {
          if (c.name) standalone.push(c.name);
        }
      }
    }

    for (const dev of scenario.devices) {
      for (const c of dev.contacts) {
        if (c.name) required.push(c.name);
      }
    }

    return {
      standalone: [...new Set(standalone)],
      required: [...new Set(required)],
    };
  }

  /**
   * Apply generated character arcs to all matching devices and contacts.
   *
   * Builds a case-insensitive lookup index and assigns arcs by matching
   * on character names. Triggers a re-render and sync after application.
   *
   * @param arcs - Record of character name to arc value from the API.
   */
  private applyCharacterArcs(arcs: Record<string, unknown>): void {
    const byLower: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(arcs)) {
      byLower[
        String(k || '')
          .trim()
          .toLowerCase()
      ] = v;
    }

    for (const dev of scenario.devices) {
      const ownerArc = lookupArc(arcs, byLower, dev.owner_name);
      if (ownerArc) {
        dev.owner_story_arc = coerceToString(ownerArc);
      }
      for (const c of dev.contacts) {
        const contactArc = lookupArc(arcs, byLower, c.name);
        if (contactArc) {
          c.story_arc = coerceToString(contactArc);
        }
      }
    }

    this.renderStoryArc();
    void syncScenario();
    showToast('Character arcs generated');
  }

  /**
   * Generate AI-driven character arcs for all scenario participants.
   *
   * Requires a global story arc to be set first. Sends the cast summary,
   * character names, and the global arc to the backend, then applies the
   * returned per-character arcs to the scenario.
   */
  public async aiGenerateCharacterArcs(): Promise<void> {
    if (!scenario.story_arc) {
      showToast('Generate or write the global story arc first');
      return;
    }

    const castSummary = scenario.devices
      .map((dev) => {
        const contacts = dev.contacts.map((c) => `${c.name || 'unnamed'} (${c.role || 'contact'})`).join(', ');
        return `${dev.owner_name || 'unnamed'}: contacts are ${contacts}`;
      })
      .join('\n');

    showToast('Generating character arcs...');

    try {
      const { standalone, required } = this.collectCharacterNames();

      const res = await api<CharacterArcsResponse>('POST', '/api/ai/generate-character-arcs', {
        theme: scenario.theme || 'slice-of-life',
        culture: scenario.culture || 'american',
        story_arc: scenario.story_arc,
        cast_summary: castSummary,
        character_names: required,
        standalone_character_names: standalone,
      });

      if (res.arcs) {
        this.applyCharacterArcs(res.arcs);
      }
    } catch {
      // Error toast already shown by api()
    }
  }
}

// ---------------------------------------------------------------------------
// Default singleton & convenience exports
// ---------------------------------------------------------------------------

/** Default singleton used by all convenience exports. */
const mgr = new StoryArcManager();

/**
 * Render the global story arc textarea and all character arc fields.
 * Delegates to the default {@link StoryArcManager} instance.
 */
export function renderStoryArc(): void {
  mgr.renderStoryArc();
}

/**
 * Update a single contact's story arc text and persist.
 *
 * @param deviceId - The parent device ID.
 * @param contactId - The contact ID to update.
 * @param value - The new story arc text.
 */
export function updateContactStoryArc(deviceId: string, contactId: string, value: string): void {
  mgr.updateContactStoryArc(deviceId, contactId, value);
}

/**
 * Open the AI story arc generation modal.
 */
export function aiGenerateStoryArc(): void {
  mgr.aiGenerateStoryArc();
}

/**
 * Close the AI story arc generation modal.
 */
export function closeStoryArcModal(): void {
  mgr.closeStoryArcModal();
}

/**
 * Apply a duration preset to the story arc modal fields.
 *
 * @param months - A numeric string for the month count, or `"custom"`.
 */
export function applyArcPreset(months: string): void {
  mgr.applyArcPreset(months);
}

/**
 * Confirm and execute the AI story arc generation request.
 */
export async function confirmAiGenerateStoryArc(): Promise<void> {
  return mgr.confirmAiGenerateStoryArc();
}

/**
 * Generate AI-driven character arcs for all scenario participants.
 */
export async function aiGenerateCharacterArcs(): Promise<void> {
  return mgr.aiGenerateCharacterArcs();
}
