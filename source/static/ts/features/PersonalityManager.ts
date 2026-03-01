/**
 * Personality card UI, AI personality generation, and data helpers.
 *
 * Renders expandable personality cards for every device owner and contact,
 * provides inline editing of personality fields, tag-list management, and
 * AI-driven personality generation via the backend API.
 * @module
 */

import type { Contact, Device, MessageVolume, Personality, TextingStyle } from '../shared/types.js';

import { DEVICE_COLORS, scenario, suggestVolumeFromRole, syncScenario, THEME_LABELS } from '../core/AppState.js';
import { api } from '../core/ApiClient.js';
import { showToast } from '../core/ToastService.js';
import { esc } from '../shared/html-utils.js';

// ---------------------------------------------------------------------------
// Supporting types
// ---------------------------------------------------------------------------

/** Owner-side personality reference on a {@link Device}. */
interface OwnerTarget {
  readonly obj: Device;
  readonly key: 'owner_personality';
}

/** Contact-side personality reference on a {@link Contact}. */
interface ContactTarget {
  readonly obj: Contact;
  readonly key: 'personality';
}

/**
 * Discriminated union linking a scenario entity to its personality field.
 * The `key` discriminant determines whether the holder is a device owner
 * or a contact.
 */
export type PersonalityTarget = OwnerTarget | ContactTarget;

/** Transient rendering item produced while iterating devices and contacts. */
interface PersonalityListItem {
  readonly type: 'owner' | 'contact';
  readonly device: Device;
  readonly devIdx: number;
  readonly contact: Contact | null;
  readonly name: string;
  readonly personality: Personality | null;
}

/** Contextual identifiers threaded through form-field renderers. */
interface FieldContext {
  readonly devId: string;
  readonly targetId: string;
}

// ---------------------------------------------------------------------------
// Module-level helpers
// ---------------------------------------------------------------------------

/** Valid values for AI-suggested message volume. */
const VALID_VOLUMES: readonly string[] = ['heavy', 'regular', 'light', 'minimal'];

/**
 * Read the personality from a target using discriminant narrowing.
 *
 * @param target - The personality target to read from.
 * @returns The personality object, or `null` if not yet set.
 */
function readPersonality(target: PersonalityTarget): Personality | null {
  return target.key === 'owner_personality' ? target.obj.owner_personality : target.obj.personality;
}

/**
 * Write a personality onto a target using discriminant narrowing.
 *
 * @param target - The personality target to write to.
 * @param p - The personality to assign, or `null` to clear.
 */
function writePersonality(target: PersonalityTarget, p: Personality | null): void {
  if (target.key === 'owner_personality') {
    target.obj.owner_personality = p;
  } else {
    target.obj.personality = p;
  }
}

/**
 * Create a blank {@link Personality} with sensible empty defaults.
 *
 * @returns A fully initialised personality ready for editing.
 */
function createBlankPersonality(): Personality {
  return {
    actor_id: '',
    name: '',
    age: 30,
    cultural_background: '',
    neighborhood: '',
    role: '',
    job_details: '',
    personality_summary: '',
    emotional_range: '',
    backstory_details: '',
    hobbies_and_interests: [],
    favorite_media: [],
    food_and_drink: '',
    favorite_local_spots: [],
    current_life_situations: [],
    topics_they_bring_up: [],
    topics_they_avoid: [],
    pet_peeves: [],
    humor_style: '',
    daily_routine_notes: '',
    texting_style: {
      punctuation: '',
      capitalization: '',
      emoji_use: '',
      abbreviations: '',
      avg_message_length: '',
      quirks: '',
    },
    how_owner_talks_to_them: '',
    relationship_arc: '',
    sample_phrases: [],
  };
}

/**
 * Type guard that checks whether a string is a valid {@link MessageVolume}.
 *
 * @param v - The candidate string to test.
 * @returns `true` when `v` is one of the four valid volume levels.
 */
function isValidVolume(v: string): v is MessageVolume {
  return VALID_VOLUMES.includes(v);
}

// ---------------------------------------------------------------------------
// PersonalityManager
// ---------------------------------------------------------------------------

/**
 * Manages personality cards for all device owners and contacts.
 *
 * Provides rendering, inline field editing, tag management, and
 * AI-powered personality generation. All public methods match the
 * original JS module exports for backward compatibility.
 */
