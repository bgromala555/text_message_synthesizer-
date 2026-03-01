/* contacts.js -- Contact CRUD UI (add/edit/delete contacts) */

import { scenario, DEVICE_COLORS, THEME_LABELS, uid, generatePhoneNumber, esc, syncScenario, suggestVolumeFromRole } from './state.js';
import { api } from './api.js';
import { showToast } from './toast.js';

// -----------------------------------------------------------------------
// Render
// -----------------------------------------------------------------------

export function renderContacts() {
    const list = document.getElementById('contacts-list');
    const empty = document.getElementById('contacts-empty');
    if (scenario.devices.length === 0) {
        list.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    let html = '';

    if (scenario.devices.length >= 2) {
        html += `<div class="card" style="border-left:3px solid var(--orange);margin-bottom:20px;">
            <div class="card-header">
                <h3>Shared / Mutual Contacts</h3>
                <button class="btn btn-primary btn-sm" onclick="addSharedContactManual()">+ Link Contacts</button>
            </div>
            <p style="color:var(--text-secondary);font-size:13px;margin-bottom:12px;">
                Link contacts that are the <strong>same person</strong> across different devices.
                You can set a number to auto-create mutual slots, or manually link existing contacts.
            </p>
            ${renderMutualPairControls()}
            ${renderExistingSharedContacts()}
        </div>`;
    }

    html += scenario.devices.map((dev, devIdx) => `
        <div class="card device-card" style="border-left-color:${DEVICE_COLORS[devIdx % DEVICE_COLORS.length]}">
            <div class="card-header">
                <h3>
                    <span class="device-number" style="background:${DEVICE_COLORS[devIdx % DEVICE_COLORS.length]}">${devIdx + 1}</span>
                    ${esc(dev.device_label)} \u2014 ${esc(dev.owner_name || 'Unnamed Owner')}
                </h3>
                <div>
                    <button class="btn btn-ai btn-sm" onclick="aiGenerateNamesForDevice('${dev.id}')">AI Names</button>
                    <button class="btn btn-sm" onclick="addContactToDevice('${dev.id}')">+ Contact</button>
                </div>
            </div>
            ${dev.contacts.length === 0 ? '<p style="color:var(--text-muted);font-size:13px;">No contacts.</p>' : ''}
            ${dev.contacts.map(c => {
                const sharedBadge = c.shared_with && c.shared_with.length > 0
                    ? '<span class="badge badge-orange" style="margin-left:6px;">Shared</span>' : '';
                const vol = c.message_volume || 'regular';
                return `
                <div class="contact-row">
                    <span style="color:var(--text-muted);font-size:10px;width:90px;flex:none;" title="${esc(c.actor_id)}">${esc(c.actor_id)}</span>
                    <input type="text" value="${esc(c.name)}" placeholder="Contact name"
                        oninput="updateContact('${dev.id}','${c.id}','name',this.value)">
                    <input type="text" class="role-input" value="${esc(c.role)}" placeholder="Role"
                        oninput="updateContact('${dev.id}','${c.id}','role',this.value)">
                    <select class="volume-select" title="Message volume"
                        onchange="updateContact('${dev.id}','${c.id}','message_volume',this.value)">
                        <option value="heavy" ${vol==='heavy'?'selected':''}>Heavy</option>
                        <option value="regular" ${vol==='regular'?'selected':''}>Regular</option>
                        <option value="light" ${vol==='light'?'selected':''}>Light</option>
                        <option value="minimal" ${vol==='minimal'?'selected':''}>Minimal</option>
                    </select>
                    ${sharedBadge}
                    <button class="btn btn-danger btn-icon btn-sm" onclick="removeContact('${dev.id}','${c.id}')">&times;</button>
                </div>`;
            }).join('')}
        </div>
    `).join('');

    list.innerHTML = html;
}

// -----------------------------------------------------------------------
// Mutual / Shared contacts
// -----------------------------------------------------------------------

function renderMutualPairControls() {
    const pairs = [];
    for (let i = 0; i < scenario.devices.length; i++) {
        for (let j = i + 1; j < scenario.devices.length; j++) {
            pairs.push([scenario.devices[i], scenario.devices[j]]);
        }
    }
    return pairs.map(([d1, d2]) => {
        const currentCount = countSharedBetween(d1.id, d2.id);
        return `<div class="contact-row" style="margin-bottom:8px;">
            <span style="flex:none;font-size:13px;color:var(--text-primary);">
                ${esc(d1.device_label)} \u2194 ${esc(d2.device_label)}
            </span>
            <span style="color:var(--text-secondary);font-size:12px;flex:none;">
                ${currentCount} shared
            </span>
            <input type="number" value="${currentCount}" min="0" max="10" style="width:60px;flex:none;"
                onchange="setMutualCount('${d1.id}','${d2.id}',parseInt(this.value)||0)">
            <span style="color:var(--text-muted);font-size:11px;">mutual contacts</span>
        </div>`;
    }).join('');
}

export function countSharedBetween(devId1, devId2) {
    const dev1 = scenario.devices.find(d => d.id === devId1);
    if (!dev1) return 0;
    let count = 0;
    for (const c of dev1.contacts) {
        if (c.shared_with && c.shared_with.some(s => s.device_id === devId2)) count++;
    }
    return count;
}

export function setMutualCount(devId1, devId2, target) {
    const dev1 = scenario.devices.find(d => d.id === devId1);
    const dev2 = scenario.devices.find(d => d.id === devId2);
    if (!dev1 || !dev2) return;

    const current = countSharedBetween(devId1, devId2);

    for (let i = current; i < target; i++) {
        const c1id = uid();
        const c2id = uid();
        const sharedPhone = generatePhoneNumber();

        dev1.contacts.push({
            id: c1id,
            actor_id: sharedPhone,
            name: '', role: '', message_volume: 'regular', story_arc: '',
            personality: null,
            shared_with: [{ device_id: devId2, contact_id: c2id }]
        });
        dev2.contacts.push({
            id: c2id,
            actor_id: sharedPhone,
            name: '', role: '', message_volume: 'regular', story_arc: '',
            personality: null,
            shared_with: [{ device_id: devId1, contact_id: c1id }]
        });
    }

    if (target < current) {
        let toRemove = current - target;
        for (let i = dev1.contacts.length - 1; i >= 0 && toRemove > 0; i--) {
            const c = dev1.contacts[i];
            const link = c.shared_with && c.shared_with.find(s => s.device_id === devId2);
            if (link) {
                dev2.contacts = dev2.contacts.filter(x => x.id !== link.contact_id);
                dev1.contacts.splice(i, 1);
                toRemove--;
            }
        }
    }

    renderContacts();
    syncScenario();
}

function renderExistingSharedContacts() {
    const shared = [];
    for (const dev of scenario.devices) {
        for (const c of dev.contacts) {
            if (c.shared_with && c.shared_with.length > 0) {
                for (const link of c.shared_with) {
                    const otherDev = scenario.devices.find(d => d.id === link.device_id);
                    const otherContact = otherDev?.contacts.find(x => x.id === link.contact_id);
                    const devIdx = scenario.devices.indexOf(dev);
                    const otherDevIdx = otherDev ? scenario.devices.indexOf(otherDev) : 999;
                    if (devIdx < otherDevIdx) {
                        shared.push({
                            dev1: dev, c1: c,
                            dev2: otherDev, c2: otherContact
                        });
                    }
                }
            }
        }
    }
    if (shared.length === 0) return '';
    return '<div style="margin-top:12px;">' + shared.map(s => `
        <div class="contact-row" style="background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.2);">
            <span style="font-size:12px;color:var(--orange);flex:none;">SHARED</span>
            <span style="font-size:13px;">${esc(s.c1?.name || 'unnamed')} on ${esc(s.dev1?.device_label || '?')}</span>
            <span style="color:var(--text-muted);">\u2194</span>
            <span style="font-size:13px;">${esc(s.c2?.name || 'unnamed')} on ${esc(s.dev2?.device_label || '?')}</span>
            <button class="btn btn-danger btn-icon btn-sm"
                onclick="unlinkShared('${s.dev1?.id}','${s.c1?.id}','${s.dev2?.id}','${s.c2?.id}')">&times;</button>
        </div>
    `).join('') + '</div>';
}

export function addSharedContactManual() {
    if (scenario.devices.length < 2) { showToast('Need 2+ devices'); return; }
    const d1 = scenario.devices[0];
    const d2 = scenario.devices[1];
    setMutualCount(d1.id, d2.id, countSharedBetween(d1.id, d2.id) + 1);
}

export function unlinkShared(devId1, cId1, devId2, cId2) {
    const d1 = scenario.devices.find(d => d.id === devId1);
    const d2 = scenario.devices.find(d => d.id === devId2);
    if (d1) {
        const c1 = d1.contacts.find(c => c.id === cId1);
        if (c1) c1.shared_with = (c1.shared_with || []).filter(s => s.contact_id !== cId2);
    }
    if (d2) {
        const c2 = d2.contacts.find(c => c.id === cId2);
        if (c2) c2.shared_with = (c2.shared_with || []).filter(s => s.contact_id !== cId1);
    }
    renderContacts();
    syncScenario();
}

// -----------------------------------------------------------------------
// CRUD
// -----------------------------------------------------------------------

export function addContactToDevice(deviceId) {
    const dev = scenario.devices.find(d => d.id === deviceId);
    if (!dev) return;
    dev.contacts.push({
        id: uid(), actor_id: generatePhoneNumber(),
        name: '', role: '', message_volume: 'regular', story_arc: '',
        personality: null, shared_with: []
    });
    renderContacts();
    syncScenario();
}

export function removeContact(deviceId, contactId) {
    const dev = scenario.devices.find(d => d.id === deviceId);
    if (!dev) return;
    const c = dev.contacts.find(x => x.id === contactId);
    if (c && c.shared_with) {
        for (const link of c.shared_with) {
            const otherDev = scenario.devices.find(d => d.id === link.device_id);
            if (otherDev) {
                const otherC = otherDev.contacts.find(x => x.id === link.contact_id);
                if (otherC) {
                    otherC.shared_with = (otherC.shared_with || []).filter(s => s.contact_id !== contactId);
                }
            }
        }
    }
    dev.contacts = dev.contacts.filter(x => x.id !== contactId);
    renderContacts();
    syncScenario();
}

export function updateContact(deviceId, contactId, field, value) {
    const dev = scenario.devices.find(d => d.id === deviceId);
    if (!dev) return;
    const contact = dev.contacts.find(c => c.id === contactId);
    if (contact) {
        contact[field] = value;
        if (field === 'name' && contact.shared_with) {
            for (const link of contact.shared_with) {
                const otherDev = scenario.devices.find(d => d.id === link.device_id);
                const otherC = otherDev?.contacts.find(x => x.id === link.contact_id);
                if (otherC) otherC.name = value;
            }
        }
    }
    syncScenario();
}

// -----------------------------------------------------------------------
// AI name generation
// -----------------------------------------------------------------------

export async function aiGenerateNamesForDevice(deviceId) {
    const dev = scenario.devices.find(d => d.id === deviceId);
    if (!dev) return;
    const unnamed = dev.contacts.filter(c => !c.name);
    if (unnamed.length === 0) { showToast('All contacts already have names'); return; }
    showToast('Generating names + roles...');
    const themeLabel = THEME_LABELS[scenario.theme] || scenario.theme;
    try {
        const effectiveRoleStyle = (dev.generation_mode || 'story') === 'standalone' ? 'normal' : (dev.role_style || 'normal');
        const res = await api('POST', '/api/ai/generate-names', {
            count: unnamed.length,
            context: `SMS conversation scenario \u2014 theme: ${themeLabel}`,
            owner_name: dev.owner_name || '',
            theme: scenario.theme || 'slice-of-life',
            culture: scenario.culture || 'american',
            generation_mode: dev.generation_mode || 'story',
            role_style: effectiveRoleStyle
        });
        if (res.names) {
            res.names.forEach((name, i) => {
                if (i < unnamed.length) {
                    unnamed[i].name = name;
                    if (res.roles && res.roles[i] && !unnamed[i].role) {
                        unnamed[i].role = res.roles[i];
                    }
                    if (unnamed[i].role && !unnamed[i]._volumeManuallySet) {
                        unnamed[i].message_volume = suggestVolumeFromRole(unnamed[i].role);
                    }
                    if (unnamed[i].shared_with) {
                        for (const link of unnamed[i].shared_with) {
                            const od = scenario.devices.find(d => d.id === link.device_id);
                            const oc = od?.contacts.find(x => x.id === link.contact_id);
                            if (oc) oc.name = name;
                        }
                    }
                }
            });
            renderContacts();
            syncScenario();
            showToast('Names + roles + volumes generated');
        }
    } catch (e) { /* toast already shown by api() */ }
}

export async function aiGenerateAllNames() {
    for (const dev of scenario.devices) await aiGenerateNamesForDevice(dev.id);
}
