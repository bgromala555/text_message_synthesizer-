/* storyarc.js -- Story arc UI, AI story arc generation */

import { scenario, DEVICE_COLORS, THEME_LABELS, esc, syncScenario } from './state.js';
import { api } from './api.js';
import { showToast } from './toast.js';

// -----------------------------------------------------------------------
// Render
// -----------------------------------------------------------------------

export function renderStoryArc() {
    const globalBox = document.getElementById('story-arc-global');
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

    let html = '';
    scenario.devices.forEach((dev, devIdx) => {
        const color = DEVICE_COLORS[devIdx % DEVICE_COLORS.length];
        html += `<div class="card" style="border-left:3px solid ${color};margin-bottom:12px;">
            <h4 style="margin:0 0 8px 0;">
                <span class="device-number" style="background:${color}">${devIdx+1}</span>
                ${esc(dev.owner_name || dev.device_label)} <span style="color:var(--text-muted);font-size:12px;">(owner)</span>
            </h4>
            <div class="form-group">
                <textarea rows="2" placeholder="This person's narrative arc..."
                    oninput="updateDeviceField('${dev.id}','owner_story_arc',this.value)">${esc(dev.owner_story_arc || '')}</textarea>
            </div>`;

        dev.contacts.forEach(c => {
            html += `<div style="margin-left:16px;margin-bottom:8px;">
                <label style="font-size:13px;font-weight:500;">${esc(c.name || 'Unnamed')}
                    <span style="color:var(--text-muted);font-size:11px;">(${esc(c.role || 'contact')})</span>
                </label>
                <textarea rows="2" placeholder="This contact's narrative arc..."
                    style="margin-top:4px;"
                    oninput="updateContactStoryArc('${dev.id}','${c.id}',this.value)">${esc(c.story_arc || '')}</textarea>
            </div>`;
        });

        html += '</div>';
    });

    list.innerHTML = html;
}

export function updateContactStoryArc(deviceId, contactId, value) {
    const dev = scenario.devices.find(d => d.id === deviceId);
    if (!dev) return;
    const c = dev.contacts.find(x => x.id === contactId);
    if (c) c.story_arc = value;
    syncScenario();
}

// -----------------------------------------------------------------------
// Story arc modal
// -----------------------------------------------------------------------

export function aiGenerateStoryArc() {
    const themeLabel = THEME_LABELS[scenario.theme] || scenario.theme || 'Slice of Life';
    document.getElementById('modal-arc-theme-label').textContent = themeLabel + ' theme';
    document.getElementById('modal-arc-start').value = scenario.generation_settings.date_start;
    document.getElementById('modal-arc-end').value = scenario.generation_settings.date_end;
    document.getElementById('modal-arc-events').value = '6';
    document.getElementById('modal-arc-preset').value = 'custom';
    _detectArcPreset();
    document.getElementById('ai-storyarc-modal').classList.remove('hidden');
}

export function closeStoryArcModal() {
    document.getElementById('ai-storyarc-modal').classList.add('hidden');
}

export function applyArcPreset(months) {
    if (months === 'custom') return;
    const m = parseInt(months);
    const start = new Date(scenario.generation_settings.date_start || '2025-01-01');
    const end = new Date(start);
    end.setMonth(end.getMonth() + m);
    document.getElementById('modal-arc-start').value = start.toISOString().slice(0, 10);
    document.getElementById('modal-arc-end').value = end.toISOString().slice(0, 10);
    const suggestedEvents = Math.max(3, Math.min(20, Math.round(m * 1.5)));
    document.getElementById('modal-arc-events').value = String(suggestedEvents);
}

function _detectArcPreset() {
    const start = document.getElementById('modal-arc-start').value;
    const end = document.getElementById('modal-arc-end').value;
    if (!start || !end) return;
    const diffMs = new Date(end) - new Date(start);
    const diffMonths = Math.round(diffMs / (1000 * 60 * 60 * 24 * 30.44));
    const presetEl = document.getElementById('modal-arc-preset');
    const match = [1, 3, 6, 12, 24].find(v => Math.abs(v - diffMonths) <= 1);
    presetEl.value = match ? String(match) : 'custom';
}