export class PersonalityManager {
  // -----------------------------------------------------------------------
  // Private form-field renderers
  // -----------------------------------------------------------------------

  /**
   * Render a text input or textarea for a personality property.
   *
   * @param label - Human-readable field label.
   * @param key - Personality property name used in the `oninput` callback.
   * @param value - Current field value (stringified before escaping).
   * @param isTextarea - When `true`, renders a `<textarea>` instead of an `<input>`.
   * @param ctx - Device and target identifiers for the callback.
   * @returns An HTML string for the form group.
   */
  private renderField(
    label: string,
    key: string,
    value: string | number | undefined,
    isTextarea: boolean,
    ctx: FieldContext
  ): string {
    const safe = esc(String(value ?? ''));
    if (isTextarea) {
      return `<div class="form-group"><label>${label}</label>
        <textarea oninput="setPersonalityField('${ctx.devId}','${ctx.targetId}','${key}',this.value)">${safe}</textarea></div>`;
    }
    return `<div class="form-group"><label>${label}</label>
      <input type="text" value="${safe}" oninput="setPersonalityField('${ctx.devId}','${ctx.targetId}','${key}',this.value)"></div>`;
  }

  /**
   * Render a tag-input list for an array-valued personality property.
   *
   * @param label - Human-readable field label.
   * @param key - Personality property name for add/remove callbacks.
   * @param values - Current tag values.
   * @param ctx - Device and target identifiers for the callbacks.
   * @returns An HTML string for the tag-input form group.
   */
  private renderListField(label: string, key: string, values: string[] | undefined, ctx: FieldContext): string {
    const items = Array.isArray(values) ? values : [];
    const tagHtml = items
      .map(
        (v, i) =>
          `<span class="tag">${esc(v)}<span class="remove-tag" ` +
          `onclick="removeTag('${ctx.devId}','${ctx.targetId}','${key}',${String(i)})">&times;</span></span>`
      )
      .join('');
    return `<div class="form-group"><label>${label}</label>
      <div class="tag-input-container" id="tags-${ctx.devId}-${ctx.targetId}-${key}">
        ${tagHtml}
        <input type="text" placeholder="Type and press Enter"
          onkeydown="if(event.key==='Enter'){event.preventDefault();addTag('${ctx.devId}','${ctx.targetId}','${key}',this.value);this.value='';}">
      </div></div>`;
  }

  /**
   * Render a text input for a texting-style sub-property.
   *
   * @param label - Human-readable field label.
   * @param key - Texting-style property name for the `oninput` callback.
   * @param value - Current field value.
   * @param ctx - Device and target identifiers for the callback.
   * @returns An HTML string for the form group.
   */
  private renderTsField(label: string, key: string, value: string | undefined, ctx: FieldContext): string {
    return `<div class="form-group"><label>${label}</label>
      <input type="text" value="${esc(value ?? '')}"
        oninput="setTextingStyleField('${ctx.devId}','${ctx.targetId}','${key}',this.value)"></div>`;
  }

  // -----------------------------------------------------------------------
  // Section renderers (keep renderPersonalityForm under 75 lines)
  // -----------------------------------------------------------------------

  /**
   * Render the Basic Info section of a personality form.
   *
   * @param p - Partial personality data.
   * @param ctx - Field context with device and target identifiers.
   * @returns An HTML string for the section.
   */
  private renderBasicInfoSection(p: Partial<Personality>, ctx: FieldContext): string {
    return `<div class="personality-section"><h4>Basic Info</h4>
      <div class="row">${this.renderField('Age', 'age', p.age, false, ctx)}${this.renderField('Neighborhood', 'neighborhood', p.neighborhood, false, ctx)}${this.renderField('Role', 'role', p.role, false, ctx)}</div>
      ${this.renderField('Cultural Background', 'cultural_background', p.cultural_background, false, ctx)}
      ${this.renderField('Job Details', 'job_details', p.job_details, false, ctx)}
    </div>`;
  }

