/* tabs.js -- Tab switching logic and status indicators */

import { renderContacts } from './contacts.js';
import { renderPersonalities } from './personalities.js';
import { renderStoryArc } from './storyarc.js';
import { renderLinkChart } from './linkchart.js';
import { renderEvents, renderGroupChats } from './events.js';
import { renderGenerate } from './generate.js';

export function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            const panel = document.getElementById('panel-' + btn.dataset.tab);
            if (panel) panel.classList.add('active');
            onTabSwitch(btn.dataset.tab);
        });
    });
}

export function onTabSwitch(tab) {
    if (tab === 'contacts') renderContacts();
    if (tab === 'personalities') renderPersonalities();
    if (tab === 'storyarc') renderStoryArc();
    if (tab === 'eventslinks') { renderLinkChart(); renderEvents(); renderGroupChats(); }
    if (tab === 'generate') renderGenerate();
}
