/* generate.js -- Generation panel UI, progress display, SSE stream handling */

import { scenario, DEVICE_COLORS, THEME_LABELS, LANGUAGE_LABELS, esc, syncScenario } from './state.js';
import { api } from './api.js';
import { showToast } from './toast.js';
import { extractLocations } from './linkchart.js';

// -----------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------

function countCrossDeviceEventLinks() {
    const pairSet = new Set();
    scenario.timeline_events.forEach(ev => {
        const parts = ev.participants || [];
        for (let i = 0; i < parts.length; i++) {
            for (let j = i + 1; j < parts.length; j++) {
                if (parts[i].device_id !== parts[j].device_id) {
                    const key = [parts[i].device_id + '::' + parts[i].contact_id,
                                 parts[j].device_id + '::' + parts[j].contact_id].sort().join('|');
                    pairSet.add(key);
                }
            }
        }
    });
    return pairSet.size;
}

function collectAllLocations() {
    const locs = new Set();
    scenario.timeline_events.forEach(ev => {
        extractLocations(ev.description || '').forEach(l => locs.add(l));
        Object.values(ev.device_impacts || {}).forEach(txt => {
            extractLocations(txt).forEach(l => locs.add(l));
        });
    });
    return [...locs];
}

function estimateTotalMessages() {
    const VOLUME_DENSITY = { heavy: 0.65, regular: 0.35, light: 0.18, minimal: 0.10 };
    const VOLUME_SKIP    = { heavy: 0.12, regular: 0.45, light: 0.82, minimal: 0.96 };
    const s = scenario.generation_settings;
    const start = new Date(s.date_start);
    const end = new Date(s.date_end);
    const days = Math.max(1, Math.round((end - start) / 86400000) + 1);
    const avgBase = (s.messages_per_day_min + s.messages_per_day_max) / 2;
    let total = 0;
    for (const dev of scenario.devices) {
        for (const c of dev.contacts) {
            const vol = c.message_volume || 'regular';
            const density = VOLUME_DENSITY[vol] || 0.5;
            const skip = VOLUME_SKIP[vol] || 0.25;
            const avgPerDay = avgBase * density;
            const activeDays = days * (1 - skip);
            total += Math.round(avgPerDay * activeDays);
        }
    }
    return total;
}

// -----------------------------------------------------------------------
// Render Summary + Settings
// -----------------------------------------------------------------------

