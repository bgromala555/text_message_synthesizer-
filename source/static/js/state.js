/* state.js -- Scenario state management */

import { api } from './api.js';

// -----------------------------------------------------------------------
// Core scenario state
// -----------------------------------------------------------------------

export let scenario = {
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
        language: 'en'
    }
};

export function setScenario(data) {
    // Mutate in place so all references (including window.scenario) stay valid
    for (const k of Object.keys(scenario)) delete scenario[k];
    Object.assign(scenario, data);
}

// -----------------------------------------------------------------------
// Constants
// -----------------------------------------------------------------------

export const DEVICE_COLORS = [
    '#3b82f6', '#22c55e', '#f59e0b', '#ef4444',
    '#a855f7', '#ec4899', '#14b8a6', '#f97316',
    '#6366f1', '#84cc16'
];

export const THEME_LABELS = {
    'slice-of-life': 'Slice of Life',
    'crime': 'Crime / Investigation',
    'espionage': 'Espionage / Spy',
    'romance': 'Romance / Drama',
    'family-drama': 'Family Drama',
    'college': 'College Life',
    'corporate': 'Corporate / Business',
    'thriller': 'Thriller / Suspense',
    'comedy': 'Comedy / Sitcom',
};

export const CULTURE_LABELS = {
    'american': 'American (US)',
    'arab-gulf': 'Arab \u2014 Gulf States',
    'arab-levant': 'Arab \u2014 Levant',
    'arab-north-africa': 'Arab \u2014 North Africa',
    'british': 'British (UK)',
    'chinese': 'Chinese',
    'french': 'French',
    'indian': 'Indian',
    'japanese': 'Japanese',
    'korean': 'Korean',
    'latin-american': 'Latin American',
    'nigerian': 'Nigerian',
    'russian': 'Russian',
    'southeast-asian': 'Southeast Asian',
    'turkish': 'Turkish',
    'west-african': 'West African',
};

export const ROLE_VOLUME_HINTS = {
    'partner': 'heavy', 'spouse': 'heavy', 'boyfriend': 'heavy', 'girlfriend': 'heavy',
    'best friend': 'heavy', 'bff': 'heavy', 'significant other': 'heavy',
    'friend': 'regular', 'close friend': 'heavy', 'coworker': 'regular',
    'colleague': 'regular', 'roommate': 'regular', 'sibling': 'regular',
    'brother': 'regular', 'sister': 'regular', 'cousin': 'regular',
    'classmate': 'regular', 'neighbor': 'light',
    'acquaintance': 'light', 'gym buddy': 'light', 'study partner': 'regular',
    'boss': 'light', 'manager': 'light', 'mentor': 'light',
    'therapist': 'minimal', 'barber': 'minimal', 'doctor': 'minimal',
    'landlord': 'minimal', 'ex': 'minimal', 'dealer': 'minimal',
    'handler': 'regular', 'informant': 'light', 'target': 'light',
    'asset': 'light', 'contact': 'light',
};

export const LANGUAGE_LABELS = {
    en: 'English', es: 'Spanish', ar: 'Arabic', zh: 'Mandarin Chinese', fr: 'French'
};

// -----------------------------------------------------------------------
// Utility helpers
// -----------------------------------------------------------------------

export function uid() {
    return Math.random().toString(36).substring(2, 10);
}

export const _usedPhoneNumbers = new Set();

export function generatePhoneNumber() {
    const areaCodes = [212, 718, 347, 646, 917, 929, 332, 551, 201, 973, 862, 908];
    let phone;
    do {
        const area = areaCodes[Math.floor(Math.random() * areaCodes.length)];
        const exchange = Math.floor(Math.random() * 900) + 100;
        const subscriber = Math.floor(Math.random() * 9000) + 1000;
        phone = `+1${area}${exchange}${subscriber}`;
    } while (_usedPhoneNumbers.has(phone));
    _usedPhoneNumbers.add(phone);
    return phone;
}

export function esc(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function suggestVolumeFromRole(role) {
    if (!role) return 'regular';
    const lower = role.toLowerCase().trim();
    if (ROLE_VOLUME_HINTS[lower]) return ROLE_VOLUME_HINTS[lower];
    for (const [key, vol] of Object.entries(ROLE_VOLUME_HINTS)) {
        if (lower.includes(key)) return vol;
    }
    return 'regular';
}

// -----------------------------------------------------------------------
// Scenario sync (debounced PUT)
// -----------------------------------------------------------------------

let _syncTimer = null;
let _snapshotCallback = null;

export function registerSnapshotCallback(fn) {
    _snapshotCallback = fn;
}

export async function syncScenario() {
    if (_snapshotCallback) _snapshotCallback();
    if (_syncTimer) clearTimeout(_syncTimer);
    _syncTimer = setTimeout(async () => {
        await api('PUT', '/api/scenario', scenario);
    }, 300);
}

// -----------------------------------------------------------------------
// Dropdown & field sync helpers
// -----------------------------------------------------------------------

export function registerExistingPhoneNumbers() {
    for (const dev of scenario.devices || []) {
        if (dev.owner_actor_id) _usedPhoneNumbers.add(dev.owner_actor_id);
        for (const c of dev.contacts || []) {
            if (c.actor_id) _usedPhoneNumbers.add(c.actor_id);
        }
    }
}

export function syncThemeDropdown() {
    const sel = document.getElementById('theme-select');
    if (sel && scenario.theme) sel.value = scenario.theme;
    const culSel = document.getElementById('culture-select');
    if (culSel) culSel.value = scenario.culture || 'american';
    const langSel = document.getElementById('scenario-language');
    if (langSel) langSel.value = scenario.generation_settings?.language || 'en';
}

export function updateTheme(value) {
    scenario.theme = value;
    syncScenario();
}

export function updateCulture(value) {
    scenario.culture = value || 'american';
    syncScenario();
}

export function updateLanguage(value) {
    scenario.generation_settings.language = value || 'en';
    const scenLang = document.getElementById('scenario-language');
    if (scenLang) scenLang.value = value;
    syncScenario();
}
