/* devices.js -- Device CRUD UI (add/edit/delete device panels) */

import { scenario, DEVICE_COLORS, THEME_LABELS, uid, generatePhoneNumber, esc, syncScenario } from './state.js';
import { api } from './api.js';
import { showToast } from './toast.js';

export function addDevice() {
    const idx = scenario.devices.length;
    scenario.devices.push({
        id: uid(),
        device_label: 'Device ' + (idx + 1),
        owner_name: '',
        owner_actor_id: generatePhoneNumber(),
        owner_story_arc: '',
        generation_mode: 'story',
        role_style: 'normal',
        spam_density: 'medium',
        owner_personality: null,
        contacts: []
    });
    renderDevices();
    syncScenario();
}

export function removeDevice(deviceId) {
    scenario.devices = scenario.devices.filter(d => d.id !== deviceId);
    scenario.connections = scenario.connections.filter(
        c => c.source_device_id !== deviceId && c.target_device_id !== deviceId
    );
    renderDevices();
    syncScenario();
}

export function updateDeviceField(deviceId, field, value) {
    const dev = scenario.devices.find(d => d.id === deviceId);
    if (dev) {
        dev[field] = value;
        if (field === 'generation_mode' && value === 'standalone') {
            dev.role_style = 'normal';
        }
    }
    syncScenario();
}

export function setDeviceGenerationMode(deviceId, value) {
    updateDeviceField(deviceId, 'generation_mode', value);
    renderDevices();
}

export function setContactCount(deviceId, count) {
    const dev = scenario.devices.find(d => d.id === deviceId);
    if (!dev) return;
    count = Math.max(0, Math.min(20, parseInt(count) || 0));
    while (dev.contacts.length < count) {
        dev.contacts.push({
            id: uid(),
            actor_id: generatePhoneNumber(),
            name: '', role: '', message_volume: 'regular', story_arc: '',
            personality: null, shared_with: []
        });
    }
    while (dev.contacts.length > count) dev.contacts.pop();
    renderDevices();
    syncScenario();
}

export function renderDevices() {
    const list = document.getElementById('devices-list');
    const empty = document.getElementById('devices-empty');
    if (scenario.devices.length === 0) {
        list.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');
    list.innerHTML = scenario.devices.map((dev, idx) => {
        const sd = dev.spam_density || 'medium';
        const gm = dev.generation_mode || 'story';
        const rs = gm === 'standalone' ? 'normal' : (dev.role_style || 'normal');
        const roleStyleSection = gm === 'standalone'
            ? `
                <div class="form-group">
                    <label>Role Style</label>
                    <div style="font-size:12px;color:var(--text-secondary);padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg-elevated);">
                        Normal phone (auto-locked for Standalone mode)
                    </div>
                </div>
            `
            : `
                <div class="form-group">
                    <label>Role Style</label>
                    <select onchange="updateDeviceField('${dev.id}','role_style',this.value)">
                        <option value="normal"${rs === 'normal' ? ' selected' : ''}>Normal phone (family/friends/work)</option>
                        <option value="mixed"${rs === 'mixed' ? ' selected' : ''}>Mixed (mostly normal, few plot roles)</option>
                        <option value="story_heavy"${rs === 'story_heavy' ? ' selected' : ''}>Story-heavy (still realistic)</option>
                    </select>
                </div>
            `;
        return `
        <div class="card device-card" style="border-left-color:${DEVICE_COLORS[idx % DEVICE_COLORS.length]}">
            <div class="card-header">
                <h3>
                    <span class="device-number" style="background:${DEVICE_COLORS[idx % DEVICE_COLORS.length]}">${idx + 1}</span>
                    <input type="text" value="${esc(dev.device_label)}" placeholder="Device label"
                        style="background:transparent;border:none;color:var(--text-primary);font-size:15px;font-weight:600;width:200px;"
                        oninput="updateDeviceField('${dev.id}','device_label',this.value)">
                </h3>
                <button class="btn btn-danger btn-sm" onclick="removeDevice('${dev.id}')">Remove</button>
            </div>
            <div class="row">
                <div class="form-group">
                    <label>Owner Name</label>
                    <div style="display:flex;gap:6px;align-items:center;">
                        <input type="text" value="${esc(dev.owner_name)}" placeholder="e.g. Alex Rivera"
                            style="flex:1;"
                            oninput="updateDeviceField('${dev.id}','owner_name',this.value)">
                        <button class="btn btn-ai btn-sm" style="white-space:nowrap;"
                            onclick="aiGenerateOwnerName('${dev.id}')">AI Name</button>
                    </div>
                </div>
                <div class="form-group">
                    <label>Owner Phone #</label>
                    <input type="text" value="${esc(dev.owner_actor_id)}" placeholder="+12125551234"
                        oninput="updateDeviceField('${dev.id}','owner_actor_id',this.value)">
                </div>
                <div class="form-group">
                    <label>Number of Contacts</label>
                    <input type="number" value="${dev.contacts.length}" min="0" max="20"
                        onchange="setContactCount('${dev.id}', this.value)">
                </div>
                <div class="form-group">
                    <label>Generation Mode</label>
                    <select onchange="setDeviceGenerationMode('${dev.id}',this.value)">
                        <option value="story"${gm === 'story' ? ' selected' : ''}>Story-linked (events/connections)</option>
                        <option value="standalone"${gm === 'standalone' ? ' selected' : ''}>Standalone messages only</option>
                    </select>
                </div>
                ${roleStyleSection}
                <div class="form-group">
                    <label>Spam / Noise</label>
                    <select onchange="updateDeviceField('${dev.id}','spam_density',this.value)">
                        <option value="none"${sd === 'none' ? ' selected' : ''}>None</option>
                        <option value="low"${sd === 'low' ? ' selected' : ''}>Low (5-15)</option>
                        <option value="medium"${sd === 'medium' ? ' selected' : ''}>Medium (20-40)</option>
                        <option value="high"${sd === 'high' ? ' selected' : ''}>High (50-100)</option>
                    </select>
                </div>
            </div>
        </div>`;
    }).join('');
}

export async function aiGenerateOwnerName(deviceId) {
    const dev = scenario.devices.find(d => d.id === deviceId);
    if (!dev) return;
    if (dev.owner_name) {
        if (!confirm('Owner already has a name. Replace it?')) return;
    }
    showToast('Generating owner name...');
    const themeLabel = THEME_LABELS[scenario.theme] || scenario.theme;
    try {
        const res = await api('POST', '/api/ai/generate-names', {
            count: 1,
            context: `Device owner for SMS scenario \u2014 theme: ${themeLabel}`,
            owner_name: '',
            theme: scenario.theme || 'slice-of-life',
            culture: scenario.culture || 'american'
        });
        if (res.names && res.names[0]) {
            dev.owner_name = res.names[0];
            renderDevices();
            syncScenario();
            showToast('Owner name: ' + res.names[0]);
        }
    } catch (e) { /* toast shown by api() */ }
}

export async function aiGenerateAllOwnerNames() {
    for (const dev of scenario.devices) {
        if (!dev.owner_name) await aiGenerateOwnerName(dev.id);
    }
}
