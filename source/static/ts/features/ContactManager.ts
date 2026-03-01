/**
 * Contact CRUD operations and rendering for the contacts configuration panel.
 *
 * Manages adding, removing, and editing contacts within each device, as well
 * as shared/mutual contact linkage across device pairs. Rendering produces
 * card-based HTML injected via innerHTML with all user-supplied values
 * escaped through {@link esc}.
 * @module
 */

import type { Contact, Device } from '../shared/types.js';

import { api } from '../core/ApiClient.js';
import {
  DEVICE_COLORS,
  THEME_LABELS,
  generatePhoneNumber,
  scenario,
  suggestVolumeFromRole,
  syncScenario,
  uid,
} from '../core/AppState.js';
import { showToast } from '../core/ToastService.js';

import { esc } from '../shared/html-utils.js';

/**
 * String-valued fields on {@link Contact} that can be set from HTML form inputs.
 * Used for type-safe dynamic field assignment in {@link ContactManager.updateContact}.
 */
type MutableContactField = 'name' | 'role' | 'message_volume';

/** Response payload from the AI name-generation endpoint. */
interface AiGenerateNamesResponse {
  names?: string[];
  roles?: string[];
}

/**
 * Represents a pair of linked (shared) contacts across two devices,
 * used internally for rendering the shared-contacts summary.
 */
interface SharedContactPair {
  readonly dev1: Device;
  readonly c1: Contact;
  readonly dev2: Device | undefined;
  readonly c2: Contact | undefined;
}

/**
 * Manages contact lifecycle, shared-contact linkage, and rendering
 * within the scenario builder.
 *
 * Provides CRUD operations for per-device contacts, mutual contact
 * pairing between devices, card-based HTML rendering, and AI-powered
 * contact name generation.
 */
export class ContactManager {
  // -------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------

  /**
   * Render the complete contacts panel into the DOM.
   *
   * Rebuilds the contacts list from the current scenario state, including
   * the shared/mutual contacts section (when 2+ devices exist) and per-device
   * contact cards. Shows an empty-state message when no devices exist.
   */
  public renderContacts(): void {
    const list = document.getElementById('contacts-list');
    const empty = document.getElementById('contacts-empty');
    if (!list || !empty) return;

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
            ${this.renderMutualPairControls()}
            ${this.renderExistingSharedContacts()}
        </div>`;
    }

    html += scenario.devices.map((dev, devIdx) => this.renderDeviceBlock(dev, devIdx)).join('');

    list.innerHTML = html;
  }

  /**
   * Build the HTML for a single device's contact block.
   *
   * Produces a card with the device header, AI-names button, add-contact
   * button, and a row for each contact.
   *
   * @param dev - The device whose contacts to render.
   * @param devIdx - Zero-based position of the device in the list.
   * @returns HTML string for the device contact block.
   */
  private renderDeviceBlock(dev: Device, devIdx: number): string {
    const color = DEVICE_COLORS[devIdx % DEVICE_COLORS.length];
    const contactRows = dev.contacts.map((c) => this.renderContactRow(dev, c)).join('');
    const emptyMsg =
      dev.contacts.length === 0 ? '<p style="color:var(--text-muted);font-size:13px;">No contacts.</p>' : '';

    return `
        <div class="card device-card" style="border-left-color:${color}">
            <div class="card-header">
                <h3>
                    <span class="device-number" style="background:${color}">${String(devIdx + 1)}</span>
                    ${esc(dev.device_label)} \u2014 ${esc(dev.owner_name || 'Unnamed Owner')}
                </h3>
                <div>
                    <button class="btn btn-ai btn-sm" onclick="aiGenerateNamesForDevice('${dev.id}')">AI Names</button>
                    <button class="btn btn-sm" onclick="addContactToDevice('${dev.id}')">+ Contact</button>
                </div>
            </div>
            ${emptyMsg}
            ${contactRows}
        </div>
    `;
  }

  /**
   * Build the HTML for a single contact row within a device card.
   *
   * Renders the contact's phone number, name input, role input,
   * volume selector, shared badge, and remove button.
   *
   * @param dev - The parent device (used for event handler IDs).
   * @param c - The contact to render.
   * @returns HTML string for the contact row.
   */
  private renderContactRow(dev: Device, c: Contact): string {
    const sharedBadge =
      c.shared_with.length > 0 ? '<span class="badge badge-orange" style="margin-left:6px;">Shared</span>' : '';
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
                        <option value="heavy" ${vol === 'heavy' ? 'selected' : ''}>Heavy</option>
                        <option value="regular" ${vol === 'regular' ? 'selected' : ''}>Regular</option>
                        <option value="light" ${vol === 'light' ? 'selected' : ''}>Light</option>
                        <option value="minimal" ${vol === 'minimal' ? 'selected' : ''}>Minimal</option>
                    </select>
                    ${sharedBadge}
                    <button class="btn btn-danger btn-icon btn-sm" onclick="removeContact('${dev.id}','${c.id}')">&times;</button>
                </div>`;
  }