export async function confirmAiGenerateStoryArc() {
    const dateStart = document.getElementById('modal-arc-start').value;
    const dateEnd = document.getElementById('modal-arc-end').value;
    const numEvents = parseInt(document.getElementById('modal-arc-events').value) || 6;
    closeStoryArcModal();

    scenario.generation_settings.date_start = dateStart;
    scenario.generation_settings.date_end = dateEnd;

    const castSummary = scenario.devices.map(dev => {
        const contacts = dev.contacts.map(c => `${c.name || 'unnamed'} (${c.role || 'contact'})`).join(', ');
        return `${dev.owner_name || 'unnamed owner'}'s phone: contacts are ${contacts}`;
    }).join('\n');

    const existingEvents = scenario.timeline_events
        .filter(e => e.description)
        .map(e => `${e.date}: ${e.description}`)
        .join('\n');

    showToast(`Generating story arc (${dateStart} \u2192 ${dateEnd})...`);
    try {
        const res = await api('POST', '/api/ai/generate-story-arc', {
            theme: scenario.theme || 'slice-of-life',
            culture: scenario.culture || 'american',
            cast_summary: castSummary,
            existing_events: existingEvents,
            date_start: dateStart,
            date_end: dateEnd,
            num_events: numEvents
        });
        let arc = res.story_arc;
        if (arc && typeof arc === 'object') arc = JSON.stringify(arc, null, 2);
        if (arc) {
            scenario.story_arc = arc;
            renderStoryArc();
            syncScenario();
            showToast('Story arc generated');
        }
    } catch (e) { /* toast shown by api() */ }
}

// -----------------------------------------------------------------------
// Character arcs AI
// -----------------------------------------------------------------------

export async function aiGenerateCharacterArcs() {
    if (!scenario.story_arc) { showToast('Generate or write the global story arc first'); return; }

    const castSummary = scenario.devices.map(dev => {
        const contacts = dev.contacts.map(c =>
            `${c.name || 'unnamed'} (${c.role || 'contact'})`
        ).join(', ');
        return `${dev.owner_name || 'unnamed'}: contacts are ${contacts}`;
    }).join('\n');

    showToast('Generating character arcs...');
    try {
        const standaloneCharacterNames = [];
        const requiredCharacterNames = [];
        for (const dev of scenario.devices) {
            if (dev.owner_name) requiredCharacterNames.push(dev.owner_name);
            if ((dev.generation_mode || 'story') !== 'standalone') continue;
            if (dev.owner_name) standaloneCharacterNames.push(dev.owner_name);
            for (const c of dev.contacts || []) {
                if (c.name) standaloneCharacterNames.push(c.name);
            }
        }
        for (const dev of scenario.devices) {
            for (const c of dev.contacts || []) {
                if (c.name) requiredCharacterNames.push(c.name);
            }
        }

        const dedupedRequiredNames = [...new Set(requiredCharacterNames)];
        const res = await api('POST', '/api/ai/generate-character-arcs', {
            theme: scenario.theme || 'slice-of-life',
            culture: scenario.culture || 'american',
            story_arc: scenario.story_arc,
            cast_summary: castSummary,
            character_names: dedupedRequiredNames,
            standalone_character_names: [...new Set(standaloneCharacterNames)]
        });
        if (res.arcs) {
            const coerce = v => (typeof v === 'object' && v !== null) ? JSON.stringify(v, null, 2) : (v || '');
            const byLower = {};
            for (const [k, v] of Object.entries(res.arcs)) {
                byLower[String(k || '').trim().toLowerCase()] = v;
            }
            const lookupArc = (name) => {
                const key = String(name || '').trim();
                if (!key) return '';
                return res.arcs[key] || byLower[key.toLowerCase()] || '';
            };
            for (const dev of scenario.devices) {
                const ownerArc = lookupArc(dev.owner_name);
                if (ownerArc) {
                    dev.owner_story_arc = coerce(ownerArc);
                }
                for (const c of dev.contacts) {
                    const contactArc = lookupArc(c.name);
                    if (contactArc) {
                        c.story_arc = coerce(contactArc);
                    }
                }
            }
            renderStoryArc();
            syncScenario();
            showToast('Character arcs generated');
        }
    } catch (e) { /* toast shown by api() */ }
}
