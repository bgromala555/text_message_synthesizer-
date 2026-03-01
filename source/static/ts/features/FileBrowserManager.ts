/**
 * Scenario file browser modal for listing and loading saved scenarios.
 *
 * Dynamically creates a modal overlay on first use, fetches the list of
 * saved scenarios from the server, and allows the user to load one into
 * the active workspace. The modal is managed through {@link ModalService}.
 * @module
 */

import type { Scenario } from '../shared/types.js';

import { api } from '../core/ApiClient.js';
import { registerExistingPhoneNumbers, setScenario, syncThemeDropdown } from '../core/AppState.js';
import { closeModal, openModal } from '../core/ModalService.js';
import { showToast } from '../core/ToastService.js';
import { esc } from '../shared/html-utils.js';

import { renderDevices } from './DeviceManager.js';

// ---------------------------------------------------------------------------
// Local types
// ---------------------------------------------------------------------------

/** DOM ID for the file-browser modal overlay element. */
const MODAL_ID = 'file-browser-modal';

/** Summary record for a single saved scenario in the list endpoint. */
interface ScenarioListItem {
  id: string;
  name: string;
  modified_date: string;
  device_count: number;
}

/** Response envelope from `GET /api/scenario/list`. */
interface ScenarioListResponse {
  scenarios: ScenarioListItem[];
}

/**
 * Extended scenario type allowing an optional `error` field on the load
 * response. The API client already throws on non-2xx, but this mirrors
 * the original defensive check.
 */
interface ScenarioLoadResponse extends Scenario {
  error?: string;
}

// ---------------------------------------------------------------------------
// FileBrowserManager
// ---------------------------------------------------------------------------

/**
 * Manages the file-browser modal lifecycle: creation, listing, and loading.
 *
 * The modal DOM is lazily created on first open via {@link ensureModal}
 * and reused on subsequent opens. Scenario rows are rendered dynamically
 * from the server list endpoint.
 */
export class FileBrowserManager {
  /**
   * Create the file-browser modal overlay if it does not already exist.
   *
   * Injects a dialog overlay with a header, description, list container,
   * empty-state placeholder, and a cancel button. Clicking the overlay
   * backdrop closes the modal.
   */
  private ensureModal(): void {
    if (document.getElementById(MODAL_ID)) return;

    const overlay = document.createElement('div');
    overlay.id = MODAL_ID;
    overlay.className = 'modal-overlay hidden';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'fb-modal-title');
    overlay.addEventListener('click', (e: MouseEvent) => {
      if (e.target === overlay) this.closeFileBrowser();
    });

    overlay.innerHTML =
      '<div class="modal-box" style="width:35rem;">' +
      '<div class="modal-header">' +
      '<h3 id="fb-modal-title">Load Scenario</h3>' +
      '<button class="btn btn-danger btn-icon btn-sm" onclick="closeFileBrowser()">&times;</button>' +
      '</div>' +
      '<p style="color:var(--text-secondary);font-size:0.8125rem;margin-bottom:0.875rem;">' +
      'Select a saved scenario to load.' +
      '</p>' +
      '<div id="fb-list" class="file-browser-list"></div>' +
      '<div id="fb-empty" class="empty-state hidden"><p>No saved scenarios found.</p></div>' +
      '<div style="display:flex;justify-content:flex-end;gap:0.5rem;margin-top:0.75rem;">' +
      '<button class="btn" onclick="closeFileBrowser()">Cancel</button>' +
      '</div>' +
      '</div>';