  // -------------------------------------------------------------------
  // Mutual / Shared contacts
  // -------------------------------------------------------------------

  /**
   * Build the HTML controls for setting mutual contact counts between device pairs.
   *
   * Generates one row per unique device pair with a numeric input
   * for adjusting the number of shared contacts.
   *
   * @returns HTML string for the mutual pair control rows.
   */
  private renderMutualPairControls(): string {
    const pairs: [Device, Device][] = [];
    for (let i = 0; i < scenario.devices.length; i++) {
      for (let j = i + 1; j < scenario.devices.length; j++) {
        pairs.push([scenario.devices[i], scenario.devices[j]]);
      }
    }

    return pairs
      .map(([d1, d2]) => {
        const currentCount = this.countSharedBetween(d1.id, d2.id);
        return `<div class="contact-row" style="margin-bottom:8px;">
            <span style="flex:none;font-size:13px;color:var(--text-primary);">
                ${esc(d1.device_label)} \u2194 ${esc(d2.device_label)}
            </span>
            <span style="color:var(--text-secondary);font-size:12px;flex:none;">
                ${String(currentCount)} shared
            </span>
            <input type="number" value="${String(currentCount)}" min="0" max="10" style="width:60px;flex:none;"
                onchange="setMutualCount('${d1.id}','${d2.id}',parseInt(this.value)||0)">
            <span style="color:var(--text-muted);font-size:11px;">mutual contacts</span>
        </div>`;
      })
      .join('');
  }

  /**
   * Build the HTML summary of existing shared contact links.
   *
   * Collects all shared contact pairs (de-duplicated so each pair appears
   * only once) and renders them as highlighted rows with an unlink button.
   *
   * @returns HTML string for the shared contacts summary, or empty string
   *   if no shared contacts exist.
   */
  private renderExistingSharedContacts(): string {
    const shared: SharedContactPair[] = [];

    for (const dev of scenario.devices) {
      for (const c of dev.contacts) {
        if (c.shared_with.length === 0) continue;
        for (const link of c.shared_with) {
          const otherDev = scenario.devices.find((d) => d.id === link.device_id);
          const otherContact = otherDev?.contacts.find((x) => x.id === link.contact_id);
          const devIdx = scenario.devices.indexOf(dev);
          const otherDevIdx = otherDev ? scenario.devices.indexOf(otherDev) : 999;

          // Only add once per pair (lower-index device owns the entry)
          if (devIdx < otherDevIdx) {
            shared.push({
              dev1: dev,
              c1: c,
              dev2: otherDev,
              c2: otherContact,
            });
          }
        }
      }
    }

    if (shared.length === 0) return '';

    return (
      '<div style="margin-top:12px;">' +
      shared
        .map(
          (s) => `
        <div class="contact-row" style="background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.2);">
            <span style="font-size:12px;color:var(--orange);flex:none;">SHARED</span>
            <span style="font-size:13px;">${esc(s.c1.name || 'unnamed')} on ${esc(s.dev1.device_label || '?')}</span>
            <span style="color:var(--text-muted);">\u2194</span>
            <span style="font-size:13px;">${esc(s.c2?.name || 'unnamed')} on ${esc(s.dev2?.device_label || '?')}</span>
            <button class="btn btn-danger btn-icon btn-sm"
                onclick="unlinkShared('${s.dev1.id}','${s.c1.id}','${s.dev2?.id}','${s.c2?.id}')">&times;</button>
        </div>
    `
        )
        .join('') +
      '</div>'
    );
  }

