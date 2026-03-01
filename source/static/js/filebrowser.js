/* filebrowser.js -- Scenario file browser modal */

import { api } from './api.js';
import { showToast } from './toast.js';
import { openModal, closeModal } from './modals.js';
import { setScenario, registerExistingPhoneNumbers, syncThemeDropdown } from './state.js';
import { renderDevices } from './devices.js';

const MODAL_ID = 'file-browser-modal';

function ensureModal() {
    if (document.getElementById(MODAL_ID)) return;
    const overlay = document.createElement('div');
    overlay.id = MODAL_ID;
    overlay.className = 'modal-overlay hidden';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'fb-modal-title');
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeFileBrowser();
    });
    overlay.innerHTML = '<div class="modal-box" style="width:560px;">'
        + '<div class="modal-header">'
        + '<h3 id="fb-modal-title">Load Scenario</h3>'
        + '<button class="btn btn-danger btn-icon btn-sm" onclick="closeFileBrowser()">&times;</button>'
        + '</div>'
        + '<p style="color:var(--text-secondary);font-size:13px;margin-bottom:14px;">'
        + 'Select a saved scenario to load.'
        + '</p>'
        + '<div id="fb-list" class="file-browser-list"></div>'
        + '<div id="fb-empty" class="empty-state hidden"><p>No saved scenarios found.</p></div>'
        + '<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px;">'
        + '<button class="btn" onclick="closeFileBrowser()">Cancel</button>'
        + '</div>'
        + '</div>';
    document.body.appendChild(overlay);
}

function esc(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export async function openFileBrowser() {
    ensureModal();
    const list = document.getElementById('fb-list');
    const empty = document.getElementById('fb-empty');
    list.innerHTML = '<div style="text-align:center;padding:20px;"><span class="spinner"></span> Loading...</div>';
    empty.classList.add('hidden');
    openModal(MODAL_ID);

    try {
        const res = await api('GET', '/api/scenario/list');
        const scenarios = res.scenarios || [];
        if (scenarios.length === 0) {
            list.innerHTML = '';
            empty.classList.remove('hidden');
            return;
        }
        empty.classList.add('hidden');
        list.innerHTML = scenarios.map(s => {
            const date = new Date(s.modified_date);
            const dateStr = date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            return '<div class="file-browser-row" onclick="loadScenarioFromBrowser(\x27' + esc(s.id) + '\x27)">'
                + '<div>'
                + '<div class="fb-name">' + esc(s.name) + '</div>'
                + '<div class="fb-meta">' + esc(s.id) + ' &middot; ' + s.device_count + ' device' + (s.device_count !== 1 ? 's' : '') + '</div>'
                + '</div>'
                + '<div class="fb-meta">' + esc(dateStr) + '</div>'
                + '</div>';
        }).join('');
    } catch (e) {
        list.innerHTML = '<div class="empty-state"><p>Error loading scenarios.</p></div>';
    }
}

export function closeFileBrowser() {
    closeModal(MODAL_ID);
}

export async function loadScenarioFromBrowser(id) {
    closeFileBrowser();
    try {
        const data = await api('POST', '/api/scenario/load/' + id);
        if (data && !data.error) {
            setScenario(data);
            registerExistingPhoneNumbers();
            syncThemeDropdown();
            renderDevices();
            showToast('Scenario loaded', 'success');
        }
    } catch (e) {
        showToast('Failed to load scenario', 'error');
    }
}

export function initFileBrowser() {
    ensureModal();
}