export function renderGenerate() {
    const totalDevices = scenario.devices.length;
    const totalContacts = scenario.devices.reduce((sum, d) => sum + d.contacts.length, 0);
    const totalEvents = scenario.timeline_events.length;
    const sharedPairs = scenario.devices.reduce((sum, d) =>
        sum + d.contacts.filter(c => c.shared_with?.length > 0).length, 0);
    const sharedCount = sharedPairs / 2 | 0;
    const eventLinks = countCrossDeviceEventLinks();
    const totalLinks = sharedCount + eventLinks;
    const profilesDone = scenario.devices.reduce((sum, d) => {
        let count = d.owner_personality ? 1 : 0;
        count += d.contacts.filter(c => c.personality).length;
        return sum + count;
    }, 0);
    const profilesTotal = totalDevices + totalContacts;
    const locations = collectAllLocations();
    const estMessages = estimateTotalMessages();
    const themeLabel = THEME_LABELS[scenario.theme] || scenario.theme || 'Slice of Life';

    const volumeCounts = { heavy: 0, regular: 0, light: 0, minimal: 0 };
    for (const dev of scenario.devices) {
        for (const c of dev.contacts) volumeCounts[c.message_volume || 'regular']++;
    }
    const volBreakdown = ['heavy','regular','light','minimal']
        .filter(v => volumeCounts[v] > 0)
        .map(v => `${volumeCounts[v]} ${v}`)
        .join(', ');

    const spamCounts = { none: 0, low: 0, medium: 0, high: 0 };
    for (const dev of scenario.devices) spamCounts[dev.spam_density || 'medium']++;
    const spamParts = ['high','medium','low','none']
        .filter(v => spamCounts[v] > 0)
        .map(v => `${spamCounts[v]} ${v}`);
    const spamSummary = spamParts.length > 0 ? spamParts.join(', ') : 'N/A';

    const hasStoryArc = scenario.story_arc ? 'Yes' : 'No';
    const charArcsSet = scenario.devices.reduce((sum, d) => {
        let cnt = d.owner_story_arc ? 1 : 0;
        cnt += d.contacts.filter(c => c.story_arc).length;
        return sum + cnt;
    }, 0);
    const charArcsTotal = totalDevices + totalContacts;

    document.getElementById('summary-grid').innerHTML = `
        <div class="summary-stat"><div class="value">${totalDevices}</div><div class="label">Devices</div></div>
        <div class="summary-stat"><div class="value">${totalContacts}</div><div class="label">Contacts</div></div>
        <div class="summary-stat"><div class="value">${sharedCount}</div><div class="label">Shared Contacts</div></div>
        <div class="summary-stat"><div class="value">${profilesDone}/${profilesTotal}</div><div class="label">Profiles Set</div></div>
        <div class="summary-stat"><div class="value">${totalLinks}</div><div class="label">Cross-Device Links</div></div>
        <div class="summary-stat"><div class="value">${totalEvents}</div><div class="label">Events</div></div>
        <div class="summary-stat"><div class="value">${eventLinks}</div><div class="label">Event Links</div></div>
        <div class="summary-stat"><div class="value">${locations.length}</div><div class="label">Locations</div></div>
        <div class="summary-stat"><div class="value">~${estMessages}</div><div class="label">Est. Messages</div></div>
        <div class="summary-stat"><div class="value">${themeLabel}</div><div class="label">Theme</div></div>
        <div class="summary-stat"><div class="value">${hasStoryArc}</div><div class="label">Story Arc</div></div>
        <div class="summary-stat"><div class="value">${charArcsSet}/${charArcsTotal}</div><div class="label">Char Arcs</div></div>
        <div class="summary-stat"><div class="value">${volBreakdown || 'N/A'}</div><div class="label">Volume Mix</div></div>
        <div class="summary-stat"><div class="value">${spamSummary}</div><div class="label">Spam Density</div></div>
        <div class="summary-stat"><div class="value">${LANGUAGE_LABELS[scenario.generation_settings?.language] || scenario.generation_settings?.language || 'English'}</div><div class="label">Language</div></div>
    `;

    const costDiv = document.getElementById('gen-cost-estimate');
    if (costDiv && estMessages > 0) {
        const batchSize = scenario.generation_settings.batch_size || 25;
        const estCalls = Math.ceil(estMessages / batchSize) * scenario.devices.length;
        document.getElementById('cost-estimate-text').textContent =
            `Estimated: ~${estCalls} API calls, ~${estMessages} total messages across all devices`;
        costDiv.classList.remove('hidden');
    }

    const dateStartEl = document.getElementById('gen-date-start');
    const dateEndEl = document.getElementById('gen-date-end');
    if (dateStartEl) dateStartEl.value = scenario.generation_settings.date_start;
    if (dateEndEl) dateEndEl.value = scenario.generation_settings.date_end;
    document.getElementById('gen-msg-min').value = scenario.generation_settings.messages_per_day_min;
    document.getElementById('gen-msg-max').value = scenario.generation_settings.messages_per_day_max;
    document.getElementById('gen-batch-size').value = scenario.generation_settings.batch_size;
    document.getElementById('gen-provider').value = scenario.generation_settings.llm_provider;
    document.getElementById('gen-temperature').value = scenario.generation_settings.temperature;

    checkGenerationProgress();
}

export function updateSettings() {
    scenario.generation_settings = {
        date_start: document.getElementById('gen-date-start').value,
        date_end: document.getElementById('gen-date-end').value,
        messages_per_day_min: parseInt(document.getElementById('gen-msg-min').value) || 2,
        messages_per_day_max: parseInt(document.getElementById('gen-msg-max').value) || 8,
        batch_size: parseInt(document.getElementById('gen-batch-size').value) || 25,
        llm_provider: document.getElementById('gen-provider').value,
        llm_model: '',
        temperature: parseFloat(document.getElementById('gen-temperature').value) || 0.9,
        language: document.getElementById('scenario-language').value || 'en'
    };
    syncScenario();
}

// -----------------------------------------------------------------------
// Progress check
// -----------------------------------------------------------------------

