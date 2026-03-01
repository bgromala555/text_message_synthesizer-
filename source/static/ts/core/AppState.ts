/**
 * Global scenario state management and utility functions.
 *
 * Holds the single in-memory {@link Scenario} object shared by every
 * module, along with lookup tables for themes/cultures/languages,
 * phone-number generation, and a debounced PUT for persistence.
 */

import type { Culture, Language, MessageVolume, Scenario, Theme } from '../shared/types.js';
import { esc } from '../shared/html-utils.js';
import { api } from './ApiClient.js';

export { esc };

// -----------------------------------------------------------------------
// Core scenario state
// -----------------------------------------------------------------------

/**
 * The global in-memory scenario object.
 *
 * Every module that imports `scenario` receives the *same* object
 * reference. {@link setScenario} mutates it in-place so that all
 * existing references stay valid without re-importing.
 */
export let scenario: Scenario = {
  id: '',
  name: 'Untitled Scenario',
  theme: 'slice-of-life',
  culture: 'american',
  story_arc: '',
  devices: [],
  connections: [],
  timeline_events: [],
  group_chats: [],
  generation_settings: {
    date_start: '2025-01-01',
    date_end: '2025-12-31',
    messages_per_day_min: 2,
    messages_per_day_max: 8,
    batch_size: 25,
    llm_provider: 'openai',
    llm_model: '',
    temperature: 0.9,
    language: 'en',
  },
};

/**
 * Replace the scenario data in-place so every existing reference
 * continues to point at the live object.
 *
 * All own-properties are deleted first, then the new payload is
 * merged via {@link Object.assign}.
 *
 * @param data - The complete scenario payload to adopt.
 */
export function setScenario(data: Scenario): void {
  // Double-assert through unknown so we can delete dynamic keys.
  const record = scenario as unknown as Record<string, unknown>;
  for (const key of Object.keys(record)) {
    // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
    delete record[key];
  }
  Object.assign(scenario, data);
}

// -----------------------------------------------------------------------
// Constants
// -----------------------------------------------------------------------

/** Palette colours assigned to devices in creation order. */
export const DEVICE_COLORS = [
  '#3b82f6',
  '#22c55e',
  '#f59e0b',
  '#ef4444',
  '#a855f7',
  '#ec4899',
  '#14b8a6',
  '#f97316',
  '#6366f1',
  '#84cc16',
] as const;

/** Human-readable labels for scenario themes, keyed by {@link Theme}. */
export const THEME_LABELS = {
  'slice-of-life': 'Slice of Life',
  crime: 'Crime / Investigation',
  espionage: 'Espionage / Spy',
  romance: 'Romance / Drama',
  'family-drama': 'Family Drama',
  college: 'College Life',
  corporate: 'Corporate / Business',
  thriller: 'Thriller / Suspense',
  comedy: 'Comedy / Sitcom',
} as const satisfies Record<Theme, string>;

/** Human-readable labels for scenario cultures, keyed by {@link Culture}. */
export const CULTURE_LABELS = {
  american: 'American (US)',
  'arab-gulf': 'Arab \u2014 Gulf States',
  'arab-levant': 'Arab \u2014 Levant',
  'arab-north-africa': 'Arab \u2014 North Africa',
  british: 'British (UK)',
  chinese: 'Chinese',
  french: 'French',
  indian: 'Indian',
  japanese: 'Japanese',
  korean: 'Korean',
  'latin-american': 'Latin American',
  nigerian: 'Nigerian',
  russian: 'Russian',
  'southeast-asian': 'Southeast Asian',
  turkish: 'Turkish',
  'west-african': 'West African',
} as const satisfies Record<Culture, string>;

/**
 * Maps common contact role keywords to a suggested {@link MessageVolume}.
 *
 * Used by {@link suggestVolumeFromRole} for automatic volume inference.
 */
export const ROLE_VOLUME_HINTS: Readonly<Record<string, MessageVolume>> = {
  partner: 'heavy',
  spouse: 'heavy',
  boyfriend: 'heavy',
  girlfriend: 'heavy',
  'best friend': 'heavy',
  bff: 'heavy',
  'significant other': 'heavy',
  friend: 'regular',
  'close friend': 'heavy',
  coworker: 'regular',
  colleague: 'regular',
  roommate: 'regular',
  sibling: 'regular',
  brother: 'regular',
  sister: 'regular',
  cousin: 'regular',
  classmate: 'regular',
  neighbor: 'light',
  acquaintance: 'light',
  'gym buddy': 'light',
  'study partner': 'regular',
  boss: 'light',
  manager: 'light',
  mentor: 'light',
  therapist: 'minimal',
  barber: 'minimal',
  doctor: 'minimal',
  landlord: 'minimal',
  ex: 'minimal',
  dealer: 'minimal',
  handler: 'regular',
  informant: 'light',
  target: 'light',
  asset: 'light',
  contact: 'light',
};

/** Human-readable labels for generation languages, keyed by {@link Language}. */
export const LANGUAGE_LABELS = {
  en: 'English',
  es: 'Spanish',
  ar: 'Arabic',
  zh: 'Mandarin Chinese',
  fr: 'French',
} as const satisfies Record<Language, string>;

// -----------------------------------------------------------------------
// Utility helpers
// -----------------------------------------------------------------------

