/**
 * Device CRUD operations and rendering for the device configuration panel.
 *
 * Manages adding, removing, and editing devices within the global scenario.
 * Each device has an owner, contacts, generation settings, and spam density.
 * Rendering produces card-based HTML injected via innerHTML with all
 * user-supplied values escaped through {@link esc}.
 * @module
 */

import type { Device, RoleStyle } from '../shared/types.js';

import { api } from '../core/ApiClient.js';
import { DEVICE_COLORS, THEME_LABELS, generatePhoneNumber, scenario, syncScenario, uid } from '../core/AppState.js';
import { showToast } from '../core/ToastService.js';

import { esc } from '../shared/html-utils.js';

/**
 * String-valued fields on {@link Device} that can be set from HTML form inputs.
 * Used to provide type-safe dynamic field assignment in
 * {@link DeviceManager.updateDeviceField}.
 */
type MutableDeviceField =
  | 'device_label'
  | 'owner_name'
  | 'owner_actor_id'
  | 'owner_story_arc'
  | 'generation_mode'
  | 'role_style'
  | 'spam_density';

/** Response payload from the AI name-generation endpoint. */
interface AiGenerateNamesResponse {
  names?: string[];
  roles?: string[];
}

/**
 * Manages device lifecycle and rendering within the scenario builder.
 *
 * Provides CRUD operations for devices, inline field updates, contact-count
 * adjustment, card-based HTML rendering, and AI-powered owner name generation.
 */
export class DeviceManager {
  /**
   * Add a new device with default settings to the scenario.
   *
   * The device receives an auto-incremented label, a generated phone number,
   * and sensible defaults for all configuration fields. Triggers a re-render
   * and persistence sync.
   */
  public addDevice(): void {
    const idx = scenario.devices.length;
    scenario.devices.push({
      id: uid(),
      device_label: `Device ${String(idx + 1)}`,
      owner_name: '',
      owner_actor_id: generatePhoneNumber(),
      owner_story_arc: '',
      generation_mode: 'story',
      role_style: 'normal',
      spam_density: 'medium',
      owner_personality: null,
      contacts: [],
    });
    this.renderDevices();
    void syncScenario();
  }

  /**
   * Remove a device and all its connections from the scenario.
   *
   * Filters the device out of the devices array and removes any connections
   * that reference it as source or target. Triggers re-render and sync.
   *
   * @param deviceId - The unique ID of the device to remove.
   */
  public removeDevice(deviceId: string): void {
    scenario.devices = scenario.devices.filter((d) => d.id !== deviceId);
    scenario.connections = scenario.connections.filter(
      (c) => c.source_device_id !== deviceId && c.target_device_id !== deviceId
    );
    this.renderDevices();
    void syncScenario();
  }

  /**
   * Update a single string-valued field on a device.
   *
   * Performs dynamic property assignment for fields that are edited via inline
   * HTML form inputs. When the generation mode is set to "standalone", the
   * role style is automatically locked to "normal".
   *
   * @param deviceId - The unique ID of the device to update.
   * @param field - The device property to modify.
   * @param value - The new string value from the form input.
   */
  public updateDeviceField(deviceId: string, field: MutableDeviceField, value: string): void {
    const dev = scenario.devices.find((d) => d.id === deviceId);
    if (dev) {
      // All MutableDeviceField keys map to string-compatible types on Device.
      // Dynamic assignment via a widened record mirrors the original JS logic.
      const record = dev as unknown as Record<string, string>;
      record[field] = value;

      if (field === 'generation_mode' && value === 'standalone') {
        dev.role_style = 'normal';
      }
    }
    void syncScenario();
  }

  /**
   * Change a device's generation mode and re-render the device list.
   *
   * Delegates to {@link updateDeviceField} for the actual mutation, then
   * triggers a re-render so mode-dependent UI sections update.
   *
   * @param deviceId - The unique ID of the device to update.
   * @param value - The new generation mode value from the select input.
   */
  public setDeviceGenerationMode(deviceId: string, value: string): void {
    this.updateDeviceField(deviceId, 'generation_mode', value);
    this.renderDevices();
  }

  /**
   * Adjust the number of contacts on a device to the specified count.
   *
   * Adds new contacts with generated phone numbers when the target count
   * exceeds the current count, or pops contacts from the end when reducing.
   * The count is clamped to the 0–20 range.
   *
   * @param deviceId - The unique ID of the device to adjust.
   * @param count - The desired contact count (parsed from a string input).
   */
  public setContactCount(deviceId: string, count: string): void {
    const dev = scenario.devices.find((d) => d.id === deviceId);
    if (!dev) return;

    let target = Math.max(0, Math.min(20, parseInt(count, 10) || 0));

    while (dev.contacts.length < target) {
      dev.contacts.push({
        id: uid(),
        actor_id: generatePhoneNumber(),
        name: '',
        role: '',
        message_volume: 'regular',
        story_arc: '',
        personality: null,
        shared_with: [],
      });
    }
    while (dev.contacts.length > target) dev.contacts.pop();

    this.renderDevices();
    void syncScenario();
  }

  /**
   * Render the complete device list into the DOM.
   *
   * Clears the device list container and rebuilds it from the current
   * scenario state. Shows an empty-state message when no devices exist.
   * All user-supplied values are escaped via {@link esc} before injection.
   */
  public renderDevices(): void {
    const list = document.getElementById('devices-list');
    const empty = document.getElementById('devices-empty');
    if (!list || !empty) return;

    if (scenario.devices.length === 0) {
      list.innerHTML = '';
      empty.classList.remove('hidden');
      return;
    }
    empty.classList.add('hidden');
    list.innerHTML = scenario.devices.map((dev, idx) => this.renderDeviceCard(dev, idx)).join('');
  }