  /**
   * Count the number of shared contacts between two devices.
   *
   * Scans the first device's contacts for shared_with entries that
   * reference the second device.
   *
   * @param devId1 - ID of the first device.
   * @param devId2 - ID of the second device.
   * @returns The number of contacts on device 1 shared with device 2.
   */
  public countSharedBetween(devId1: string, devId2: string): number {
    const dev1 = scenario.devices.find((d) => d.id === devId1);
    if (!dev1) return 0;

    let count = 0;
    for (const c of dev1.contacts) {
      if (c.shared_with.some((s) => s.device_id === devId2)) count++;
    }
    return count;
  }

  /**
   * Set the number of mutual contacts between two devices to a target value.
   *
   * When the target exceeds the current count, new paired contacts are
   * created on both devices with matching phone numbers and cross-references.
   * When the target is lower, shared contact pairs are removed from the end.
   *
   * @param devId1 - ID of the first device.
   * @param devId2 - ID of the second device.
   * @param target - The desired number of shared contacts.
   */
  public setMutualCount(devId1: string, devId2: string, target: number): void {
    const dev1 = scenario.devices.find((d) => d.id === devId1);
    const dev2 = scenario.devices.find((d) => d.id === devId2);
    if (!dev1 || !dev2) return;

    const current = this.countSharedBetween(devId1, devId2);

    for (let i = current; i < target; i++) {
      const c1id = uid();
      const c2id = uid();
      const sharedPhone = generatePhoneNumber();

      const sharedContact1: Contact = {
        id: c1id,
        actor_id: sharedPhone,
        name: '',
        role: '',
        message_volume: 'regular',
        story_arc: '',
        personality: null,
        shared_with: [{ device_id: devId2, contact_id: c2id }],
      };
      const sharedContact2: Contact = {
        id: c2id,
        actor_id: sharedPhone,
        name: '',
        role: '',
        message_volume: 'regular',
        story_arc: '',
        personality: null,
        shared_with: [{ device_id: devId1, contact_id: c1id }],
      };

      dev1.contacts.push(sharedContact1);
      dev2.contacts.push(sharedContact2);
    }

    if (target < current) {
      let toRemove = current - target;
      for (let i = dev1.contacts.length - 1; i >= 0 && toRemove > 0; i--) {
        const c = dev1.contacts[i];
        const link = c.shared_with.find((s) => s.device_id === devId2);
        if (link) {
          dev2.contacts = dev2.contacts.filter((x) => x.id !== link.contact_id);
          dev1.contacts.splice(i, 1);
          toRemove--;
        }
      }
    }

    this.renderContacts();
    void syncScenario();
  }

  /**
   * Add one shared contact between the first two devices.
   *
   * Convenience method that increments the mutual count between device 0
   * and device 1 by one. Requires at least two devices.
   */
  public addSharedContactManual(): void {
    if (scenario.devices.length < 2) {
      showToast('Need 2+ devices');
      return;
    }
    const d1 = scenario.devices[0];
    const d2 = scenario.devices[1];
    this.setMutualCount(d1.id, d2.id, this.countSharedBetween(d1.id, d2.id) + 1);
  }

  /**
   * Remove the shared link between two specific contacts on different devices.
   *
   * Removes the cross-reference entries from both contacts' shared_with
   * arrays without deleting the contacts themselves.
   *
   * @param devId1 - ID of the first device.
   * @param cId1 - ID of the contact on the first device.
   * @param devId2 - ID of the second device.
   * @param cId2 - ID of the contact on the second device.
   */
  public unlinkShared(devId1: string, cId1: string, devId2: string, cId2: string): void {
    const d1 = scenario.devices.find((d) => d.id === devId1);
    const d2 = scenario.devices.find((d) => d.id === devId2);

    if (d1) {
      const c1 = d1.contacts.find((c) => c.id === cId1);
      if (c1) {
        c1.shared_with = c1.shared_with.filter((s) => s.contact_id !== cId2);
      }
    }
    if (d2) {
      const c2 = d2.contacts.find((c) => c.id === cId2);
      if (c2) {
        c2.shared_with = c2.shared_with.filter((s) => s.contact_id !== cId1);
      }
    }

    this.renderContacts();
    void syncScenario();
  }

