/* linkchart.js -- vis-network graph visualization, context menu, node inspector */

import { scenario, DEVICE_COLORS, uid, esc, syncScenario } from './state.js';
import { showToast } from './toast.js';
import { renderEvents, renderGroupChats, toggleEventParticipant } from './events.js';

// -----------------------------------------------------------------------
// Module-level state
// -----------------------------------------------------------------------

let linkChartNetwork = null;
let chartNodeMap = {};
let chartReverseMap = {};
let pendingLinkConnect = null;

const OWNER_SHAPES = ['diamond', 'star', 'triangle', 'square', 'triangleDown'];

// -----------------------------------------------------------------------
// Render link chart
// -----------------------------------------------------------------------

export function renderLinkChart() {
    const container = document.getElementById('link-chart-container');
    if (!container) return;

    chartNodeMap = {};
    chartReverseMap = {};

    if (scenario.devices.length === 0) {
        container.innerHTML = '<p style="text-align:center;color:var(--text-muted);padding:40px;">Add devices to see the link chart.</p>';
        return;
    }

    const nodes = [];
    const edges = [];
    let nodeId = 1;
    let hasShared = false, hasLocation = false, hasNearMiss = false, hasEvent = false;

    scenario.devices.forEach((dev, devIdx) => {
        const color = DEVICE_COLORS[devIdx % DEVICE_COLORS.length];
        const ownerShape = OWNER_SHAPES[devIdx % OWNER_SHAPES.length];
        const ownerId = nodeId++;
        chartNodeMap[dev.id + '::__owner__'] = ownerId;
        chartReverseMap[ownerId] = { device_id: dev.id, contact_id: '__owner__' };
        nodes.push({
            id: ownerId,
            label: dev.owner_name || dev.device_label,
            color: { background: color, border: '#fff', highlight: { background: '#fff', border: color } },
            font: { color: '#fff', size: 14, bold: { color: '#fff' } },
            shape: ownerShape, size: 30, borderWidth: 3,
            title: dev.device_label + ' (Owner)\nRight-click for options'
        });
        dev.contacts.forEach(c => {
            const cNodeId = nodeId++;
            const cKey = dev.id + '::' + c.id;
            chartNodeMap[cKey] = cNodeId;
            chartReverseMap[cNodeId] = { device_id: dev.id, contact_id: c.id };
            const isSharedContact = c.shared_with && c.shared_with.length > 0;
            const label = (c.name || c.actor_id) + (c.role ? '\n' + c.role : '');
            nodes.push({
                id: cNodeId, label: label,
                color: { background: color, border: isSharedContact ? '#ef4444' : color,
                    highlight: { background: '#fff', border: color } },
                font: { color: '#e8e8e8', size: 12, multi: 'md' },
                shape: 'dot', size: isSharedContact ? 22 : 16,
                borderWidth: isSharedContact ? 3 : 2,
                title: dev.device_label + ' \u2014 ' + (c.role || 'contact') +
                    (isSharedContact ? '\n\ud83d\udd17 SHARED across devices' : '') +
                    '\nRight-click for options'
            });
            edges.push({
                from: ownerId, to: cNodeId,
                color: { color: color + '55', highlight: color }, width: 1.5,
                dashes: [4, 4], arrows: ''
            });

            if (c.shared_with) {
                for (const link of c.shared_with) {
                    const targetKey = link.device_id + '::' + link.contact_id;
                    if (chartNodeMap[targetKey] !== undefined) {
                        hasShared = true;
                        edges.push({
                            from: cNodeId, to: chartNodeMap[targetKey],
                            color: { color: '#ef4444', highlight: '#ff6b6b' }, width: 4,
                            label: 'SAME PERSON', font: { color: '#ef4444', size: 10, strokeWidth: 2, strokeColor: '#000' },
                            smooth: { type: 'curvedCW', roundness: 0.2 },
                            title: 'SHARED CONTACT \u2014 same real person on both phones. They text with both owners directly.'
                        });
                    }
                }
            }
        });
    });

    // Connection edges
    scenario.connections.forEach(conn => {
        const fromKey = conn.source_device_id + '::' + conn.source_contact_id;
        const toKey = conn.target_device_id + '::' + conn.target_contact_id;
        if (chartNodeMap[fromKey] !== undefined && chartNodeMap[toKey] !== undefined) {
            let edgeColor = '#f59e0b';
            let tipText = 'LOCATION LINK \u2014 both devices reference this place.';
            if (conn.connection_type === 'near_miss') {
                edgeColor = '#a855f7';
                tipText = 'NEAR MISS \u2014 same place, same time, unknowingly. Forensic breadcrumb.';
                hasNearMiss = true;
            } else {
                hasLocation = true;
            }
            edges.push({
                from: chartNodeMap[fromKey], to: chartNodeMap[toKey],
                color: { color: edgeColor, highlight: '#fff' }, width: 3,
                label: conn.label || conn.connection_type,
                font: { color: edgeColor, size: 10, strokeWidth: 2, strokeColor: '#000' },
                smooth: { type: 'curvedCW', roundness: 0.2 },
                title: tipText
            });
        }
    });

    // Event edges
    const eventEdgeSet = new Set();
    scenario.timeline_events.forEach(ev => {
        const participants = ev.participants || [];
        if (participants.length < 2) return;
        const shortDesc = (ev.description || 'Event').substring(0, 30) + (ev.description?.length > 30 ? '\u2026' : '');
        for (let i = 0; i < participants.length; i++) {
            for (let j = i + 1; j < participants.length; j++) {
                const a = participants[i], b = participants[j];
                if (a.device_id === b.device_id) continue;
                const aKey = a.device_id + '::' + a.contact_id;
                const bKey = b.device_id + '::' + b.contact_id;
                const edgePairKey = [aKey, bKey].sort().join('|');
                if (eventEdgeSet.has(edgePairKey)) continue;
                eventEdgeSet.add(edgePairKey);
                if (chartNodeMap[aKey] !== undefined && chartNodeMap[bKey] !== undefined) {
                    hasEvent = true;
                    edges.push({
                        from: chartNodeMap[aKey], to: chartNodeMap[bKey],
                        color: { color: '#38bdf8', highlight: '#7dd3fc' }, width: 2.5,
                        dashes: [8, 5],
                        label: shortDesc,
                        font: { color: '#38bdf8', size: 9, strokeWidth: 2, strokeColor: '#000' },
                        smooth: { type: 'curvedCCW', roundness: 0.15 },
                        title: 'EVENT CO-PRESENCE \u2014 both at this event. Does NOT mean they know each other.\n\n' +
                               (ev.description || '')
                    });
                }
            }
        }
    });

    // Legend
    const legendEl = document.getElementById('link-chart-legend');
    if (legendEl) {
        let legendHtml = '';
        if (hasShared) legendHtml += '<span><span class="legend-line" style="background:#ef4444;"></span> Shared Contact</span>';
        if (hasEvent) legendHtml += '<span><span class="legend-line legend-dashed" style="border-color:#38bdf8;"></span> Event Co-presence</span>';
        if (hasLocation) legendHtml += '<span><span class="legend-line" style="background:#f59e0b;"></span> Location Link</span>';
        if (hasNearMiss) legendHtml += '<span><span class="legend-line" style="background:#a855f7;"></span> Near Miss</span>';
        scenario.devices.forEach((dev, idx) => {
            const c = DEVICE_COLORS[idx % DEVICE_COLORS.length];
            legendHtml += `<span><span class="legend-line" style="background:${c};"></span> ${esc(dev.device_label)}</span>`;
        });
        legendEl.innerHTML = legendHtml;
    }

    const data = { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) };
    const options = {
        physics: {
            forceAtlas2Based: { gravitationalConstant: -40, centralGravity: 0.005,
                springLength: 160, springConstant: 0.04 },
            solver: 'forceAtlas2Based', stabilization: { iterations: 120 }
        },
        interaction: { hover: true, multiselect: true, tooltipDelay: 150 },
        edges: { smooth: { enabled: true } }
    };
    linkChartNetwork = new vis.Network(container, data, options);

    linkChartNetwork.on('oncontext', function(params) {
        params.event.preventDefault();
        const nodeVisId = linkChartNetwork.getNodeAt(params.pointer.DOM);
        if (nodeVisId !== undefined) {
            showNodeContextMenu(nodeVisId, params.event.clientX, params.event.clientY);
        } else {
            hideNodeContextMenu();
        }
    });

    linkChartNetwork.on('click', function(params) {
        hideNodeContextMenu();
        if (pendingLinkConnect && params.nodes.length > 0) {
            handleLinkConnectSelection(params.nodes[0]);
            return;
        }
        if (params.nodes.length > 0) {
            showNodeInspector(params.nodes[0]);
        } else {
            hideNodeInspector();
        }
    });
}

