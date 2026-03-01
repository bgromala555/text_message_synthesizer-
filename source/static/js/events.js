/* events.js -- Timeline events & group chats UI */

import { scenario, DEVICE_COLORS, THEME_LABELS, uid, esc, syncScenario } from './state.js';
import { api } from './api.js';
import { showToast } from './toast.js';
import { renderLinkChart } from './linkchart.js';

// -----------------------------------------------------------------------
// Render Events
// -----------------------------------------------------------------------

export function renderEvents() {
    const list = document.getElementById('events-list');
    const empty = document.getElementById('events-empty');
    const bar = document.getElementById('timeline-bar');

    if (scenario.timeline_events.length === 0) {
        list.innerHTML = '';
        bar.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    const start = new Date(scenario.generation_settings.date_start);
    const end = new Date(scenario.generation_settings.date_end);
    const range = end - start || 1;

    bar.innerHTML = scenario.timeline_events.map((ev, idx) => {
        const evDate = new Date(ev.date);
        const pct = Math.max(0, Math.min(100, ((evDate - start) / range) * 100));
        return `<div class="timeline-event" style="left:${pct}%" onclick="scrollToEvent(${idx})" title="${ev.date}">
            <div class="timeline-event-label">${(ev.date || '').substring(5)}</div></div>`;
    }).join('');

    list.innerHTML = scenario.timeline_events.map((ev, idx) => `
        <div class="card event-card" id="event-${idx}">
            <div class="card-header">
                <h3>${esc(ev.date || 'No date')}${ev.time ? ' at ' + esc(ev.time) : ''}</h3>
                <button class="btn btn-danger btn-sm" onclick="removeEvent(${idx})">Remove</button>
            </div>

            <div style="margin-bottom:12px;">
                <label style="font-size:12px;font-weight:500;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.5px;">
                    Participants (select people involved in this event)
                </label>
                <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;">
                    ${renderParticipantPicker(ev, idx)}
                </div>
            </div>

            <div class="form-group"><label>Description</label>
                <textarea oninput="updateEventField(${idx},'description',this.value)">${esc(ev.description || '')}</textarea></div>
            <div class="row">
                <div class="form-group"><label>Date</label>
                    <input type="date" value="${ev.date || ''}" onchange="updateEventField(${idx},'date',this.value)"></div>
                <div class="form-group"><label>Time (optional)</label>
                    <input type="text" value="${esc(ev.time || '')}" placeholder="HH:MM"
                        onchange="updateEventField(${idx},'time',this.value||null)"></div>
                <div class="form-group"><label>Encounter Type</label>
                    <select onchange="updateEventField(${idx},'encounter_type',this.value)">
                        <option value="planned" ${(ev.encounter_type||'planned')==='planned'?'selected':''}>Planned Meeting</option>
                        <option value="chance_encounter" ${ev.encounter_type==='chance_encounter'?'selected':''}>Chance Encounter</option>
                        <option value="near_miss" ${ev.encounter_type==='near_miss'?'selected':''}>Near Miss</option>
                    </select></div>
            </div>

            <div style="margin-top:8px;">
                <label style="font-size:12px;font-weight:500;color:var(--text-secondary);text-transform:uppercase;">Per-Device Impact</label>
                ${scenario.devices.map(dev => `
                    <div class="form-group" style="margin-top:6px;">
                        <label>${esc(dev.device_label)}</label>
                        <textarea placeholder="How this event appears on ${esc(dev.device_label)}..."
                            oninput="updateEventDeviceImpact(${idx},'${dev.id}',this.value)">${esc((ev.device_impacts || {})[dev.id] || '')}</textarea>
                    </div>
                `).join('')}
            </div>

            <div style="margin-top:8px;">
                <button class="btn btn-ai btn-sm" onclick="aiSuggestEventDetails(${idx})">AI Fill Details</button>
            </div>
        </div>
    `).join('');
}

function renderParticipantPicker(ev, evIdx) {
    const participants = ev.participants || [];
    let html = '';
    scenario.devices.forEach((dev, devIdx) => {
        const color = DEVICE_COLORS[devIdx % DEVICE_COLORS.length];
        const ownerSelected = participants.some(p => p.device_id === dev.id && p.contact_id === '__owner__');
        html += `<button class="btn btn-sm ${ownerSelected ? 'btn-primary' : ''}"
            style="${ownerSelected ? '' : 'border-color:' + color + ';color:' + color}"
            onclick="toggleEventParticipant(${evIdx},'${dev.id}','__owner__')">
            ${esc(dev.owner_name || dev.device_label)}</button>`;
        dev.contacts.forEach(c => {
            const selected = participants.some(p => p.device_id === dev.id && p.contact_id === c.id);
            html += `<button class="btn btn-sm ${selected ? 'btn-primary' : ''}"
                style="${selected ? '' : 'border-color:' + color + ';color:' + color}"
                onclick="toggleEventParticipant(${evIdx},'${dev.id}','${c.id}')">
                ${esc(c.name || c.actor_id)}</button>`;
        });
    });
    return html;
}

// -----------------------------------------------------------------------
// Event CRUD
// -----------------------------------------------------------------------

export function toggleEventParticipant(evIdx, deviceId, contactId) {
    const ev = scenario.timeline_events[evIdx];
    if (!ev) return;
    if (!ev.participants) ev.participants = [];
    const existing = ev.participants.findIndex(p => p.device_id === deviceId && p.contact_id === contactId);
    if (existing >= 0) {
        ev.participants.splice(existing, 1);
    } else {
        ev.participants.push({ device_id: deviceId, contact_id: contactId });
    }
    renderEvents();
    renderLinkChart();
    syncScenario();
}

export function scrollToEvent(idx) {
    const el = document.getElementById('event-' + idx);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

export function addEvent() {
    scenario.timeline_events.push({
        id: uid(),
        date: scenario.generation_settings.date_start,
        time: null,
        description: '',
        encounter_type: 'planned',
        device_impacts: {},
        involved_contacts: {},
        participants: []
    });
    renderEvents();
    syncScenario();
}

export function removeEvent(idx) {
    scenario.timeline_events.splice(idx, 1);
    renderEvents();
    renderLinkChart();
    syncScenario();
}

export function updateEventField(idx, field, value) {
    const ev = scenario.timeline_events[idx];
    if (ev) ev[field] = value;
    if (field === 'date') renderEvents();
    renderLinkChart();
    syncScenario();
}

export function updateEventDeviceImpact(idx, deviceId, value) {
    const ev = scenario.timeline_events[idx];
    if (!ev) return;
    if (!ev.device_impacts) ev.device_impacts = {};
    ev.device_impacts[deviceId] = value;
    syncScenario();
}

// -----------------------------------------------------------------------
// AI Events
// -----------------------------------------------------------------------

export function aiSuggestEvents() {
    const themeLabel = THEME_LABELS[scenario.theme] || scenario.theme || 'Slice of Life';
    document.getElementById('modal-theme-label').textContent = themeLabel + ' theme';
    document.getElementById('modal-ev-start').value = scenario.generation_settings.date_start;
    document.getElementById('modal-ev-end').value = scenario.generation_settings.date_end;
    document.getElementById('modal-ev-count').value = '6';
    document.getElementById('ai-events-modal').classList.remove('hidden');
}

export function closeEventsModal() {
    document.getElementById('ai-events-modal').classList.add('hidden');
}

export async function confirmAiSuggestEvents() {
    const dateStart = document.getElementById('modal-ev-start').value;
    const dateEnd = document.getElementById('modal-ev-end').value;
    const count = parseInt(document.getElementById('modal-ev-count').value) || 6;
    closeEventsModal();

    const rosterDevices = scenario.devices.map(dev => ({
        device_id: dev.id,
        device_label: dev.device_label,
        owner_name: dev.owner_name || '',
        contacts: dev.contacts.map(c => ({
            contact_id: c.id,
            name: c.name || '',
            role: c.role || '',
            personality_summary: c.personality?.personality_summary || ''
        }))
    }));

    const existingDescriptions = scenario.timeline_events
        .map(e => e.description).filter(Boolean);

    showToast(`Generating ${count} events (${dateStart} \u2192 ${dateEnd})...`);
    try {
        const res = await api('POST', '/api/ai/suggest-full-events', {
            devices: rosterDevices,
            date_start: dateStart,
            date_end: dateEnd,
            count: count,
            existing_descriptions: existingDescriptions,
            theme: scenario.theme || 'slice-of-life',
            culture: scenario.culture || 'american',
            story_arc: scenario.story_arc || ''
        });
        if (res.events && Array.isArray(res.events)) {
            res.events.forEach(ev => {
                scenario.timeline_events.push({
                    id: uid(),
                    date: ev.date || '',
                    time: ev.time || null,
                    description: ev.description || '',
                    device_impacts: ev.device_impacts || {},
                    involved_contacts: {},
                    participants: ev.participants || []
                });
            });
            renderEvents();
            renderLinkChart();
            syncScenario();
            showToast('Added ' + res.events.length + ' fully-detailed events');
        }
    } catch (e) { /* toast shown by api() */ }
}

export async function aiSuggestEventDetails(evIdx) {
    const ev = scenario.timeline_events[evIdx];
    if (!ev) return;
    if (!ev.participants || ev.participants.length === 0) {
        showToast('Select participants first');
        return;
    }

    const rosterDevices = [];
    const involvedDeviceIds = [...new Set(ev.participants.map(p => p.device_id))];
    for (const devId of involvedDeviceIds) {
        const dev = scenario.devices.find(d => d.id === devId);
        if (!dev) continue;
        const relevantContacts = ev.participants
            .filter(p => p.device_id === devId && p.contact_id !== '__owner__')
            .map(p => {
                const c = dev.contacts.find(x => x.id === p.contact_id);
                return {
                    contact_id: c?.id || p.contact_id,
                    name: c?.name || '',
                    role: c?.role || '',
                    personality_summary: c?.personality?.personality_summary || ''
                };
            });
        const includesOwner = ev.participants.some(p => p.device_id === devId && p.contact_id === '__owner__');
        rosterDevices.push({
            device_id: dev.id,
            device_label: dev.device_label,
            owner_name: includesOwner ? (dev.owner_name || '') : '',
            contacts: relevantContacts
        });
    }

    showToast('AI filling event details...');
    try {
        const res = await api('POST', '/api/ai/suggest-full-events', {
            devices: rosterDevices,
            date_start: ev.date || scenario.generation_settings.date_start,
            date_end: ev.date || scenario.generation_settings.date_end,
            count: 1,
            existing_descriptions: [],
            theme: scenario.theme || 'slice-of-life',
            culture: scenario.culture || 'american',
            story_arc: scenario.story_arc || ''
        });
        if (res.events && res.events[0]) {
            const suggested = res.events[0];
            if (suggested.description) ev.description = suggested.description;
            if (!ev.date && suggested.date) ev.date = suggested.date;
            if (!ev.time && suggested.time) ev.time = suggested.time;
            if (suggested.device_impacts) {
                if (!ev.device_impacts) ev.device_impacts = {};
                for (const [devId, impact] of Object.entries(suggested.device_impacts)) {
                    ev.device_impacts[devId] = impact;
                }
            }
            renderEvents();
            renderLinkChart();
            syncScenario();
            showToast('Event details filled (description + device impacts)');
        }
    } catch (e) { /* toast shown by api() */ }
}

// -----------------------------------------------------------------------
// Group Chats
// -----------------------------------------------------------------------

export function renderGroupChats() {
    const list = document.getElementById('group-chats-list');
    const empty = document.getElementById('group-chats-empty');
    if (!list) return;

    const groups = scenario.group_chats || [];
    if (groups.length === 0) {
        list.innerHTML = '';
        if (empty) empty.classList.remove('hidden');
        return;
    }
    if (empty) empty.classList.add('hidden');

    list.innerHTML = groups.map((gc, idx) => {
        const memberNames = (gc.members || []).map(m => {
            for (const dev of scenario.devices) {
                if (dev.id === m.device_id) {
                    if (m.contact_id === '__owner__') return dev.owner_name || dev.device_label + ' Owner';
                    const c = dev.contacts.find(x => x.id === m.contact_id);
                    if (c) return c.name || 'Unnamed';
                }
            }
            return '?';
        });

        return `
        <div class="card" style="border-left:3px solid var(--purple);margin-bottom:10px;">
            <div class="card-header">
                <h4 style="margin:0;">
                    <input type="text" value="${esc(gc.name)}" placeholder="Group name (e.g. The Crew)"
                        style="background:transparent;border:none;color:var(--text-primary);font-weight:600;font-size:14px;width:200px;"
                        oninput="updateGroupChat(${idx},'name',this.value)">
                </h4>
                <button class="btn btn-danger btn-icon btn-sm" onclick="removeGroupChat(${idx})">&times;</button>
            </div>
            <div class="row">
                <div class="form-group" style="flex:1;">
                    <label>Vibe / Dynamic</label>
                    <input type="text" value="${esc(gc.vibe || '')}" placeholder="e.g. casual banter, work coordination"
                        oninput="updateGroupChat(${idx},'vibe',this.value)">
                </div>
                <div class="form-group">
                    <label>Volume</label>
                    <select onchange="updateGroupChat(${idx},'message_volume',this.value)">
                        <option value="heavy" ${gc.message_volume==='heavy'?'selected':''}>Heavy</option>
                        <option value="regular" ${gc.message_volume==='regular'?'selected':''}>Regular</option>
                        <option value="light" ${gc.message_volume==='light'?'selected':''}>Light</option>
                        <option value="minimal" ${gc.message_volume==='minimal'?'selected':''}>Minimal</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Start Date</label>
                    <input type="date" value="${gc.start_date || ''}" onchange="updateGroupChat(${idx},'start_date',this.value)">
                </div>
                <div class="form-group">
                    <label>End Date</label>
                    <input type="date" value="${gc.end_date || ''}" placeholder="ongoing" onchange="updateGroupChat(${idx},'end_date',this.value)">
                </div>
            </div>
            <div class="form-group">
                <label>Members (${memberNames.length}): ${esc(memberNames.join(', '))}</label>
                <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px;">
                    ${renderGroupMemberPicker(idx)}
                </div>
            </div>
        </div>`;
    }).join('');
}

function renderGroupMemberPicker(gcIdx) {
    const gc = scenario.group_chats[gcIdx];
    if (!gc) return '';
    const members = gc.members || [];
    let html = '';
    scenario.devices.forEach((dev, devIdx) => {
        const color = DEVICE_COLORS[devIdx % DEVICE_COLORS.length];
        const ownerIn = members.some(m => m.device_id === dev.id && m.contact_id === '__owner__');
        html += `<button class="btn btn-sm ${ownerIn ? 'btn-success' : ''}"
            style="font-size:11px;border-color:${color};" title="${esc(dev.device_label)} owner"
            onclick="toggleGroupMember(${gcIdx},'${dev.id}','__owner__')">
            ${esc(dev.owner_name || dev.device_label)} \ud83d\udcf1</button>`;
        dev.contacts.forEach(c => {
            const isIn = members.some(m => m.device_id === dev.id && m.contact_id === c.id);
            html += `<button class="btn btn-sm ${isIn ? 'btn-success' : ''}"
                style="font-size:11px;border-color:${color};"
                onclick="toggleGroupMember(${gcIdx},'${dev.id}','${c.id}')">
                ${esc(c.name || 'Unnamed')}</button>`;
        });
    });
    return html;
}

export function toggleGroupMember(gcIdx, deviceId, contactId) {
    const gc = scenario.group_chats[gcIdx];
    if (!gc) return;
    if (!gc.members) gc.members = [];
    const existing = gc.members.findIndex(m => m.device_id === deviceId && m.contact_id === contactId);
    if (existing >= 0) {
        gc.members.splice(existing, 1);
    } else {
        gc.members.push({ device_id: deviceId, contact_id: contactId });
    }
    renderGroupChats();
    renderLinkChart();
    syncScenario();
}

export function addGroupChat() {
    if (!scenario.group_chats) scenario.group_chats = [];
    scenario.group_chats.push({
        id: uid(),
        name: '',
        members: [],
        origin_event_id: '',
        start_date: scenario.generation_settings.date_start || '2025-01-01',
        end_date: '',
        message_volume: 'regular',
        vibe: ''
    });
    renderGroupChats();
    syncScenario();
}

export function removeGroupChat(idx) {
    scenario.group_chats.splice(idx, 1);
    renderGroupChats();
    renderLinkChart();
    syncScenario();
}

export function updateGroupChat(idx, field, value) {
    const gc = scenario.group_chats[idx];
    if (gc) gc[field] = value;
    syncScenario();
}

export async function aiSuggestGroupChats() {
    if (scenario.devices.length === 0) { showToast('Add devices and contacts first'); return; }

    const castSummary = scenario.devices.map(dev => {
        const contacts = dev.contacts.map(c => `${c.name || 'unnamed'} (${c.role || 'contact'})`).join(', ');
        return `${dev.owner_name || 'unnamed'}'s phone: contacts are ${contacts}`;
    }).join('\n');

    const eventsSummary = scenario.timeline_events
        .filter(e => e.description)
        .map(e => `${e.date}: ${e.description}`)
        .join('\n');
    const eventsStructured = scenario.timeline_events
        .filter(e => e.description)
        .map(e => {
            const participantNames = (e.participants || []).map(p => {
                const pd = scenario.devices.find(d => d.id === p.device_id);
                if (!pd) return '';
                if (p.contact_id === '__owner__') return pd.owner_name || pd.device_label;
                const pc = pd.contacts.find(c => c.id === p.contact_id);
                return pc ? (pc.name || pc.actor_id) : '';
            }).filter(Boolean);
            return {
                event_id: e.id || '',
                date: e.date || '',
                description: e.description || '',
                participant_names: participantNames
            };
        });

    showToast('AI suggesting group chats...');
    try {
        const res = await api('POST', '/api/ai/suggest-group-chats', {
            theme: scenario.theme || 'slice-of-life',
            culture: scenario.culture || 'american',
            story_arc: scenario.story_arc || '',
            cast_summary: castSummary,
            events_summary: eventsSummary,
            events: eventsStructured,
            devices: scenario.devices.map(d => ({
                device_id: d.id,
                device_label: d.device_label,
                owner_name: d.owner_name || '',
                contacts: d.contacts.map(c => ({
                    contact_id: c.id,
                    name: c.name || '',
                    role: c.role || ''
                }))
            }))
        });
        if (res.group_chats && Array.isArray(res.group_chats)) {
            if (!scenario.group_chats) scenario.group_chats = [];
            res.group_chats.forEach(gc => {
                scenario.group_chats.push({
                    id: uid(),
                    name: gc.name || '',
                    members: gc.members || [],
                    origin_event_id: gc.origin_event_id || '',
                    start_date: gc.start_date || scenario.generation_settings.date_start,
                    end_date: gc.end_date || '',
                    message_volume: gc.message_volume || 'regular',
                    vibe: gc.vibe || '',
                    activation_mode: gc.activation_mode || 'event_time',
                    auto_pair_threads: gc.auto_pair_threads !== false,
                    quality_score: gc.quality_score ?? 1.0
                });
            });
            if (res.quality && Array.isArray(res.quality.findings) && res.quality.findings.length) {
                showToast('Group/event quality warnings: ' + res.quality.findings.length);
            }
            renderGroupChats();
            renderLinkChart();
            syncScenario();
            showToast('Added ' + res.group_chats.length + ' group chats');
        }
    } catch (e) { /* toast shown by api() */ }
}