/**
 * Generate a short random identifier suitable for local client-side IDs.
 *
 * @returns An 8-character alphanumeric string.
 */
export function uid(): string {
  return Math.random().toString(36).substring(2, 10);
}

/** Set of phone-number / actor-ID strings already in use within the scenario. */
export const _usedPhoneNumbers: Set<string> = new Set<string>();

/**
 * Generate a unique US-formatted phone number that has not been used before.
 *
 * Area codes are drawn from the New York / New Jersey metro area.
 * The generated number is automatically registered in {@link _usedPhoneNumbers}.
 *
 * @returns A phone number string in E.164-ish format (e.g. `+12125551234`).
 */
export function generatePhoneNumber(): string {
  const areaCodes = [212, 718, 347, 646, 917, 929, 332, 551, 201, 973, 862, 908];
  let phone: string;
  do {
    const area = areaCodes[Math.floor(Math.random() * areaCodes.length)]!;
    const exchange = Math.floor(Math.random() * 900) + 100;
    const subscriber = Math.floor(Math.random() * 9000) + 1000;
    phone = `+1${String(area)}${String(exchange)}${String(subscriber)}`;
  } while (_usedPhoneNumbers.has(phone));
  _usedPhoneNumbers.add(phone);
  return phone;
}

/**
 * Suggest a {@link MessageVolume} based on a contact's role description.
 *
 * Performs an exact match first, then falls back to a substring search
 * across all known role keywords in {@link ROLE_VOLUME_HINTS}.
 *
 * @param role - The free-text role description (e.g. "best friend").
 * @returns The inferred volume, defaulting to `"regular"` when no match is found.
 */
export function suggestVolumeFromRole(role: string): MessageVolume {
  if (!role) return 'regular';
  const lower = role.toLowerCase().trim();

  const direct = ROLE_VOLUME_HINTS[lower];
  if (direct) return direct;

  for (const [key, vol] of Object.entries(ROLE_VOLUME_HINTS)) {
    if (lower.includes(key)) return vol;
  }
  return 'regular';
}

// -----------------------------------------------------------------------
// Scenario sync (debounced PUT)
// -----------------------------------------------------------------------

/** Handle returned by setTimeout for the debounced sync, or null if idle. */
let _syncTimer: number | null = null;

/** Optional callback invoked just before each sync to capture UI state. */
let _snapshotCallback: (() => void) | null = null;

/**
 * Register a callback that runs immediately before each persistence sync.
 *
 * Typically used to snapshot ephemeral UI state (e.g. collapsed panels)
 * into the scenario object before it is sent to the server.
 *
 * @param fn - Snapshot callback, or `null` to clear.
 */
export function registerSnapshotCallback(fn: (() => void) | null): void {
  _snapshotCallback = fn;
}

/**
 * Persist the current scenario to the server via a debounced PUT.
 *
 * Multiple rapid calls collapse into a single request fired 300 ms
 * after the last invocation.
 */
export async function syncScenario(): Promise<void> {
  if (_snapshotCallback) _snapshotCallback();
  if (_syncTimer) clearTimeout(_syncTimer);
  _syncTimer = window.setTimeout(() => {
    void api('PUT', '/api/scenario', scenario);
  }, 300);
}

// -----------------------------------------------------------------------
// Dropdown & field sync helpers
// -----------------------------------------------------------------------

/**
 * Register all actor IDs and phone numbers already present in the
 * scenario so that {@link generatePhoneNumber} avoids collisions.
 */
export function registerExistingPhoneNumbers(): void {
  for (const dev of scenario.devices || []) {
    if (dev.owner_actor_id) _usedPhoneNumbers.add(dev.owner_actor_id);
    for (const c of dev.contacts || []) {
      if (c.actor_id) _usedPhoneNumbers.add(c.actor_id);
    }
  }
}

/**
 * Synchronise the theme, culture, and language `<select>` elements
 * with the values currently stored in {@link scenario}.
 */
export function syncThemeDropdown(): void {
  const sel = document.getElementById('theme-select') as HTMLSelectElement | null;
  if (sel && scenario.theme) {
    sel.value = scenario.theme;
  }

  const culSel = document.getElementById('culture-select') as HTMLSelectElement | null;
  if (culSel) {
    culSel.value = scenario.culture || 'american';
  }

  const langSel = document.getElementById('scenario-language') as HTMLSelectElement | null;
  if (langSel) {
    langSel.value = scenario.generation_settings?.language || 'en';
  }
}

/**
 * Update the scenario theme and trigger a debounced sync.
 *
 * @param value - The new theme to set.
 */
export function updateTheme(value: Theme): void {
  scenario.theme = value;
  void syncScenario();
}

/**
 * Update the scenario culture and trigger a debounced sync.
 *
 * @param value - The new culture to set.
 */
export function updateCulture(value: Culture): void {
  scenario.culture = value || 'american';
  void syncScenario();
}

/**
 * Update the scenario generation language, sync the UI dropdown,
 * and trigger a debounced sync.
 *
 * @param value - The new language code to set.
 */
export function updateLanguage(value: Language): void {
  scenario.generation_settings.language = value || 'en';
  const scenLang = document.getElementById('scenario-language') as HTMLSelectElement | null;
  if (scenLang) {
    scenLang.value = value;
  }
  void syncScenario();
}