// -----------------------------------------------------------------------
// Context menu
// -----------------------------------------------------------------------

export function showNodeContextMenu(visNodeId, x, y) {
    const ref = chartReverseMap[visNodeId];
    if (!ref) return;
    const dev = scenario.devices.find(d => d.id === ref.device_id);
    if (!dev) return;
    const isOwner = ref.contact_id === '__owner__';
    const contact = isOwner ? null : dev.contacts.find(c => c.id === ref.contact_id);
    const personName = isOwner ? (dev.owner_name || dev.device_label + ' Owner') : (contact?.name || contact?.actor_id || '?');

    let html = `<div class="context-menu-header">${esc(personName)}</div>`;
    html += `<div class="context-menu-section">Events</div>`;

    if (scenario.timeline_events.length === 0) {
        html += `<div class="context-menu-item" style="color:var(--text-muted);cursor:default;">No events yet</div>`;
    } else {
        scenario.timeline_events.forEach((ev, idx) => {
            const isIn = (ev.participants || []).some(p => p.device_id === ref.device_id && p.contact_id === ref.contact_id);
            const evLabel = (ev.description || 'Event ' + (idx + 1)).substring(0, 40);
            const dateLabel = ev.date ? ' (' + ev.date.substring(5) + ')' : '';
            html += `<div class="context-menu-item" onclick="contextToggleParticipant(${idx},'${ref.device_id}','${ref.contact_id}')">
                <span class="${isIn ? 'cm-check' : 'cm-empty'}">${isIn ? '\u2705' : ''}</span>
                <span>${esc(evLabel)}${dateLabel}</span>
            </div>`;
        });
    }

    html += `<div class="context-menu-section">Actions</div>`;
    html += `<div class="context-menu-item" onclick="startConnectFromNode('${ref.device_id}','${ref.contact_id}')">
        <span class="cm-empty">\ud83d\udd17</span> Start connect from ${esc(personName)}</div>`;
    html += `<div class="context-menu-item" onclick="contextAddEventWithPerson('${ref.device_id}','${ref.contact_id}')">
        <span class="cm-empty">+</span> Create new event with ${esc(personName)}</div>`;

    const menu = document.getElementById('node-context-menu');
    menu.innerHTML = html;
    menu.classList.remove('hidden');
    menu.style.left = Math.min(x, window.innerWidth - 340) + 'px';
    menu.style.top = Math.min(y, window.innerHeight - 380) + 'px';
}