  // -------------------------------------------------------------------
  // CRUD
  // -------------------------------------------------------------------

  /**
   * Add a new empty contact to a device.
   *
   * Creates a contact with a generated phone number and default settings,
   * appends it to the device's contacts, and re-renders.
   *
   * @param deviceId - The ID of the device to add the contact to.
   */
  public addContactToDevice(deviceId: string): void {
    const dev = scenario.devices.find((d) => d.id === deviceId);
    if (!dev) return;

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
    this.renderContacts();
    void syncScenario();
  }

  /**
   * Remove a contact from a device, cleaning up any shared links.
   *
   * Before removal, iterates the contact's shared_with entries to remove
   * back-references from linked contacts on other devices.
   *
   * @param deviceId - The ID of the device containing the contact.
   * @param contactId - The ID of the contact to remove.
   */
  public removeContact(deviceId: string, contactId: string): void {
    const dev = scenario.devices.find((d) => d.id === deviceId);
    if (!dev) return;

    const c = dev.contacts.find((x) => x.id === contactId);
    if (c) {
      for (const link of c.shared_with) {
        const otherDev = scenario.devices.find((d) => d.id === link.device_id);
        if (otherDev) {
          const otherC = otherDev.contacts.find((x) => x.id === link.contact_id);
          if (otherC) {
            otherC.shared_with = otherC.shared_with.filter((s) => s.contact_id !== contactId);
          }
        }
      }
    }

    dev.contacts = dev.contacts.filter((x) => x.id !== contactId);
    this.renderContacts();
    void syncScenario();
  }

  /**
   * Update a single field on a contact.
   *
   * Performs dynamic property assignment for fields edited via inline HTML
   * inputs. When the name field is changed, the new name is propagated to
   * all linked contacts on other devices.
   *
   * @param deviceId - The ID of the device containing the contact.
   * @param contactId - The ID of the contact to update.
   * @param field - The contact property to modify.
   * @param value - The new string value from the form input.
   */
  public updateContact(deviceId: string, contactId: string, field: MutableContactField, value: string): void {
    const dev = scenario.devices.find((d) => d.id === deviceId);
    if (!dev) return;

    const contact = dev.contacts.find((c) => c.id === contactId);
    if (contact) {
      // All MutableContactField keys map to string-compatible types on Contact.
      const record = contact as unknown as Record<string, string>;
      record[field] = value;

      // Propagate name changes to linked contacts on other devices
      if (field === 'name') {
        for (const link of contact.shared_with) {
          const otherDev = scenario.devices.find((d) => d.id === link.device_id);
          const otherC = otherDev?.contacts.find((x) => x.id === link.contact_id);
          if (otherC) otherC.name = value;
        }
      }
    }
    void syncScenario();
  }

  // -------------------------------------------------------------------
  // AI name generation
  // -------------------------------------------------------------------

  /**
   * AI-generate names and roles for unnamed contacts on a specific device.
   *
   * Calls the `/api/ai/generate-names` endpoint with the count of unnamed
   * contacts, then assigns the returned names and roles. Also infers
   * message volume from the assigned role. Name changes are propagated
   * to linked contacts on other devices.
   *
   * @param deviceId - The ID of the device whose contacts need names.
   */
  public async aiGenerateNamesForDevice(deviceId: string): Promise<void> {
    const dev = scenario.devices.find((d) => d.id === deviceId);
    if (!dev) return;

    const unnamed = dev.contacts.filter((c) => !c.name);
    if (unnamed.length === 0) {
      showToast('All contacts already have names');
      return;
    }

    showToast('Generating names + roles...');
    const themeLabel = THEME_LABELS[scenario.theme] || scenario.theme;

    try {
      const effectiveRoleStyle =
        (dev.generation_mode || 'story') === 'standalone' ? 'normal' : dev.role_style || 'normal';

      const res = await api<AiGenerateNamesResponse>('POST', '/api/ai/generate-names', {
        count: unnamed.length,
        context: `SMS conversation scenario \u2014 theme: ${themeLabel}`,
        owner_name: dev.owner_name || '',
        theme: scenario.theme || 'slice-of-life',
        culture: scenario.culture || 'american',
        generation_mode: dev.generation_mode || 'story',
        role_style: effectiveRoleStyle,
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

            // Propagate name to shared contacts on other devices
            for (const link of unnamed[i].shared_with) {
              const od = scenario.devices.find((d) => d.id === link.device_id);
              const oc = od?.contacts.find((x) => x.id === link.contact_id);
              if (oc) oc.name = name;
            }
          }
        });

        this.renderContacts();
        void syncScenario();
        showToast('Names + roles + volumes generated');
      }
    } catch {
      // Toast already shown by api()
    }
  }

  /**
   * AI-generate names for all unnamed contacts across every device.
   *
   * Iterates through devices sequentially and calls
   * {@link aiGenerateNamesForDevice} for each one.
   */
  public async aiGenerateAllNames(): Promise<void> {
    for (const dev of scenario.devices) {
      await this.aiGenerateNamesForDevice(dev.id);
    }
  }
}