  /**
   * Build the HTML card markup for a single device.
   *
   * Produces a card with editable fields for label, owner name, phone number,
   * contact count, generation mode, role style, and spam density. The card
   * border colour is drawn from {@link DEVICE_COLORS} by device index.
   *
   * @param dev - The device data to render.
   * @param idx - The zero-based position of the device in the list.
   * @returns HTML string for the device card.
   */
  private renderDeviceCard(dev: Device, idx: number): string {
    const sd = dev.spam_density || 'medium';
    const gm = dev.generation_mode || 'story';
    const rs: RoleStyle = gm === 'standalone' ? 'normal' : dev.role_style || 'normal';
    const color = DEVICE_COLORS[idx % DEVICE_COLORS.length];

    const roleStyleSection =
      gm === 'standalone'
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
        <div class="card device-card" style="border-left-color:${color}">
            <div class="card-header">
                <h3>
                    <span class="device-number" style="background:${color}">${String(idx + 1)}</span>
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
                    <input type="number" value="${String(dev.contacts.length)}" min="0" max="20"
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
  }

  /**
   * AI-generate a name for the owner of a specific device.
   *
   * Calls the `/api/ai/generate-names` endpoint to produce a single name
   * based on the scenario theme and culture. Prompts for confirmation if the
   * device owner already has a name. Updates the device and re-renders on
   * success.
   *
   * @param deviceId - The unique ID of the device whose owner needs a name.
   */
  public async aiGenerateOwnerName(deviceId: string): Promise<void> {
    const dev = scenario.devices.find((d) => d.id === deviceId);
    if (!dev) return;

    if (dev.owner_name) {
      if (!confirm('Owner already has a name. Replace it?')) return;
    }

    showToast('Generating owner name...');
    const themeLabel = THEME_LABELS[scenario.theme] || scenario.theme;

    try {
      const res = await api<AiGenerateNamesResponse>('POST', '/api/ai/generate-names', {
        count: 1,
        context: `Device owner for SMS scenario \u2014 theme: ${themeLabel}`,
        owner_name: '',
        theme: scenario.theme || 'slice-of-life',
        culture: scenario.culture || 'american',
      });

      if (res.names && res.names[0]) {
        dev.owner_name = res.names[0];
        this.renderDevices();
        void syncScenario();
        showToast(`Owner name: ${res.names[0]}`);
      }
    } catch {
      // Toast already shown by api()
    }
  }

  /**
   * AI-generate owner names for all devices that lack one.
   *
   * Iterates through every device in the scenario and calls
   * {@link aiGenerateOwnerName} for each device whose owner name is empty.
   * Processes devices sequentially to avoid overwhelming the API.
   */
  public async aiGenerateAllOwnerNames(): Promise<void> {
    for (const dev of scenario.devices) {
      if (!dev.owner_name) await this.aiGenerateOwnerName(dev.id);
    }
  }
}

/** Default singleton used by the convenience exports. */
const defaultManager = new DeviceManager();

/**
 * Add a new device with default settings to the scenario.
 * Delegates to the default {@link DeviceManager} singleton.
 */
export function addDevice(): void {
  defaultManager.addDevice();
}

/**
 * Remove a device and all its connections from the scenario.
 * Delegates to the default {@link DeviceManager} singleton.
 *
 * @param deviceId - The unique ID of the device to remove.
 */
export function removeDevice(deviceId: string): void {
  defaultManager.removeDevice(deviceId);
}

/**
 * Update a single string-valued field on a device.
 * Delegates to the default {@link DeviceManager} singleton.
 *
 * @param deviceId - The unique ID of the device to update.
 * @param field - The device property to modify.
 * @param value - The new string value from the form input.
 */
export function updateDeviceField(deviceId: string, field: MutableDeviceField, value: string): void {
  defaultManager.updateDeviceField(deviceId, field, value);
}

/**
 * Change a device's generation mode and re-render.
 * Delegates to the default {@link DeviceManager} singleton.
 *
 * @param deviceId - The unique ID of the device to update.
 * @param value - The new generation mode value.
 */
export function setDeviceGenerationMode(deviceId: string, value: string): void {
  defaultManager.setDeviceGenerationMode(deviceId, value);
}

/**
 * Adjust the number of contacts on a device.
 * Delegates to the default {@link DeviceManager} singleton.
 *
 * @param deviceId - The unique ID of the device to adjust.
 * @param count - The desired contact count as a string.
 */
export function setContactCount(deviceId: string, count: string): void {
  defaultManager.setContactCount(deviceId, count);
}

/**
 * Render the complete device list into the DOM.
 * Delegates to the default {@link DeviceManager} singleton.
 */
export function renderDevices(): void {
  defaultManager.renderDevices();
}

/**
 * AI-generate a name for a device's owner.
 * Delegates to the default {@link DeviceManager} singleton.
 *
 * @param deviceId - The unique ID of the target device.
 */
export function aiGenerateOwnerName(deviceId: string): Promise<void> {
  return defaultManager.aiGenerateOwnerName(deviceId);
}

/**
 * AI-generate owner names for all unnamed devices.
 * Delegates to the default {@link DeviceManager} singleton.
 */
export function aiGenerateAllOwnerNames(): Promise<void> {
  return defaultManager.aiGenerateAllOwnerNames();
}