  /**
   * Render the Personality section of a personality form.
   *
   * @param p - Partial personality data.
   * @param ctx - Field context with device and target identifiers.
   * @returns An HTML string for the section.
   */
  private renderPersonalitySection(p: Partial<Personality>, ctx: FieldContext): string {
    return `<div class="personality-section"><h4>Personality</h4>
      ${this.renderField('Personality Summary', 'personality_summary', p.personality_summary, true, ctx)}
      ${this.renderField('Emotional Range', 'emotional_range', p.emotional_range, true, ctx)}
      ${this.renderField('Backstory', 'backstory_details', p.backstory_details, true, ctx)}
      ${this.renderField('Humor Style', 'humor_style', p.humor_style, false, ctx)}
      ${this.renderField('Daily Routine', 'daily_routine_notes', p.daily_routine_notes, true, ctx)}
    </div>`;
  }

  /**
   * Render the Interests section of a personality form.
   *
   * @param p - Partial personality data.
   * @param ctx - Field context with device and target identifiers.
   * @returns An HTML string for the section.
   */
  private renderInterestsSection(p: Partial<Personality>, ctx: FieldContext): string {
    return `<div class="personality-section"><h4>Interests</h4>
      ${this.renderListField('Hobbies & Interests', 'hobbies_and_interests', p.hobbies_and_interests, ctx)}
      ${this.renderListField('Favorite Media', 'favorite_media', p.favorite_media, ctx)}
      ${this.renderField('Food & Drink', 'food_and_drink', p.food_and_drink, false, ctx)}
      ${this.renderListField('Favorite Local Spots', 'favorite_local_spots', p.favorite_local_spots, ctx)}
      ${this.renderListField('Current Life Situations', 'current_life_situations', p.current_life_situations, ctx)}
    </div>`;
  }

  /**
   * Render the Behavior section of a personality form.
   *
   * @param p - Partial personality data.
   * @param ctx - Field context with device and target identifiers.
   * @returns An HTML string for the section.
   */
  private renderBehaviorSection(p: Partial<Personality>, ctx: FieldContext): string {
    return `<div class="personality-section"><h4>Behavior</h4>
      ${this.renderListField('Topics They Bring Up', 'topics_they_bring_up', p.topics_they_bring_up, ctx)}
      ${this.renderListField('Topics They Avoid', 'topics_they_avoid', p.topics_they_avoid, ctx)}
      ${this.renderListField('Pet Peeves', 'pet_peeves', p.pet_peeves, ctx)}
    </div>`;
  }

  /**
   * Render the Texting Style section of a personality form.
   *
   * @param ts - Partial texting style data.
   * @param ctx - Field context with device and target identifiers.
   * @returns An HTML string for the section.
   */
  private renderTextingStyleSection(ts: Partial<TextingStyle>, ctx: FieldContext): string {
    return `<div class="personality-section"><h4>Texting Style</h4>
      <div class="row">
        ${this.renderTsField('Punctuation', 'punctuation', ts.punctuation, ctx)}
        ${this.renderTsField('Capitalization', 'capitalization', ts.capitalization, ctx)}
      </div><div class="row">
        ${this.renderTsField('Emoji Use', 'emoji_use', ts.emoji_use, ctx)}
        ${this.renderTsField('Abbreviations', 'abbreviations', ts.abbreviations, ctx)}
      </div><div class="row">
        ${this.renderTsField('Avg Message Length', 'avg_message_length', ts.avg_message_length, ctx)}
        ${this.renderTsField('Quirks', 'quirks', ts.quirks, ctx)}
      </div>
    </div>`;
  }

  /**
   * Render the Relationship section of a personality form.
   *
   * @param p - Partial personality data.
   * @param ctx - Field context with device and target identifiers.
   * @returns An HTML string for the section.
   */
  private renderRelationshipSection(p: Partial<Personality>, ctx: FieldContext): string {
    return `<div class="personality-section"><h4>Relationship</h4>
      ${this.renderField('How Owner Talks to Them', 'how_owner_talks_to_them', p.how_owner_talks_to_them, true, ctx)}
      ${this.renderField('Relationship Arc', 'relationship_arc', p.relationship_arc, true, ctx)}
      ${this.renderListField('Sample Phrases', 'sample_phrases', p.sample_phrases, ctx)}
    </div>`;
  }

  // -----------------------------------------------------------------------
  // Card renderers
  // -----------------------------------------------------------------------