export async function checkGenerationProgress() {
    try {
        const res = await api('GET', '/api/generate/progress');
        const resumeBtn = document.getElementById('gen-resume-btn');
        const progressInfo = document.getElementById('gen-progress-info');
        if (!resumeBtn || !progressInfo) return res;

        if (res.has_partial || res.all_complete) {
            const parts = res.devices.map(d => {
                const status = d.complete ? 'done' : (d.has_output ? `${d.contacts_done}/${d.contacts_total}` : 'pending');
                return `${d.label}: ${status} (${d.total_messages} msgs)`;
            });
            progressInfo.innerHTML = `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">
                <strong>Existing output found:</strong><br>${parts.join('<br>')}
            </div>`;
            progressInfo.classList.remove('hidden');
            if (res.has_partial) {
                resumeBtn.classList.remove('hidden');
            } else {
                resumeBtn.classList.add('hidden');
            }
        } else {
            progressInfo.classList.add('hidden');
            resumeBtn.classList.add('hidden');
        }
        return res;
    } catch (e) { return null; }
}

// -----------------------------------------------------------------------
// Quality check
// -----------------------------------------------------------------------

export async function runQualityCheck() {
    await syncScenario();
    const fixBtn = document.getElementById('gen-quality-fix-btn');
    const pc = document.getElementById('progress-container');
    const pl = document.getElementById('progress-log');
    const runBtn = document.getElementById('gen-run-btn');
    const resumeBtn = document.getElementById('gen-resume-btn');

    pc.classList.remove('hidden');
    pl.innerHTML = '';

    function addLog(text, cls) {
        const line = document.createElement('div');
        line.className = 'log-line' + (cls ? ' ' + cls : '');
        line.textContent = text;
        pl.appendChild(line);
        pl.scrollTop = pl.scrollHeight;
    }

    if (fixBtn) fixBtn.disabled = true;
    if (runBtn) runBtn.disabled = true;
    if (resumeBtn) resumeBtn.disabled = true;

    if (fixBtn) fixBtn.innerHTML = '<span class="spinner"></span> Running AI quality fix...';

    try {
        const response = await fetch('/api/generate/quality-check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ auto_adjust: true }),
        });
        if (!response.ok) throw new Error('Quality check failed');
        const data = await response.json();
        const before = data.before?.summary || {};
        const after = data.after?.summary || {};
        const beforePct = Math.round((before.overall_score || 0) * 100);
        const afterPct = Math.round((after.overall_score || 0) * 100);
        const delta = afterPct - beforePct;

        addLog('AI quality fix complete.', 'success');
        addLog('Quality now: ' + afterPct + '% (' + (after.overall_severity || 'ok') + '), findings ' + (after.findings_total || 0), 'success');
        addLog('Change vs previous check: ' + (delta >= 0 ? '+' : '') + delta + '%');

        const adjustments = data.adjustments || [];
        if (adjustments.length > 0) {
            addLog('Fixes applied:', 'success');
            adjustments.forEach(a => addLog('  - ' + a, 'success'));
        } else {
            addLog('No automatic fixes were needed.', 'success');
        }

        if (data.quality_report_path) addLog('Quality report saved: ' + data.quality_report_path, 'success');
        if (data.readiness) {
            const r = data.readiness;
            addLog('Scenario readiness: personalities ' + r.personality_complete + ', arcs ' + r.arc_complete + ', events ' + r.events_with_participants);
        }
        if (data.resolution_writeup) {
            const rw = data.resolution_writeup;
            addLog('Issue resolution write-up:', 'success');
            addLog('  Problems before: ' + (rw.before_problem_count ?? 0) + ', after: ' + (rw.after_problem_count ?? 0) + ', resolved: ' + (rw.resolved_estimate ?? 0), 'success');
            const items = rw.items || [];
            items.slice(0, 15).forEach(item => {
                addLog('  Issue: ' + (item.issue || ''), 'success');
                addLog('    Action: ' + (item.action || ''), 'success');
                addLog('    Result: ' + (item.result || ''), 'success');
                if (item.repair_details?.length) {
                    item.repair_details.forEach(rd => {
                        const outcomeTag = (rd.outcome === 'repaired') ? 'success' : 'error';
                        addLog('      [' + (rd.device || '') + '] ' + (rd.thread || '') + ' \u2192 ' + rd.outcome +
                            (rd.messages_produced ? ' (' + rd.messages_produced + ' msgs)' : '') +
                            (rd.reason ? ' \u2014 ' + rd.reason : ''), outcomeTag);
                        (rd.issues || []).forEach(issue => addLog('        \u2023 ' + issue));
                    });
                }
            });
        }

        const topFindings = data.after?.top_findings || [];
        if (topFindings.length > 0) {
            addLog('Top findings:');
            topFindings.slice(0, 8).forEach(f => {
                const sev = (f.severity || 'warning').toUpperCase();
                const check = f.check_id ? (' (' + f.check_id + ')') : '';
                addLog(sev + check + ': ' + (f.message || ''), f.severity === 'critical' ? 'error' : '');
            });
        } else {
            addLog('No findings detected after quality check.', 'success');
        }
    } catch (e) {
        addLog('Quality check error: ' + e.message, 'error');
    } finally {
        if (fixBtn) fixBtn.disabled = false;
        if (runBtn) runBtn.disabled = false;
        if (resumeBtn) resumeBtn.disabled = false;
        if (fixBtn) fixBtn.innerHTML = 'AI Quality Fix';
    }
}