/** Default singleton used by the convenience exports. */
const defaultManager = new ContactManager();

/**
 * Render the complete contacts panel into the DOM.
 * Delegates to the default {@link ContactManager} singleton.
 */
export function renderContacts(): void {
  defaultManager.renderContacts();
}

/**
 * Count the number of shared contacts between two devices.
 * Delegates to the default {@link ContactManager} singleton.
 *
 * @param devId1 - ID of the first device.
 * @param devId2 - ID of the second device.
 * @returns The number of contacts on device 1 shared with device 2.
 */
export function countSharedBetween(devId1: string, devId2: string): number {
  return defaultManager.countSharedBetween(devId1, devId2);
}

/**
 * Set the number of mutual contacts between two devices.
 * Delegates to the default {@link ContactManager} singleton.
 *
 * @param devId1 - ID of the first device.
 * @param devId2 - ID of the second device.
 * @param target - The desired number of shared contacts.
 */
export function setMutualCount(devId1: string, devId2: string, target: number): void {
  defaultManager.setMutualCount(devId1, devId2, target);
}

/**
 * Add one shared contact between the first two devices.
 * Delegates to the default {@link ContactManager} singleton.
 */
export function addSharedContactManual(): void {
  defaultManager.addSharedContactManual();
}

/**
 * Remove a shared link between two contacts on different devices.
 * Delegates to the default {@link ContactManager} singleton.
 *
 * @param devId1 - ID of the first device.
 * @param cId1 - ID of the contact on the first device.
 * @param devId2 - ID of the second device.
 * @param cId2 - ID of the contact on the second device.
 */
export function unlinkShared(devId1: string, cId1: string, devId2: string, cId2: string): void {
  defaultManager.unlinkShared(devId1, cId1, devId2, cId2);
}

/**
 * Add a new empty contact to a device.
 * Delegates to the default {@link ContactManager} singleton.
 *
 * @param deviceId - The ID of the device to add the contact to.
 */
export function addContactToDevice(deviceId: string): void {
  defaultManager.addContactToDevice(deviceId);
}

/**
 * Remove a contact from a device.
 * Delegates to the default {@link ContactManager} singleton.
 *
 * @param deviceId - The ID of the device containing the contact.
 * @param contactId - The ID of the contact to remove.
 */
export function removeContact(deviceId: string, contactId: string): void {
  defaultManager.removeContact(deviceId, contactId);
}

/**
 * Update a single field on a contact.
 * Delegates to the default {@link ContactManager} singleton.
 *
 * @param deviceId - The ID of the device containing the contact.
 * @param contactId - The ID of the contact to update.
 * @param field - The contact property to modify.
 * @param value - The new string value from the form input.
 */
export function updateContact(deviceId: string, contactId: string, field: MutableContactField, value: string): void {
  defaultManager.updateContact(deviceId, contactId, field, value);
}

/**
 * AI-generate names and roles for unnamed contacts on a device.
 * Delegates to the default {@link ContactManager} singleton.
 *
 * @param deviceId - The ID of the target device.
 */
export function aiGenerateNamesForDevice(deviceId: string): Promise<void> {
  return defaultManager.aiGenerateNamesForDevice(deviceId);
}

/**
 * AI-generate names for all unnamed contacts across every device.
 * Delegates to the default {@link ContactManager} singleton.
 */
export function aiGenerateAllNames(): Promise<void> {
  return defaultManager.aiGenerateAllNames();
}