  /**
   * Render the full expandable personality form for a single list item.
   *
   * Delegates to individual section renderers that each produce one
   * collapsible section of the personality editing form.
   *
   * @param item - The personality list item describing the entity.
   * @returns An HTML string containing all personality form sections.
   */
  private renderPersonalityForm(item: PersonalityListItem): string {
    const p: Partial<Personality> = item.personality ?? {};
    const ts: Partial<TextingStyle> = p.texting_style ?? {};
    const ctx: FieldContext = {
      devId: item.device.id,
      targetId: item.type === 'owner' ? '__owner__' : (item.contact?.id ?? ''),
    };

    return [
      this.renderBasicInfoSection(p, ctx),
      this.renderPersonalitySection(p, ctx),
      this.renderInterestsSection(p, ctx),
      this.renderBehaviorSection(p, ctx),
      this.renderTextingStyleSection(ts, ctx),
      this.renderRelationshipSection(p, ctx),
    ].join('');
  }

  /**
   * Build the outer card HTML for a single personality list item.
   *
   * Includes the header with badges and the AI Generate button, plus
   * the expandable body containing the full personality form.
   *
   * @param item - The item to render a card for.
   * @returns An HTML string for the expandable personality card.
   */
  private renderCardHtml(item: PersonalityListItem): string {
    const hasP = item.personality !== null;
    const isOwner = item.type === 'owner';
    const badge = isOwner
      ? '<span class="badge badge-blue">Owner</span>'
      : '<span class="badge badge-purple">Contact</span>';
    const statusBadge = hasP
      ? '<span class="badge badge-green">Profile Set</span>'
      : '<span class="badge badge-orange">No Profile</span>';
    const entityId = isOwner ? item.device.id : (item.contact?.id ?? '');
    const expandId = 'personality-' + entityId;
    const isShared = !isOwner && item.contact?.shared_with !== undefined && item.contact.shared_with.length > 0;
    const sharedBadge = isShared ? '<span class="badge badge-orange">Shared</span>' : '';
    const color = DEVICE_COLORS[item.devIdx % DEVICE_COLORS.length];
    const aiTarget = isOwner ? '__owner__' : (item.contact?.id ?? '');

    return `
    <div class="card personality-card" id="${expandId}-card">
      <div class="card-header toggle-expand" onclick="togglePersonality('${expandId}')">
        <h3>
          <span class="device-number" style="background:${color}">${String(item.devIdx + 1)}</span>
          ${esc(item.name || 'Unnamed')} ${badge} ${statusBadge} ${sharedBadge}
        </h3>
        <div onclick="event.stopPropagation()">
          <button class="btn btn-ai btn-sm" onclick="aiGeneratePersonality('${item.device.id}','${aiTarget}')">
            AI Generate
          </button>
        </div>
      </div>
      <div class="expandable" id="${expandId}">
        ${this.renderPersonalityForm(item)}
      </div>
    </div>`;
  }

  // -----------------------------------------------------------------------
  // Public render/toggle API
  // -----------------------------------------------------------------------

  /**
   * Render all personality cards into the personalities list container.
   *
   * Iterates every device owner and contact, builds expandable card
   * markup, and replaces the list element's inner HTML. Toggles the
   * empty-state placeholder based on whether any entities exist.
   */
  public renderPersonalities(): void {
    const list = document.getElementById('personalities-list');
    const empty = document.getElementById('personalities-empty');
    if (!list) return;

    const allItems: PersonalityListItem[] = [];
    scenario.devices.forEach((dev, devIdx) => {
      allItems.push({
        type: 'owner',
        device: dev,
        devIdx,
        contact: null,
        name: dev.owner_name,
        personality: dev.owner_personality,
      });
      dev.contacts.forEach((c) => {
        allItems.push({
          type: 'contact',
          device: dev,
          devIdx,
          contact: c,
          name: c.name,
          personality: c.personality,
        });
      });
    });

    if (allItems.length === 0) {
      list.innerHTML = '';
      empty?.classList.remove('hidden');
      return;
    }
    empty?.classList.add('hidden');

    list.innerHTML = allItems.map((item) => this.renderCardHtml(item)).join('');
  }

