/* personalities.js -- Personality cards UI, AI personality generation */

import { scenario, DEVICE_COLORS, THEME_LABELS, esc, syncScenario, suggestVolumeFromRole } from './state.js';
import { api } from './api.js';
import { showToast } from './toast.js';

// -----------------------------------------------------------------------
// Render
// -----------------------------------------------------------------------

export function renderPersonalities() {
    const list = document.getElementById('personalities-list');
    const empty = document.getElementById('personalities-empty');
    const allItems = [];
    scenario.devices.forEach((dev, devIdx) => {
        allItems.push({ type: 'owner', device: dev, devIdx, contact: null, name: dev.owner_name, personality: dev.owner_personality });
        dev.contacts.forEach(c => {
            allItems.push({ type: 'contact', device: dev, devIdx, contact: c, name: c.name, personality: c.personality });
        });
    });
    if (allItems.length === 0) { list.innerHTML = ''; empty.classList.remove('hidden'); return; }
    empty.classList.add('hidden');

    list.innerHTML = allItems.map(item => {
        const hasP = !!item.personality;
        const isOwner = item.type === 'owner';
        const badge = isOwner ? '<span class="badge badge-blue">Owner</span>' : '<span class="badge badge-purple">Contact</span>';
        const statusBadge = hasP ? '<span class="badge badge-green">Profile Set</span>' : '<span class="badge badge-orange">No Profile</span>';
        const entityId = isOwner ? item.device.id : item.contact.id;
        const expandId = 'personality-' + entityId;
        const isShared = !isOwner && item.contact.shared_with && item.contact.shared_with.length > 0;
        const sharedBadge = isShared ? '<span class="badge badge-orange">Shared</span>' : '';

        return `
        <div class="card personality-card" id="${expandId}-card">
            <div class="card-header toggle-expand" onclick="togglePersonality('${expandId}')">
                <h3>
                    <span class="device-number" style="background:${DEVICE_COLORS[item.devIdx % DEVICE_COLORS.length]}">${item.devIdx + 1}</span>
                    ${esc(item.name || 'Unnamed')} ${badge} ${statusBadge} ${sharedBadge}
                </h3>
                <div onclick="event.stopPropagation()">
                    <button class="btn btn-ai btn-sm" onclick="aiGeneratePersonality('${item.device.id}','${isOwner ? '__owner__' : item.contact.id}')">
                        AI Generate
                    </button>
                </div>
            </div>
            <div class="expandable" id="${expandId}">
                ${renderPersonalityForm(item)}
            </div>
        </div>`;
    }).join('');
}

export function togglePersonality(id) {
    const card = document.getElementById(id + '-card');
    if (card) card.classList.toggle('expanded');
}