export function hideNodeContextMenu() {
    const menu = document.getElementById('node-context-menu');
    if (menu) menu.classList.add('hidden');
}

export function contextToggleParticipant(evIdx, deviceId, contactId) {
    toggleEventParticipant(evIdx, deviceId, contactId);
    hideNodeContextMenu();
}

export function contextAddEventWithPerson(deviceId, contactId) {
    scenario.timeline_events.push({
        id: uid(), date: scenario.generation_settings.date_start, time: null,
        description: '', encounter_type: 'planned', device_impacts: {},
        involved_contacts: {}, participants: [{ device_id: deviceId, contact_id: contactId }]
    });
    renderEvents();
    renderLinkChart();
    syncScenario();
    hideNodeContextMenu();
    const el = document.getElementById('event-' + (scenario.timeline_events.length - 1));
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

export function startConnectFromNode(deviceId, contactId) {
    pendingLinkConnect = { device_id: deviceId, contact_id: contactId };
    hideNodeContextMenu();
    showToast('Connect mode: now left-click another node to create a linked event.');
}

function _labelForRef(ref) {
    const dev = scenario.devices.find(d => d.id === ref.device_id);
    if (!dev) return '?';
    if (ref.contact_id === '__owner__') return dev.owner_name || dev.device_label;
    const c = dev.contacts.find(x => x.id === ref.contact_id);
    return c ? (c.name || c.actor_id) : '?';
}

function handleLinkConnectSelection(targetVisNodeId) {
    if (!pendingLinkConnect) return;
    const targetRef = chartReverseMap[targetVisNodeId];
    const srcRef = pendingLinkConnect;
    pendingLinkConnect = null;
    if (!targetRef) return;
    if (srcRef.device_id === targetRef.device_id && srcRef.contact_id === targetRef.contact_id) {
        showToast('Select a different node.');
        return;
    }

    const encounterRaw = prompt('Event type? planned / chance_encounter / near_miss', 'near_miss');
    const encounter = (encounterRaw || 'near_miss').trim();
    const valid = ['planned', 'chance_encounter', 'near_miss'];
    const encounterType = valid.includes(encounter) ? encounter : 'near_miss';

    const srcLabel = _labelForRef(srcRef);
    const tgtLabel = _labelForRef(targetRef);
    const descDefault = encounterType === 'near_miss'
        ? `${srcLabel} and ${tgtLabel} were at the same location around the same time but did not directly interact.`
        : `${srcLabel} and ${tgtLabel} were present at the same event.`;
    const desc = prompt('Event description', descDefault) || descDefault;
    const createGroup = confirm('Also seed a group chat from this event?');

    const ev = {
        id: uid(),
        date: scenario.generation_settings.date_start,
        time: null,
        description: desc,
        encounter_type: encounterType,
        device_impacts: {},
        involved_contacts: {},
        participants: [srcRef, targetRef]
    };
    scenario.timeline_events.push(ev);

    if (createGroup) {
        const members = [srcRef, targetRef];
        if (!members.some(m => m.contact_id === '__owner__')) {
            members.push({ device_id: srcRef.device_id, contact_id: '__owner__' });
        }
        if (!scenario.group_chats) scenario.group_chats = [];
        scenario.group_chats.push({
            id: uid(),
            name: `${srcLabel} + ${tgtLabel}`,
            members: members,
            origin_event_id: ev.id,
            start_date: ev.date || scenario.generation_settings.date_start,
            end_date: '',
            message_volume: 'light',
            vibe: encounterType === 'near_miss' ? 'post-event comparison and indirect references' : 'event follow-up coordination',
            activation_mode: 'event_time',
            auto_pair_threads: true,
            quality_score: 1.0
        });
    }

    renderEvents();
    renderGroupChats();
    renderLinkChart();
    syncScenario();
    showToast('Created linked event' + (createGroup ? ' and seeded group chat.' : '.'));
}

// -----------------------------------------------------------------------
// Node Inspector
// -----------------------------------------------------------------------

export function showNodeInspector(visNodeId) {
    const ref = chartReverseMap[visNodeId];
    if (!ref) return;
    const dev = scenario.devices.find(d => d.id === ref.device_id);
    if (!dev) return;
    const isOwner = ref.contact_id === '__owner__';
    const contact = isOwner ? null : dev.contacts.find(c => c.id === ref.contact_id);
    const name = isOwner ? (dev.owner_name || dev.device_label) : (contact?.name || '?');
    const role = isOwner ? 'Phone Owner' : (contact?.role || 'contact');
    const devIdx = scenario.devices.indexOf(dev);
    const color = DEVICE_COLORS[devIdx % DEVICE_COLORS.length];
    const profile = isOwner ? dev.owner_personality : contact?.personality;
    const isShared = !isOwner && contact?.shared_with?.length > 0;

    const eventsIn = [];
    scenario.timeline_events.forEach((ev, idx) => {
        const inEv = (ev.participants || []).some(p => p.device_id === ref.device_id && p.contact_id === ref.contact_id);
        if (inEv) eventsIn.push({ idx, ev });
    });

    const personLocations = new Set();
    eventsIn.forEach(({ ev }) => {
        extractLocations(ev.description || '').forEach(l => personLocations.add(l));
        const impact = (ev.device_impacts || {})[dev.id];
        if (impact) extractLocations(impact).forEach(l => personLocations.add(l));
    });

    const crossLinks = [];
    if (isShared && contact?.shared_with) {
        contact.shared_with.forEach(link => {
            const od = scenario.devices.find(d => d.id === link.device_id);
            const oc = od?.contacts.find(x => x.id === link.contact_id);
            crossLinks.push({ type: 'shared', label: 'Same person on ' + (od?.device_label || '?'), name: oc?.name || '?' });
        });
    }
    eventsIn.forEach(({ ev }) => {
        (ev.participants || []).forEach(p => {
            if (p.device_id === ref.device_id) return;
            const od = scenario.devices.find(d => d.id === p.device_id);
            if (!od) return;
            let pName;
            if (p.contact_id === '__owner__') pName = od.owner_name || od.device_label;
            else pName = od.contacts.find(x => x.id === p.contact_id)?.name || '?';
            const shortDesc = (ev.description || '').substring(0, 50);
            crossLinks.push({ type: 'event', label: shortDesc, name: pName + ' (' + od.device_label + ')' });
        });
    });

    let html = `<div class="inspector-header">
        <span class="device-number" style="background:${color}">${devIdx + 1}</span>
        <span class="inspector-name">${esc(name)}</span>
        <span class="badge badge-blue">${esc(role)}</span>
        ${isShared ? '<span class="badge badge-orange">Shared</span>' : ''}
        <span style="font-size:11px;color:var(--text-muted);">${esc(dev.device_label)}</span>
    </div>`;

    html += '<div class="inspector-grid">';

    html += '<div class="inspector-section"><h5>Personality</h5>';
    if (profile?.personality_summary) {
        html += '<p>' + esc(profile.personality_summary) + '</p>';
    } else {
        html += '<p style="color:var(--text-muted);">No profile set</p>';
    }
    if (profile?.neighborhood) html += '<p style="margin-top:4px;">Neighborhood: <strong>' + esc(profile.neighborhood) + '</strong></p>';
    if (profile?.job_details) html += '<p>Job: ' + esc(profile.job_details) + '</p>';
    html += '</div>';

    html += '<div class="inspector-section"><h5>Events (' + eventsIn.length + ')</h5>';
    if (eventsIn.length > 0) {
        html += '<ul>';
        eventsIn.forEach(({ idx, ev }) => {
            html += '<li><strong>' + esc(ev.date || '?') + '</strong>: ' +
                esc((ev.description || '').substring(0, 80)) +
                (ev.description?.length > 80 ? '\u2026' : '') + '</li>';
        });
        html += '</ul>';
    } else {
        html += '<p style="color:var(--text-muted);">Not in any events</p>';
    }
    html += '</div>';

    html += '<div class="inspector-section"><h5>Locations</h5>';
    if (personLocations.size > 0) {
        html += '<div>' + [...personLocations].map(l => '<span class="inspector-location">' + esc(l) + '</span>').join(' ') + '</div>';
    } else {
        html += '<p style="color:var(--text-muted);">No locations extracted</p>';
    }
    html += '</div>';

    html += '<div class="inspector-section"><h5>Cross-Device Links (' + crossLinks.length + ')</h5>';
    if (crossLinks.length > 0) {
        html += '<ul>';
        crossLinks.forEach(link => {
            const icon = link.type === 'shared' ? '\ud83d\udd17' : '\ud83d\udcc5';
            html += '<li>' + icon + ' ' + esc(link.name) + ' \u2014 ' + esc(link.label) + '</li>';
        });
        html += '</ul>';
    } else {
        html += '<p style="color:var(--text-muted);">No cross-device links</p>';
    }
    html += '</div>';

    html += '</div>';

    const panel = document.getElementById('node-inspector');
    panel.innerHTML = html;
    panel.classList.remove('hidden');
}

export function hideNodeInspector() {
    const panel = document.getElementById('node-inspector');
    if (panel) panel.classList.add('hidden');
}

// -----------------------------------------------------------------------
// Location extraction
// -----------------------------------------------------------------------

export function extractLocations(text) {
    if (!text) return [];
    const locations = [];
    const patterns = [
        /(?:at|near|in|on|from|visit(?:ing)?|went to|headed to|going to|arrives? at|meet(?:ing)? at)\s+([A-Z][A-Za-z\u2019']+(?:\s+[A-Z][A-Za-z\u2019']+){0,4})/g,
        /(?:Central|Prospect|Bryant|Washington Square|Madison Square|Union Square|Times Square|Brooklyn Bridge|Coney Island|Rockaway|Williamsburg|Bushwick|Astoria|Harlem|Chelsea|SoHo|TriBeCa|Midtown|Greenpoint|Dumbo|Flatbush|Park Slope|Bed-Stuy|Fort Greene|Crown Heights|Sunset Park|Bay Ridge|Red Hook|Cobble Hill|Boerum Hill|Carroll Gardens|Jackson Heights|Flushing|Long Island City|Upper East Side|Upper West Side|Lower East Side|East Village|West Village|Greenwich Village|Nolita|NoHo|Financial District|Battery Park|Hell's Kitchen|Murray Hill|Gramercy|Kips Bay|Flatiron|Koreatown)\b/gi,
    ];
    for (const re of patterns) {
        let m;
        while ((m = re.exec(text)) !== null) {
            const loc = (m[1] || m[0]).trim().replace(/[.,;:!?]+$/, '');
            if (loc.length >= 4 && loc.length <= 60) locations.push(loc);
        }
    }
    return [...new Set(locations)];
}