  /**
   * Toggle the expanded/collapsed state of a personality card.
   *
   * @param id - The expandable section DOM ID (without the `-card` suffix).
   */
  public togglePersonality(id: string): void {
    const card = document.getElementById(id + '-card');
    if (card) card.classList.toggle('expanded');
  }

  // -----------------------------------------------------------------------
  // Data helpers
  // -----------------------------------------------------------------------

  /**
   * Locate the personality holder for a given device/target pair.
   *
   * Returns a {@link PersonalityTarget} discriminated union that can be
   * passed to {@link readPersonality} and {@link writePersonality} for
   * type-safe access to the underlying personality field.
   *
   * @param devId - The device ID to search within.
   * @param targetId - `"__owner__"` for the device owner, or a contact ID.
   * @returns The personality target, or `null` if not found.
   */
  public getPersonalityTarget(devId: string, targetId: string): PersonalityTarget | null {
    const dev = scenario.devices.find((d) => d.id === devId);
    if (!dev) return null;
    if (targetId === '__owner__') {
      return { obj: dev, key: 'owner_personality' };
    }
    const contact = dev.contacts.find((c) => c.id === targetId);
    if (contact) return { obj: contact, key: 'personality' };
    return null;
  }

  /**
   * Locate or lazily create the personality for a device/target pair.
   *
   * If no personality exists yet, a blank one is created and assigned
   * before returning the target reference.
   *
   * @param devId - The device ID to search within.
   * @param targetId - `"__owner__"` for the device owner, or a contact ID.
   * @returns The personality target (guaranteed non-null personality),
   *   or `null` if the device or contact was not found.
   */
  public ensurePersonality(devId: string, targetId: string): PersonalityTarget | null {
    const target = this.getPersonalityTarget(devId, targetId);
    if (!target) return null;
    if (!readPersonality(target)) {
      writePersonality(target, createBlankPersonality());
    }
    return target;
  }

  /**
   * Set a single top-level personality field value and persist.
   *
   * Handles the `"age"` field specially by parsing as an integer with
   * a fallback of 30. Creates a blank personality if none exists yet.
   *
   * @param devId - Device ID.
   * @param targetId - `"__owner__"` or a contact ID.
   * @param key - The personality property name to set.
   * @param value - The raw string value from the input element.
   */
  public setPersonalityField(devId: string, targetId: string, key: string, value: string): void {
    const target = this.ensurePersonality(devId, targetId);
    if (!target) return;
    const p = readPersonality(target);
    if (!p) return;
    const record = p as unknown as Record<string, unknown>;
    record[key] = key === 'age' ? parseInt(value, 10) || 30 : value;
    void syncScenario();
  }

  /**
   * Set a single texting-style sub-field and persist.
   *
   * Creates a blank personality if none exists yet.
   *
   * @param devId - Device ID.
   * @param targetId - `"__owner__"` or a contact ID.
   * @param key - The texting-style property name to set.
   * @param value - The raw string value from the input element.
   */
  public setTextingStyleField(devId: string, targetId: string, key: string, value: string): void {
    const target = this.ensurePersonality(devId, targetId);
    if (!target) return;
    const p = readPersonality(target);
    if (!p) return;
    const style = p.texting_style as unknown as Record<string, string>;
    style[key] = value;
    void syncScenario();
  }

  /**
   * Append a tag value to an array-valued personality field and re-render.
   *
   * No-ops when the trimmed value is empty.
   *
   * @param devId - Device ID.
   * @param targetId - `"__owner__"` or a contact ID.
   * @param key - The personality array property name.
   * @param value - The raw tag string to append.
   */
  public addTag(devId: string, targetId: string, key: string, value: string): void {
    if (!value.trim()) return;
    const target = this.ensurePersonality(devId, targetId);
    if (!target) return;
    const p = readPersonality(target);
    if (!p) return;
    const record = p as unknown as Record<string, unknown>;
    if (!Array.isArray(record[key])) record[key] = [];
    (record[key] as string[]).push(value.trim());
    this.renderPersonalities();
    void syncScenario();
  }