// -----------------------------------------------------------------------
// Generation (SSE stream)
// -----------------------------------------------------------------------

export async function runGeneration(resume = false, overrideChecks = false) {
    await syncScenario();
    const btn = document.getElementById('gen-run-btn');
    const resumeBtn = document.getElementById('gen-resume-btn');
    const pc = document.getElementById('progress-container');
    const pb = document.getElementById('progress-bar');
    const pl = document.getElementById('progress-log');
    btn.disabled = true;
    if (resumeBtn) resumeBtn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Generating...';
    pc.classList.remove('hidden');
    pl.innerHTML = '';
    pb.style.width = '0%';

    function addLog(text, cls) {
        const line = document.createElement('div');
        line.className = 'log-line' + (cls ? ' ' + cls : '');
        line.textContent = text;
        pl.appendChild(line);
        pl.scrollTop = pl.scrollHeight;
    }

    let url = '/api/generate/run';
    if (resume && overrideChecks) url = '/api/generate/run?resume=true&override_checks=true';
    else if (resume) url = '/api/generate/run?resume=true';

    try {
        const response = await fetch(url, { method: 'POST' });
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let totalWork = 0, doneWork = 0;
        scenario.devices.forEach(d => { totalWork += d.contacts.length; });

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const lines = decoder.decode(value, { stream: true }).split('\n');
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.substring(6));
                    if (data.type === 'device_start') {
                        const tag = data.resuming ? ' (resuming)' : '';
                        addLog('Starting ' + data.label + tag);
                    } else if (data.type === 'device_skipped') {
                        addLog('Skipped ' + data.label + ' \u2014 ' + data.reason, 'success');
                        const devContacts = scenario.devices.find(d => d.device_label === data.label)?.contacts.length || 0;
                        doneWork += devContacts;
                        pb.style.width = (totalWork > 0 ? Math.round((doneWork / totalWork) * 100) : 0) + '%';
                    } else if (data.type === 'contact_start') {
                        addLog('  Generating: ' + data.name);
                    } else if (data.type === 'contact_skipped') {
                        doneWork++;
                        pb.style.width = (totalWork > 0 ? Math.round((doneWork / totalWork) * 100) : 0) + '%';
                        addLog('  Skipped: ' + data.name + ' (already done)', 'success');
                    } else if (data.type === 'contact_done') {
                        doneWork++;
                        pb.style.width = (totalWork > 0 ? Math.round((doneWork / totalWork) * 100) : 0) + '%';
                        addLog('  Done: ' + data.messages + ' messages', 'success');
                    } else if (data.type === 'contact_error') {
                        addLog('  Error: ' + data.name + ' \u2014 ' + data.error, 'error');
                    } else if (data.type === 'quota_exhausted') {
                        addLog('API quota exhausted! Progress saved. Use Resume to continue.', 'error');
                    } else if (data.type === 'group_start') {
                        addLog('  Group chat: ' + data.group_name);
                    } else if (data.type === 'group_done') {
                        addLog('  Group done: ' + data.messages + ' messages', 'success');
                    } else if (data.type === 'pairwise_done') {
                        addLog('  Pairwise direct threads created: ' + data.threads + ' (' + data.group_name + ')', 'success');
                    } else if (data.type === 'spam_done') {
                        addLog('  Spam injected: ' + data.threads + ' threads', 'success');
                    } else if (data.type === 'quality_warning') {
                        const entity = data.entity_id ? (' [' + data.entity_id + ']') : '';
                        const check = data.check_id ? (' (' + data.check_id + ')') : '';
                        addLog('Quality ' + (data.severity || 'warning').toUpperCase() + check + entity + ': ' + data.message, 'error');
                        if (data.suggestion) addLog('  Suggestion: ' + data.suggestion);
                    } else if (data.type === 'device_done') {
                        const tag = data.partial ? ' (partial)' : '';
                        addLog('Saved: ' + data.path + tag, 'success');
                        await checkGenerationProgress();
                    } else if (data.type === 'device_event_validation') {
                        const status = data.passed ? 'PASSED' : 'FAILED';
                        addLog(
                            '  Event validation ' + status + ' (' + data.label + '): ' +
                            (data.issues_before || 0) + ' critical before \u2192 ' + (data.issues_after || 0) +
                            ' after, ' + (data.warnings_after || 0) + ' warnings, ' +
                            (data.repaired_threads || 0) + ' thread(s) repaired',
                            data.passed ? 'success' : 'error'
                        );
                        if (data.repair_details?.length) {
                            data.repair_details.forEach(rd => {
                                const outcomeTag = rd.outcome === 'regenerated' ? 'success' : 'error';
                                addLog('    Repair: ' + (rd.thread || '') + ' \u2192 ' + rd.outcome + (rd.reason ? ' (' + rd.reason + ')' : ''), outcomeTag);
                                (rd.issues || []).forEach(issue => addLog('      Issue: ' + issue));
                            });
                        }
                        if (!data.passed && data.remaining_findings?.length) {
                            addLog('    Unresolved issues:', 'error');
                            data.remaining_findings.forEach(f => {
                                addLog('      ' + (f.severity || '').toUpperCase() + ': ' + f.message, f.severity === 'critical' ? 'error' : '');
                                if (f.suggestion) addLog('        Fix: ' + f.suggestion);
                            });
                        }
                    } else if (data.type === 'resume_blocked') {
                        addLog('Resume pre-check blocked continuation.', 'error');
                        addLog(data.message, 'error');
                        if (data.quality_report_path) addLog('Proof report: ' + data.quality_report_path, 'success');
                        if (data.top_findings?.length) {
                            data.top_findings.forEach(f => {
                                const sev = (f.severity || 'warning').toUpperCase();
                                addLog(sev + ': ' + (f.message || ''), sev === 'CRITICAL' ? 'error' : '');
                            });
                        }
                        const go = confirm('Resume quality gate found mismatches. Continue anyway with override?');
                        if (go) {
                            addLog('Override approved. Resuming with override checks...', 'success');
                            await runGeneration(true, true);
                            return;
                        }
                    } else if (data.type === 'stopped') {
                        addLog(data.message, 'error');
                        if (data.blocking_findings?.length) {
                            addLog('Blocking findings that prevented completion:', 'error');
                            data.blocking_findings.forEach(f => {
                                const entity = f.entity_id ? ' [' + f.entity_id + ']' : '';
                                addLog('  CRITICAL' + entity + ': ' + f.message, 'error');
                                if (f.suggestion) addLog('    Suggested fix: ' + f.suggestion);
                            });
                        }
                        if (data.quality_summary) {
                            const s = data.quality_summary;
                            const scorePct = Math.round((s.overall_score || 0) * 100);
                            addLog('Quality: ' + scorePct + '% \u2014 ' + (s.critical_count || 0) + ' critical, ' + (s.warning_count || 0) + ' warnings');
                        }
                        addLog('Tip: Run "AI Quality Fix" to auto-repair timeline issues, then Resume.', 'success');
                        if (data.run_log_path) addLog('Run log: ' + data.run_log_path, 'success');
                        if (data.quality_report_path) addLog('Quality report: ' + data.quality_report_path, 'success');
                    } else if (data.type === 'complete') {
                        addLog('Generation complete!', 'success');
                        if (data.quality_summary) {
                            const s = data.quality_summary;
                            const scorePct = Math.round((s.overall_score || 0) * 100);
                            addLog('Quality summary: ' + scorePct + '% (' + (s.overall_severity || 'ok') + ')', 'success');
                            addLog('Findings: ' + (s.findings_total || 0) + ' total, ' + (s.critical_count || 0) + ' critical, ' + (s.warning_count || 0) + ' warning');
                        }
                        if (data.quality_report_path) {
                            addLog('Quality report saved: ' + data.quality_report_path, 'success');
                        }
                        if (data.run_log_path) {
                            addLog('Run log saved: ' + data.run_log_path, 'success');
                        }
                        pb.style.width = '100%';
                        await checkGenerationProgress();
                    } else if (data.type === 'error') {
                        addLog('Error: ' + data.message, 'error');
                    }
                } catch (e) { /* skip malformed SSE */ }
            }
        }
    } catch (e) { addLog('Connection error: ' + e.message, 'error'); }

    btn.disabled = false;
    btn.innerHTML = 'Generate Dataset';
    if (resumeBtn) resumeBtn.disabled = false;
    checkGenerationProgress();
}