function renderPersonalityForm(item) {
    const p = item.personality || {};
    const ts = p.texting_style || {};
    const isOwner = item.type === 'owner';
    const devId = item.device.id;
    const targetId = isOwner ? '__owner__' : item.contact.id;

    function field(label, key, value, isTextarea) {
        if (isTextarea) {
            return `<div class="form-group"><label>${label}</label>
                <textarea oninput="setPersonalityField('${devId}','${targetId}','${key}',this.value)">${esc(value || '')}</textarea></div>`;
        }
        return `<div class="form-group"><label>${label}</label>
            <input type="text" value="${esc(value || '')}" oninput="setPersonalityField('${devId}','${targetId}','${key}',this.value)"></div>`;
    }

    function listField(label, key, values) {
        const items = Array.isArray(values) ? values : [];
        return `<div class="form-group"><label>${label}</label>
            <div class="tag-input-container" id="tags-${devId}-${targetId}-${key}">
                ${items.map((v, i) => `<span class="tag">${esc(v)}<span class="remove-tag" onclick="removeTag('${devId}','${targetId}','${key}',${i})">&times;</span></span>`).join('')}
                <input type="text" placeholder="Type and press Enter"
                    onkeydown="if(event.key==='Enter'){event.preventDefault();addTag('${devId}','${targetId}','${key}',this.value);this.value='';}">
            </div></div>`;
    }

    return `
    <div class="personality-section"><h4>Basic Info</h4>
        <div class="row">${field('Age', 'age', p.age)}${field('Neighborhood', 'neighborhood', p.neighborhood)}${field('Role', 'role', p.role)}</div>
        ${field('Cultural Background', 'cultural_background', p.cultural_background)}
        ${field('Job Details', 'job_details', p.job_details)}
    </div>
    <div class="personality-section"><h4>Personality</h4>
        ${field('Personality Summary', 'personality_summary', p.personality_summary, true)}
        ${field('Emotional Range', 'emotional_range', p.emotional_range, true)}
        ${field('Backstory', 'backstory_details', p.backstory_details, true)}
        ${field('Humor Style', 'humor_style', p.humor_style)}
        ${field('Daily Routine', 'daily_routine_notes', p.daily_routine_notes, true)}
    </div>
    <div class="personality-section"><h4>Interests</h4>
        ${listField('Hobbies & Interests', 'hobbies_and_interests', p.hobbies_and_interests)}
        ${listField('Favorite Media', 'favorite_media', p.favorite_media)}
        ${field('Food & Drink', 'food_and_drink', p.food_and_drink)}
        ${listField('Favorite Local Spots', 'favorite_local_spots', p.favorite_local_spots)}
        ${listField('Current Life Situations', 'current_life_situations', p.current_life_situations)}
    </div>
    <div class="personality-section"><h4>Behavior</h4>
        ${listField('Topics They Bring Up', 'topics_they_bring_up', p.topics_they_bring_up)}
        ${listField('Topics They Avoid', 'topics_they_avoid', p.topics_they_avoid)}
        ${listField('Pet Peeves', 'pet_peeves', p.pet_peeves)}
    </div>
    <div class="personality-section"><h4>Texting Style</h4>
        <div class="row">
            ${tsField('Punctuation', 'punctuation', ts.punctuation, devId, targetId)}
            ${tsField('Capitalization', 'capitalization', ts.capitalization, devId, targetId)}
        </div><div class="row">
            ${tsField('Emoji Use', 'emoji_use', ts.emoji_use, devId, targetId)}
            ${tsField('Abbreviations', 'abbreviations', ts.abbreviations, devId, targetId)}
        </div><div class="row">
            ${tsField('Avg Message Length', 'avg_message_length', ts.avg_message_length, devId, targetId)}
            ${tsField('Quirks', 'quirks', ts.quirks, devId, targetId)}
        </div>
    </div>
    <div class="personality-section"><h4>Relationship</h4>
        ${field('How Owner Talks to Them', 'how_owner_talks_to_them', p.how_owner_talks_to_them, true)}
        ${field('Relationship Arc', 'relationship_arc', p.relationship_arc, true)}
        ${listField('Sample Phrases', 'sample_phrases', p.sample_phrases)}
    </div>`;
}

function tsField(label, key, value, devId, targetId) {
    return `<div class="form-group"><label>${label}</label>
        <input type="text" value="${esc(value || '')}"
            oninput="setTextingStyleField('${devId}','${targetId}','${key}',this.value)"></div>`;
}

// -----------------------------------------------------------------------
// Personality data helpers
// -----------------------------------------------------------------------

export function getPersonalityTarget(devId, targetId) {
    const dev = scenario.devices.find(d => d.id === devId);
    if (!dev) return null;
    if (targetId === '__owner__') return { obj: dev, key: 'owner_personality' };
    const contact = dev.contacts.find(c => c.id === targetId);
    if (contact) return { obj: contact, key: 'personality' };
    return null;
}

export function ensurePersonality(devId, targetId) {
    const target = getPersonalityTarget(devId, targetId);
    if (!target) return null;
    if (!target.obj[target.key]) {
        target.obj[target.key] = {
            actor_id: '', name: '', age: 30, cultural_background: '', neighborhood: '', role: '',
            job_details: '', personality_summary: '', emotional_range: '',
            backstory_details: '', hobbies_and_interests: [], favorite_media: [],
            food_and_drink: '', favorite_local_spots: [], current_life_situations: [],
            topics_they_bring_up: [], topics_they_avoid: [], pet_peeves: [],
            humor_style: '', daily_routine_notes: '',
            texting_style: { punctuation: '', capitalization: '', emoji_use: '', abbreviations: '', avg_message_length: '', quirks: '' },
            how_owner_talks_to_them: '', relationship_arc: '', sample_phrases: []
        };
    }
    return target;
}