  /**
   * Remove a tag by index from an array-valued personality field and re-render.
   *
   * @param devId - Device ID.
   * @param targetId - `"__owner__"` or a contact ID.
   * @param key - The personality array property name.
   * @param index - Zero-based index of the tag to remove.
   */
  public removeTag(devId: string, targetId: string, key: string, index: number): void {
    const target = this.getPersonalityTarget(devId, targetId);
    if (!target) return;
    const p = readPersonality(target);
    if (!p) return;
    const record = p as unknown as Record<string, unknown>;
    if (Array.isArray(record[key])) {
      (record[key] as string[]).splice(index, 1);
    }
    this.renderPersonalities();
    void syncScenario();
  }

  // -----------------------------------------------------------------------
  // AI generation
  // -----------------------------------------------------------------------

  /**
   * Build connection context for contacts shared across multiple devices.
   *
   * Produces a human-readable sentence listing the other devices this
   * contact appears on, instructing the AI to vary behaviour per owner.
   *
   * @param isOwner - Whether the target entity is a device owner.
   * @param contact - The contact object, or `null` for owners.
   * @param dev - The device the contact belongs to.
   * @returns Context string, or empty string when not applicable.
   */
  private buildConnectionContext(isOwner: boolean, contact: Contact | null, dev: Device): string {
    if (isOwner || !contact?.shared_with?.length) return '';
    const otherDevices = contact.shared_with
      .map((s) => {
        const od = scenario.devices.find((d) => d.id === s.device_id);
        return od ? od.device_label + ' (owner: ' + (od.owner_name || 'unnamed') + ')' : '';
      })
      .filter(Boolean);
    return (
      ` This person appears on multiple devices: ${dev.device_label}` +
      ` and ${otherDevices.join(', ')}.` +
      ' They should behave differently on each device depending on' +
      ' their relationship with each owner.'
    );
  }

  /**
   * Apply the AI-suggested message volume to a contact.
   *
   * Falls back to a role-based heuristic when the suggestion is missing
   * or not a recognised {@link MessageVolume} value.
   *
   * @param isOwner - Whether the target is a device owner (skipped).
   * @param contact - The contact to update, or `null` for owners.
   * @param res - The AI-generated personality response.
   */
  private applyAiVolume(isOwner: boolean, contact: Contact | null, res: Personality): void {
    if (isOwner || !contact) return;
    const aiVol = res.suggested_message_volume;
    if (aiVol && isValidVolume(aiVol)) {
      contact.message_volume = aiVol;
    } else {
      contact.message_volume = suggestVolumeFromRole(contact.role);
    }
  }

  /**
   * Request an AI-generated personality for a device owner or contact.
   *
   * Sends relevant context (name, role, theme, culture, story arcs)
   * to the backend and applies the result to the scenario. Also
   * updates the contact's message volume when applicable.
   *
   * @param devId - Device ID containing the target entity.
   * @param targetId - `"__owner__"` for the device owner, or a contact ID.
   */
  public async aiGeneratePersonality(devId: string, targetId: string): Promise<void> {
    const dev = scenario.devices.find((d) => d.id === devId);
    if (!dev) return;
    const isOwner = targetId === '__owner__';
    const contact = isOwner ? null : dev.contacts.find((c) => c.id === targetId);
    const name = isOwner ? dev.owner_name : (contact?.name ?? '');
    const role = isOwner ? 'phone owner' : (contact?.role ?? '');
    if (!name) {
      showToast('Set a name first');
      return;
    }

    const connectionContext = this.buildConnectionContext(isOwner, contact ?? null, dev);
    showToast('Generating personality for ' + name + '...');

    try {
      const themeLabel = THEME_LABELS[scenario.theme];
      const characterArc = isOwner ? dev.owner_story_arc || '' : (contact?.story_arc ?? '');
      const usesStory = (dev.generation_mode || 'story') !== 'standalone';

      const res = await api<Personality>('POST', '/api/ai/generate-personality', {
        name,
        role,
        owner_name: dev.owner_name,
        context: `SMS conversation dataset \u2014 theme: ${themeLabel}.` + connectionContext,
        theme: scenario.theme || 'slice-of-life',
        culture: scenario.culture || 'american',
        story_arc: usesStory ? scenario.story_arc || '' : '',
        character_arc: characterArc,
      });

      if (res.name) {
        const target = this.ensurePersonality(devId, targetId);
        if (target) {
          writePersonality(target, res);
          this.applyAiVolume(isOwner, contact ?? null, res);
          this.renderPersonalities();
          void syncScenario();
          showToast('Personality generated for ' + name);
        }
      }
    } catch {
      // Error toast already shown by api()
    }
  }