    document.body.appendChild(overlay);
  }

  /**
   * Open the file-browser modal and fetch the saved scenario list.
   *
   * Shows a loading spinner while the API call is in flight, then renders
   * scenario rows or an empty-state message.
   */
  public async openFileBrowser(): Promise<void> {
    this.ensureModal();

    const list = document.getElementById('fb-list');
    const empty = document.getElementById('fb-empty');
    if (!list || !empty) return;

    list.innerHTML = '<div style="text-align:center;padding:1.25rem;"><span class="spinner"></span> Loading...</div>';
    empty.classList.add('hidden');
    openModal(MODAL_ID);

    try {
      const res = await api<ScenarioListResponse>('GET', '/api/scenario/list');
      const scenarios = res.scenarios ?? [];
      if (scenarios.length === 0) {
        list.innerHTML = '';
        empty.classList.remove('hidden');
        return;
      }
      empty.classList.add('hidden');
      list.innerHTML = scenarios.map((s) => this.renderRow(s)).join('');
    } catch {
      list.innerHTML = '<div class="empty-state"><p>Error loading scenarios.</p></div>';
    }
  }

  /**
   * Build the HTML for a single scenario row in the file browser list.
   *
   * All user-supplied values are escaped via {@link esc} before injection.
   *
   * @param s - The scenario summary record from the list endpoint.
   * @returns HTML string for the row element.
   */
  private renderRow(s: ScenarioListItem): string {
    const date = new Date(s.modified_date);
    const dateStr = `${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    const plural = s.device_count !== 1 ? 's' : '';

    return (
      `<div class="file-browser-row" onclick="loadScenarioFromBrowser('${esc(s.id)}')">` +
      '<div>' +
      `<div class="fb-name">${esc(s.name)}</div>` +
      `<div class="fb-meta">${esc(s.id)} &middot; ${String(s.device_count)} device${plural}</div>` +
      '</div>' +
      `<div class="fb-meta">${esc(dateStr)}</div>` +
      '</div>'
    );
  }

  /**
   * Close the file-browser modal.
   */
  public closeFileBrowser(): void {
    closeModal(MODAL_ID);
  }

  /**
   * Load a saved scenario by ID and apply it to the workspace.
   *
   * Closes the modal, fetches the full scenario from the server, replaces
   * the in-memory state, syncs the UI dropdowns, re-renders devices, and
   * shows a confirmation toast.
   *
   * @param id - The unique identifier of the scenario to load.
   */
  public async loadScenarioFromBrowser(id: string): Promise<void> {
    this.closeFileBrowser();
    try {
      const data = await api<ScenarioLoadResponse>('POST', `/api/scenario/load/${id}`);
      if (data && !data.error) {
        setScenario(data);
        registerExistingPhoneNumbers();
        syncThemeDropdown();
        renderDevices();
        showToast('Scenario loaded', 'success');
      }
    } catch {
      showToast('Failed to load scenario', 'error');
    }
  }

  /**
   * Initialise the file browser by ensuring the modal DOM exists.
   */
  public initFileBrowser(): void {
    this.ensureModal();
  }
}

// ---------------------------------------------------------------------------
// Singleton + convenience exports
// ---------------------------------------------------------------------------

/** Default singleton used by the convenience exports. */
const defaultManager = new FileBrowserManager();

/**
 * Open the file-browser modal and fetch the saved scenario list.
 * Delegates to the default {@link FileBrowserManager} singleton.
 */
export function openFileBrowser(): Promise<void> {
  return defaultManager.openFileBrowser();
}

/**
 * Close the file-browser modal.
 * Delegates to the default {@link FileBrowserManager} singleton.
 */
export function closeFileBrowser(): void {
  defaultManager.closeFileBrowser();
}

/**
 * Load a saved scenario by ID and apply it to the workspace.
 * Delegates to the default {@link FileBrowserManager} singleton.
 *
 * @param id - The unique identifier of the scenario to load.
 */
export function loadScenarioFromBrowser(id: string): Promise<void> {
  return defaultManager.loadScenarioFromBrowser(id);
}

/**
 * Initialise the file browser by ensuring the modal DOM exists.
 * Delegates to the default {@link FileBrowserManager} singleton.
 */
export function initFileBrowser(): void {
  defaultManager.initFileBrowser();
}