export function setPersonalityField(devId, targetId, key, value) {
    const target = ensurePersonality(devId, targetId);
    if (!target) return;
    target.obj[target.key][key] = (key === 'age') ? (parseInt(value) || 30) : value;
    syncScenario();
}

export function setTextingStyleField(devId, targetId, key, value) {
    const target = ensurePersonality(devId, targetId);
    if (!target) return;
    target.obj[target.key].texting_style[key] = value;
    syncScenario();
}

export function addTag(devId, targetId, key, value) {
    if (!value.trim()) return;
    const target = ensurePersonality(devId, targetId);
    if (!target) return;
    const p = target.obj[target.key];
    if (!Array.isArray(p[key])) p[key] = [];
    p[key].push(value.trim());
    renderPersonalities();
    syncScenario();
}

export function removeTag(devId, targetId, key, index) {
    const target = getPersonalityTarget(devId, targetId);
    if (!target) return;
    const p = target.obj[target.key];
    if (Array.isArray(p[key])) p[key].splice(index, 1);
    renderPersonalities();
    syncScenario();
}

// -----------------------------------------------------------------------
// AI generation
// -----------------------------------------------------------------------

export async function aiGeneratePersonality(devId, targetId) {
    const dev = scenario.devices.find(d => d.id === devId);
    if (!dev) return;
    const isOwner = targetId === '__owner__';
    const contact = isOwner ? null : dev.contacts.find(c => c.id === targetId);
    const name = isOwner ? dev.owner_name : contact?.name || '';
    const role = isOwner ? 'phone owner' : contact?.role || '';
    if (!name) { showToast('Set a name first'); return; }

    let connectionContext = '';
    if (!isOwner && contact?.shared_with?.length) {
        const otherDevices = contact.shared_with.map(s => {
            const od = scenario.devices.find(d => d.id === s.device_id);
            return od ? od.device_label + ' (owner: ' + (od.owner_name || 'unnamed') + ')' : '';
        }).filter(Boolean);
        connectionContext = ` This person appears on multiple devices: ${dev.device_label} and ${otherDevices.join(', ')}. They should behave differently on each device depending on their relationship with each owner.`;
    }

    showToast('Generating personality for ' + name + '...');
    try {
        const themeLabel = THEME_LABELS[scenario.theme] || scenario.theme;
        const characterArc = isOwner ? (dev.owner_story_arc || '') : (contact?.story_arc || '');
        const deviceUsesStoryContext = (dev.generation_mode || 'story') !== 'standalone';
        const res = await api('POST', '/api/ai/generate-personality', {
            name, role, owner_name: dev.owner_name,
            context: `SMS conversation dataset \u2014 theme: ${themeLabel}.` + connectionContext,
            theme: scenario.theme || 'slice-of-life',
            culture: scenario.culture || 'american',
            story_arc: deviceUsesStoryContext ? (scenario.story_arc || '') : '',
            character_arc: characterArc
        });
        if (res && res.name) {
            const target = ensurePersonality(devId, targetId);
            if (target) {
                target.obj[target.key] = res;
                if (!isOwner && contact) {
                    const aiVol = res.suggested_message_volume;
                    const validVols = ['heavy', 'regular', 'light', 'minimal'];
                    if (aiVol && validVols.includes(aiVol)) {
                        contact.message_volume = aiVol;
                    } else {
                        contact.message_volume = suggestVolumeFromRole(contact.role);
                    }
                }
                renderPersonalities();
                syncScenario();
                showToast('Personality generated for ' + name);
            }
        }
    } catch (e) { /* toast shown by api() */ }
}

export async function aiGenerateAllPersonalities() {
    for (const dev of scenario.devices) {
        if (!dev.owner_personality && dev.owner_name) await aiGeneratePersonality(dev.id, '__owner__');
        for (const c of dev.contacts) {
            if (!c.personality && c.name) await aiGeneratePersonality(dev.id, c.id);
        }
    }
    showToast('All personalities generated');
}