  /**
   * Generate AI personalities for every owner and contact that lacks one.
   *
   * Iterates all devices sequentially, generating owner personalities
   * first and then contact personalities for each device.
   */
  public async aiGenerateAllPersonalities(): Promise<void> {
    for (const dev of scenario.devices) {
      if (!dev.owner_personality && dev.owner_name) {
        await this.aiGeneratePersonality(dev.id, '__owner__');
      }
      for (const c of dev.contacts) {
        if (!c.personality && c.name) {
          await this.aiGeneratePersonality(dev.id, c.id);
        }
      }
    }
    showToast('All personalities generated');
  }
}

// ---------------------------------------------------------------------------
// Default singleton & convenience exports
// ---------------------------------------------------------------------------

/** Default singleton used by all convenience exports. */
const mgr = new PersonalityManager();

/**
 * Render all personality cards into the personalities list container.
 * Delegates to the default {@link PersonalityManager} instance.
 */
export function renderPersonalities(): void {
  mgr.renderPersonalities();
}

/**
 * Toggle the expanded state of a personality card.
 *
 * @param id - The expandable section DOM ID (without `-card` suffix).
 */
export function togglePersonality(id: string): void {
  mgr.togglePersonality(id);
}

/**
 * Set a single top-level personality field value and persist.
 *
 * @param devId - Device ID.
 * @param targetId - `"__owner__"` or a contact ID.
 * @param key - The personality property name to set.
 * @param value - The raw string value from the input element.
 */
export function setPersonalityField(devId: string, targetId: string, key: string, value: string): void {
  mgr.setPersonalityField(devId, targetId, key, value);
}

/**
 * Set a single texting-style sub-field and persist.
 *
 * @param devId - Device ID.
 * @param targetId - `"__owner__"` or a contact ID.
 * @param key - The texting-style property name.
 * @param value - The raw string value.
 */
export function setTextingStyleField(devId: string, targetId: string, key: string, value: string): void {
  mgr.setTextingStyleField(devId, targetId, key, value);
}

/**
 * Append a tag to an array personality field and re-render.
 *
 * @param devId - Device ID.
 * @param targetId - `"__owner__"` or a contact ID.
 * @param key - Array field name.
 * @param value - The tag text.
 */
export function addTag(devId: string, targetId: string, key: string, value: string): void {
  mgr.addTag(devId, targetId, key, value);
}

/**
 * Remove a tag by index and re-render.
 *
 * @param devId - Device ID.
 * @param targetId - `"__owner__"` or a contact ID.
 * @param key - Array field name.
 * @param index - Zero-based tag index.
 */
export function removeTag(devId: string, targetId: string, key: string, index: number): void {
  mgr.removeTag(devId, targetId, key, index);
}

/**
 * AI-generate a personality for a single entity.
 *
 * @param devId - Device ID.
 * @param targetId - `"__owner__"` or a contact ID.
 */
export async function aiGeneratePersonality(devId: string, targetId: string): Promise<void> {
  return mgr.aiGeneratePersonality(devId, targetId);
}

/**
 * AI-generate personalities for all entities that lack one.
 */
export async function aiGenerateAllPersonalities(): Promise<void> {
  return mgr.aiGenerateAllPersonalities();
}

/**
 * Locate the personality holder for a device/target pair.
 *
 * @param devId - Device ID.
 * @param targetId - `"__owner__"` or a contact ID.
 * @returns The personality target, or `null` if not found.
 */
export function getPersonalityTarget(devId: string, targetId: string): PersonalityTarget | null {
  return mgr.getPersonalityTarget(devId, targetId);
}

/**
 * Locate or create a personality for a device/target pair.
 *
 * @param devId - Device ID.
 * @param targetId - `"__owner__"` or a contact ID.
 * @returns The personality target, or `null` if not found.
 */
export function ensurePersonality(devId: string, targetId: string): PersonalityTarget | null {
  return mgr.ensurePersonality(devId, targetId);
}
