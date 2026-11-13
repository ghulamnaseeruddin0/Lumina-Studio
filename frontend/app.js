import { createClient } from '@supabase/supabase-js';

(() => {
'use strict';
const $ = (s, el = document) => el.querySelector(s);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
const LANGS = {en:'English',es:'Spanish',fr:'French',de:'German',pt:'Portuguese',hi:'Hindi',ur:'Urdu',ar:'Arabic',ru:'Russian',ja:'Japanese',zh:'Chinese',tr:'Turkish',id:'Indonesian',it:'Italian',ko:'Korean'};
const ORDER = ['director','brand','analytics','audience','calendar','trends','research','source_citation','script','story_structure','originality','hook','factcheck','compliance','shots','style','footage','picker','narrator','mastering','music','captions','accessibility','localization','editor','seo','thumbnail','thumbnail_experiment','license','rights','publish','qa','repurpose','security','quota','cost'];
const GROUPS = ['Plan','Write','Visuals','Audio','Build','Publish','Guard'];
const KIND = {ai:'AI', code:'Code', 'ai+code':'AI + Code'};

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || '';
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY || '';
const supabase = SUPABASE_URL && SUPABASE_ANON_KEY ? createClient(SUPABASE_URL, SUPABASE_ANON_KEY) : null;
let session = null;
let profile = null;
let appPreferences = {};
let adminOwner = false;
let adminCanChangeAccountStatus = false;
let authMode = 'signin';
let activeWorkspace = localStorage.getItem('lumina_workspace') || '';
let templates = [];
let API = import.meta.env.VITE_API_URL || localStorage.getItem('lumina_api') || '';
let TOKEN = localStorage.getItem('lumina_token') || '';
const localAccessToken = new URLSearchParams(location.hash.slice(1)).get('token');
const invitationToken = new URLSearchParams(location.search).get('invite') || new URLSearchParams(location.hash.slice(1)).get('invite');
const reviewToken = new URLSearchParams(location.search).get('review');
const reviewJob = new URLSearchParams(location.search).get('job');
if (localAccessToken) {
  TOKEN = localAccessToken;
  localStorage.setItem('lumina_token', TOKEN);
  history.replaceState(null, '', location.pathname + location.search);
}
const state = { format: 'short', job: null, agents: {}, meta: [], terminal: false, last: 0, abort: null, scenesCard: null };

function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 3600); }
function authNotice(message, error = false) {
  const el = $('#auth-notice');
  el.textContent = message;
  el.hidden = !message;
  el.classList.toggle('error', error);
}
function setAuthMode(mode) {
  authMode = mode;
  const signup = mode === 'signup';
  $('#auth-title').textContent = signup ? 'Make room for your next idea.' : 'Good to see you again.';
  $('#auth-description').textContent = signup ? 'Create your account and start building your studio.' : 'Sign in to pick up where your next story begins.';
  $('#auth-name-wrap').hidden = !signup;
  $('#auth-confirm-wrap').hidden = !signup;
  $('#auth-password').autocomplete = signup ? 'new-password' : 'current-password';
  $('#auth-submit').replaceChildren(signup ? 'Create account ' : 'Sign in ', h('span', { 'aria-hidden': 'true' }, '→'));
  $('#auth-forgot').hidden = signup;
  $('#auth-switch-copy').textContent = signup ? 'Already have an account?' : 'New to Lumina?';
  $('#auth-switch').textContent = signup ? 'Sign in' : 'Create an account';
  authNotice('');
}
function showAuth() {
  $('#auth-shell').hidden = false;
  $('#app-header').hidden = true;
  $('#app-shell').hidden = true;
  $('#local-auth').hidden = !!supabase;
  if (!supabase) authNotice('Connect Supabase to enable account sign-in. For local development, use your studio access token below.');
}
async function loadAccountData(user) {
  if (!supabase || !user) return;
  const [profileResult, settingsResult] = await Promise.all([
    supabase.from('profiles').select('*').eq('id', user.id).maybeSingle(),
    supabase.from('user_settings').select('preferences').eq('user_id', user.id).maybeSingle()
  ]);
  if (profileResult.error) throw profileResult.error;
  if (settingsResult.error) throw settingsResult.error;
  profile = profileResult.data || { id: user.id, email: user.email || '', full_name: user.user_metadata?.full_name || '' };
  appPreferences = settingsResult.data?.preferences || {};
}
async function showApp(user) {
  session = user ? session : null;
  const callbackUrl = new URL(location.href);
  const publishingResult = callbackUrl.searchParams.get('publishing');
  if (publishingResult) {
    const provider = callbackUrl.searchParams.get('provider') || 'platform';
    toast(publishingResult === 'connected'
      ? `${provider[0].toUpperCase() + provider.slice(1)} account connected.`
      : `Could not connect ${provider}. Check provider setup and try again.`);
    callbackUrl.searchParams.delete('publishing');
    callbackUrl.searchParams.delete('provider');
    history.replaceState(null, '', callbackUrl.pathname + callbackUrl.search);
  }
  $('#auth-shell').hidden = true;
  $('#app-header').hidden = false;
  $('#app-shell').hidden = false;
  if (user) {
    try { await loadAccountData(user); }
    catch (e) { toast('Could not load your profile settings: ' + e.message); }
    const name = profile?.full_name || user.user_metadata?.full_name || user.email || 'Account';
    $('#profile-name').textContent = name.split(/\s+/)[0];
    $('#profile-avatar').textContent = name.trim().slice(0, 1).toUpperCase() || 'L';
    acceptInvitationIfPresent(user);
  } else {
    profile = null;
    $('#profile-name').textContent = 'Local';
    $('#profile-avatar').textContent = 'L';
  }
  applyPreferences();
  loadAdminAccess();
  loadTemplates().catch(e => toast('Could not load templates: ' + e.message));
  refreshStatus().then(() => {
    const linkedJob = new URLSearchParams(location.search).get('job');
    const last = localStorage.getItem('lumina_job');
    if (linkedJob && (TOKEN || session?.access_token)) {
      api('GET', '/api/jobs/' + encodeURIComponent(linkedJob)).then(() => openJob(linkedJob)).catch(error => toast(error.message));
    } else if (last && (TOKEN || session?.access_token) && appPreferences.remember_last_job !== false) {
      api('GET', '/api/jobs/' + encodeURIComponent(last)).then(j => {
        if (['running', 'queued', 'awaiting_review'].includes(j.status)) openJob(last);
      }).catch(() => {});
    }
  });
}
function applyPreferences() {
  const prefs = appPreferences;
  document.documentElement.dataset.theme = prefs.theme || 'system';
  document.documentElement.classList.toggle('reduced-motion', !!prefs.reduced_motion);
  document.documentElement.classList.toggle('compact-feed', !!prefs.compact_feed);
  if ($('#lang') && prefs.default_language) $('#lang').value = prefs.default_language;
  if ($('#format') && prefs.default_format && state.format !== prefs.default_format) setFormat(prefs.default_format);
  if ($('#len') && state.format === 'short' && prefs.default_duration) $('#len').value = prefs.default_duration;
  if ($('#rv-script')) $('#rv-script').checked = !!prefs.review_script;
  if ($('#rv-scenes')) $('#rv-scenes').checked = !!prefs.review_scenes;
  if ($('#music')) $('#music').checked = prefs.background_music !== false;
  if ($('#repurpose')) $('#repurpose').checked = !!prefs.repurpose_shorts;
  if ($('#topic') && prefs.autosave_topic) $('#topic').value = localStorage.getItem('lumina_topic_draft') || '';
}
function startOAuth(provider) {
  if (!supabase) { authNotice('Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY to enable account sign-in.', true); return; }
  supabase.auth.signInWithOAuth({ provider, options: { redirectTo: window.location.origin + window.location.pathname } })
    .then(({ error }) => { if (error) authNotice(error.message, true); })
    .catch(error => authNotice(error.message || 'Could not start sign-in.', true));
}
async function submitAuth(event) {
  event.preventDefault();
  if (!supabase) { authNotice('Supabase is not configured. Use the local access-token option below.', true); return; }
  const email = $('#auth-email').value.trim();
  const password = $('#auth-password').value;
  const submit = $('#auth-submit');
  submit.disabled = true;
  try {
    if (!$('#auth-email').validity.valid) throw new Error('Enter a valid email address.');
    if (password.length < 8) throw new Error('Password must be at least 8 characters.');
    if (authMode === 'signup') {
      const fullName = $('#auth-name').value.trim();
      if (!fullName) throw new Error('Please enter your full name.');
      if (password !== $('#auth-confirm').value) throw new Error('The passwords do not match.');
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: { data: { full_name: fullName }, emailRedirectTo: window.location.origin + window.location.pathname }
      });
      if (error) throw error;
      if (data.session) {
        session = data.session;
        TOKEN = data.session.access_token;
        await showApp(data.user);
      } else {
        authNotice('Account created. Check your email to confirm your address, then sign in.');
      }
    } else {
      const { data, error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) throw error;
      session = data.session;
      TOKEN = data.session.access_token;
      await showApp(data.user);
    }
  } catch (e) { authNotice(e.message || 'Sign-in failed. Please try again.', true); }
  finally { submit.disabled = false; }
}
async function api(method, path, body) {
  let res;
  try {
    const token = session?.access_token || TOKEN;
    res = await fetch(API + path, { method, headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  } catch (e) { throw new Error('Cannot reach the studio server. Is it running? (' + (API || 'this page\'s server') + ')'); }
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty */ }
  if (res.status === 401) {
    if (supabase) { await supabase.auth.signOut(); session = null; showAuth(); }
    else openSettings(true);
    throw new Error('Your session expired. Please sign in again.');
  }
  if (!res.ok) throw new Error(data.error || ('Request failed (' + res.status + ')'));
  return data;
}
async function downloadAudit() {
  const res = await fetch(API + '/api/audit/export', {
    headers: { Authorization: 'Bearer ' + (session?.access_token || TOKEN) }
  });
  if (!res.ok) {
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-JSON error */ }
    throw new Error(data.error || `Audit export failed (${res.status})`);
  }
  const url = URL.createObjectURL(await res.blob());
  const link = h('a', { href: url, download: 'lumina-audit.csv' });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let amount = value / 1024, unit = units[0];
  for (let i = 1; amount >= 1024 && i < units.length; i++) { amount /= 1024; unit = units[i]; }
  return `${amount.toFixed(1)} ${unit}`;
}
async function fileUrl(jid, rel) {
  const { url } = await api('GET', `/api/file-link/${encodeURIComponent(jid)}/${rel.split('/').map(encodeURIComponent).join('/')}`);
  return API + url;
}
function protectedFile(element, jid, rel, attribute = 'src') {
  fileUrl(jid, rel).then(url => { element[attribute] = url; })
    .catch(e => { element.removeAttribute(attribute); toast('Could not load this file: ' + e.message); });
}
const copyText = async (t) => { try { await navigator.clipboard.writeText(t); toast('Copied'); } catch (e) { toast('Select the text and copy it manually'); } };

// ---------------------------------------------------------------- create form
function setFormat(f) {
  state.format = f;
  document.querySelectorAll('#format button').forEach(b => b.classList.toggle('on', b.dataset.v === f));
  const r = $('#len');
  if (f === 'short') { r.min = 30; r.max = 90; r.step = 15; r.value = appPreferences.default_duration || 60; }
  else { r.min = 3; r.max = 30; r.step = 1; r.value = 8; }
  document.querySelectorAll('.long-only').forEach(e => e.style.display = f === 'long' ? '' : 'none');
  lenLabel();
}
function lenLabel() { $('#len-label').textContent = state.format === 'short' ? `${$('#len').value} seconds` : `${$('#len').value} minutes`; }
function templateOptions() {
  const options = { format: state.format, language: $('#lang').value, review_script: $('#rv-script').checked,
    review_scenes: $('#rv-scenes').checked, music: $('#music').checked,
    translate: $('#translate').value.split(/[,\s]+/).filter(Boolean) };
  if (state.format === 'short') options.seconds = +$('#len').value;
  else { options.minutes = +$('#len').value; options.repurpose = $('#repurpose').checked ? 2 : 0; }
  return options;
}
async function loadTemplates() {
  const result = await api('GET', '/api/templates');
  templates = result.templates || [];
  const select = $('#template-select'), previous = select.value;
  select.replaceChildren(h('option', { value: '' }, 'Saved templates'),
    ...templates.map(template => h('option', { value: template.id }, template.name)));
  if (templates.some(template => template.id === previous)) select.value = previous;
}
function applyTemplate(template) {
  const p = template.params || {};
  setFormat(p.format || 'short');
  $('#len').value = p.format === 'long' ? p.minutes || 8 : p.seconds || 60;
  lenLabel();
  $('#lang').value = p.language || 'en';
  $('#rv-script').checked = !!p.review?.script;
  $('#rv-scenes').checked = !!p.review?.scenes;
  $('#music').checked = p.music !== false;
  $('#repurpose').checked = !!p.repurpose;
  $('#translate').value = (p.translate || []).join(', ');
}
async function createJob() {
  const topic = $('#topic').value.trim();
  if (topic.length < 3) { $('#create-hint').textContent = 'Please describe your topic (at least 3 characters).'; return; }
  const body = { topic, format: state.format, language: $('#lang').value, review_script: $('#rv-script').checked, review_scenes: $('#rv-scenes').checked,
    music: $('#music').checked, translate: $('#translate').value.split(/[,\s]+/).filter(Boolean) };
  if (activeWorkspace) body.workspace_id = activeWorkspace;
  if (state.format === 'short') body.seconds = +$('#len').value; else { body.minutes = +$('#len').value; body.repurpose = $('#repurpose').checked ? 2 : 0; }
  $('#btn-create').disabled = true;
  try { const r = await api('POST', '/api/jobs', body); openJob(r.id); }
  catch (e) { $('#create-hint').textContent = e.message; }
  finally { $('#btn-create').disabled = false; }
}

// ------------------------------------------------------------------ rail / live
function buildRail() {
  const rail = $('#rail'); rail.replaceChildren(); state.agents = {};
  const byId = Object.fromEntries(state.meta.map(m => [m.id, m]));
  for (const g of GROUPS) {
    const ids = ORDER.filter(id => byId[id] && byId[id].group === g);
    if (!ids.length) continue;
    rail.append(h('div', { class: 'grp' }, g));
    for (const id of ids) {
      const m = byId[id];
      const el = h('div', { class: 'ag idle', title: m.how }, h('span', { class: 'dot' }), h('span', { class: 't' }, m.title), h('span', { class: 's' }));
      rail.append(el); state.agents[id] = { el, m };
    }
  }
}
function mark(id, cls, note) {
  const a = state.agents[id]; if (!a) return;
  a.el.className = 'ag ' + cls; $('.s', a.el).textContent = note || '';
}
function resetLive() {
  state.terminal = false; state.last = 0; state.scenesCard = null;
  $('#feed').replaceChildren(); $('#log').textContent = ''; $('#result').hidden = true; $('#gate').hidden = true;
  $('#overall').style.width = '0'; $('#now-bar').style.width = '0'; $('#now-msg').textContent = 'Connecting…';
  $('#btn-cancel').hidden = false; buildRail();
  $('#btn-timeline').hidden = true;
}
function openJob(id) {
  if (state.abort) state.abort.abort();
  state.job = id; localStorage.setItem('lumina_job', id);
  $('#create').hidden = true; $('#live').hidden = false; resetLive();
  api('GET', '/api/jobs/' + id).then(j => { $('#job-title').textContent = j.topic; $('#job-sub').textContent = (j.format === 'short' ? 'YouTube Short' : 'Long video') + (j.parent ? ' (clip)' : ''); }).catch(() => {});
  stream(id);
  history.replaceState(null, '', location.pathname);
}
async function stream(id) {
  const ctl = new AbortController(); state.abort = ctl;
  while (!state.terminal && state.job === id && !ctl.signal.aborted) {
    try {
      const res = await fetch(`${API}/api/jobs/${id}/stream?after=${state.last}`, { headers: { Authorization: 'Bearer ' + (session?.access_token || TOKEN) }, signal: ctl.signal });
      if (!res.ok) throw new Error('stream ' + res.status);
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
      for (;;) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          if (block.startsWith('event: end')) { state.terminal = true; finalize(id); return; }
          const line = block.split('\n').find(l => l.startsWith('data:'));
          if (line) { try { onEvent(JSON.parse(line.slice(5))); } catch (e) { /* ignore bad block */ } }
        }
      }
    } catch (e) { if (ctl.signal.aborted) return; }
    await new Promise(r => setTimeout(r, 1800));
  }
}
function onEvent(ev) {
  state.last = Math.max(state.last, ev.id);
  const d = ev.data || {};
  switch (ev.type) {
    case 'agent_start': {
      if (ev.agent === 'director') { mark('director', 'active', 'orchestrating'); $('#now-name').textContent = 'Director'; break; }
      const a = state.agents[ev.agent]; if (!a) break;
      mark(ev.agent, 'active', d.phase || '');
      $('#now-num').textContent = '#' + a.m.number; $('#now-name').textContent = a.m.title; $('#now-kind').textContent = KIND[a.m.kind] || a.m.kind;
      $('#now-how').textContent = a.m.how; $('#now-bar').style.width = '0'; $('#now-msg').textContent = 'Starting…';
      if (d.index && d.total_steps) $('#overall').style.width = ((d.index - 1) / d.total_steps * 100) + '%';
      break; }
    case 'agent_done': mark(ev.agent, 'done', d.seconds != null ? d.seconds + 's' : ''); if (ev.agent !== 'director') $('#now-bar').style.width = '100%'; $('#now-msg').textContent = ev.message || 'Done'; break;
    case 'agent_warn': mark(ev.agent || d.agent, 'warn', 'skipped'); logLine('⚠ ' + ev.message); toast(ev.message); break;
    case 'agent_error': mark(ev.agent || d.agent, 'error', 'failed'); logLine('✖ ' + ev.message); break;
    case 'warn': logLine('⚠ ' + ev.message); $('#now-msg').textContent = '⚠ ' + ev.message; break;
    case 'log': logLine(ev.message); if (ev.agent !== 'director' || !$('#now-msg').textContent) $('#now-msg').textContent = ev.message; break;
    case 'progress': if (d.pct != null) $('#now-bar').style.width = d.pct + '%'; if (ev.message) $('#now-msg').textContent = ev.message; break;
    case 'artifact': renderArtifact(ev); break;
    case 'gate': showGate(ev); break;
    case 'job_done': mark('director', 'done', 'finished'); $('#overall').style.width = '100%'; $('#btn-cancel').hidden = true; $('#now-msg').textContent = ev.message; break;
    case 'job_failed': mark('director', 'error', 'stopped'); $('#btn-cancel').hidden = true; showFailure(ev.message); break;
    case 'job_cancelled': mark('director', 'warn', 'cancelled'); $('#btn-cancel').hidden = true; $('#now-msg').textContent = 'Cancelled.'; break;
  }
}
function logLine(t) { const l = $('#log'); l.textContent += t + '\n'; if (l.textContent.length > 20000) l.textContent = l.textContent.slice(-12000); l.scrollTop = l.scrollHeight; }
function showFailure(msg) {
  const el = $('#result'); el.hidden = false; el.replaceChildren(h('h3', {}, 'The video stopped'), h('p', {}, msg),
    h('p', { class: 'muted' }, 'Your progress is saved. Fix the cause (for example add a key in Settings), then retry: finished steps will not run again.'),
    h('button', { class: 'primary', onclick: async () => { try { await api('POST', `/api/jobs/${state.job}/retry`); openJob(state.job); } catch (e) { toast(e.message); } } }, 'Retry from where it stopped'));
}

// -------------------------------------------------------------------- artifacts
function card(title, ...body) { const el = h('div', { class: 'card art' }, h('h3', {}, title), ...body); $('#feed').prepend(el); return el; }
function renderArtifact(ev) {
  const k = ev.data.kind, p = ev.data.payload || {};
  if (k === 'scene') {
    if (!state.scenesCard) { state.scenesCard = card('Footage chosen', h('div', { class: 'scenes' })); }
    const g = $('.scenes', state.scenesCard);
    const img = h('img', { alt: '', loading: 'lazy' });
    protectedFile(img, state.job, p.thumb);
    g.append(h('div', { class: 'sc' }, img, h('div', {}, p.text, h('div', { class: 'src' }, `${p.source} ${p.kind}${p.author ? ' · ' + p.author : ''}`))));
    state.scenesCard.firstChild.textContent = `Footage chosen (${g.children.length})`; return;
  }
  if (k === 'script') card('Script: ' + ev.message, h('p', { class: 'muted' }, p.words + ' words'), ...p.sections.map(s => h('p', {}, h('b', {}, s.heading ? s.heading + ': ' : ''), s.narration)));
  else if (k === 'research') card(ev.message, h('ul', {}, ...p.facts.map(f => h('li', {}, f))), h('p', { class: 'muted' }, 'Sources: ' + (p.sources.map(s => s.title).join(', ') || 'general knowledge')));
  else if (k === 'trends') card('Keywords and angle', h('p', {}, h('b', {}, p.primary), ' — ', p.angle || ''), h('div', { class: 'tags' }, ...(p.secondary || []).map(t => h('span', {}, t))));
  else if (k === 'shots') card(ev.message, h('ul', {}, ...p.scenes.slice(0, 12).map(s => h('li', {}, `“${s.text}” → ${s.queries[0]}`))));
  else if (k === 'style') card(ev.message, h('p', {}, 'Accent ', h('span', { style: `display:inline-block;width:14px;height:14px;border-radius:4px;vertical-align:-2px;background:#${p.accent}` }), ' · transitions: ' + p.transitions.join(', ')));
  else if (k === 'factcheck') card(ev.message, p.issues.length ? h('ul', {}, ...p.issues.slice(0, 8).map(i => h('li', {}, `${i.claim} — ${i.verdict}`))) : h('p', { class: 'muted' }, 'No problems found.'));
  else if (k === 'compliance') card(ev.message, p.findings.length ? h('ul', {}, ...p.findings.map(f => h('li', {}, `${f.type} (${f.level}): ${f.text}`))) : h('p', { class: 'muted' }, 'No risky wording found.'), ...(p.ai_concerns || []).map(c => h('p', {}, '• ' + c.issue + (c.fix ? ' → ' + c.fix : ''))));
  else if (k === 'captions') card(ev.message, h('p', { class: 'muted' }, p.files.join(', ') + (p.burn_in ? ' · burned into the video' : '')));
  else if (k === 'seo') card('Titles: ' + ev.message, h('ul', {}, ...p.titles.map(t => h('li', {}, t.text))), h('div', { class: 'tags' }, ...p.tags.map(t => h('span', {}, t))));
  else if (k === 'thumbnails') {
    const imgs = p.files.map(f => { const img = h('img', { alt: 'thumbnail option' }); protectedFile(img, state.job, f); return img; });
    card(ev.message, h('div', { class: 'thumbs' }, ...imgs));
  }
  else if (k === 'licenses') card(ev.message, p.issues.length ? h('ul', {}, ...p.issues.map(i => h('li', {}, i))) : h('p', { class: 'muted' }, 'All assets have free-to-use licences.'));
  else if (k === 'quota') card(ev.message, h('p', { class: 'muted' }, `About ${p.estimate_scenes} scenes planned.`));
  else if (k === 'repurpose') card(ev.message, h('p', { class: 'muted' }, 'The Shorts appear in your Videos list as they render.'));
  else if (k === 'qa') { /* shown in the result panel */ }
}

// ------------------------------------------------------------------------ gate
function showGate(ev) {
  const g = $('#gate'); g.hidden = false; g.replaceChildren();
  const d = ev.data; $('#now-msg').textContent = 'Waiting for your review…';
  $('#btn-timeline').hidden = d.gate !== 'scenes';
  mark('director', 'warn', 'waiting for you');
  g.append(h('p', { class: 'muted' }, d.reason));
  const approve = async (edits) => {
    try { await api('POST', `/api/jobs/${state.job}/approve`, { gate: d.gate, edits }); g.hidden = true; mark('director', 'active', 'continuing'); toast('Approved. Continuing…'); }
    catch (e) { toast(e.message); }
  };
  if (d.gate === 'script') {
    g.append(h('h3', {}, 'Review the script'));
    const title = h('input', { value: d.script.title, maxlength: '100' }); g.append(title);
    const areas = d.script.sections.map((s, i) => {
      const head = h('input', { value: s.heading, placeholder: 'Section heading' });
      const ta = h('textarea', {}); ta.value = s.narration;
      const wc = h('span', { class: 'wc' }); const upd = () => wc.textContent = (ta.value.trim().split(/\s+/).filter(Boolean).length) + ' words'; ta.addEventListener('input', upd); upd();
      g.append(h('div', { class: 'secname' }, h('b', {}, 'Section ' + (i + 1)), wc), head, ta);
      return { head, ta };
    });
    g.append(h('button', { class: 'primary', onclick: () => approve({ script: { title: title.value, sections: areas.map(a => ({ heading: a.head.value, narration: a.ta.value })).filter(a => a.narration.trim()) } }) }, 'Approve and continue'));
  } else {
    g.append(h('h3', {}, 'Review the footage'), h('p', { class: 'muted' }, 'Swap any clip for an alternative, or remove a scene.'));
    g.append(h('button', { onclick: openTimelineEditor }, 'Edit scene order'));
    const swaps = {}, removed = new Set();
    const grid = h('div', { class: 'scenes' });
    for (const s of d.scenes) {
      const sel = h('select', {}, h('option', { value: '' }, 'Keep this clip'), ...s.alternates.map((a, i) => h('option', { value: i }, `Alt ${i + 1}: ${a.source} ${a.kind}${a.title ? ' · ' + a.title.slice(0, 28) : ''}`)));
      sel.addEventListener('change', () => { if (sel.value === '') delete swaps[s.id]; else swaps[s.id] = +sel.value; });
      const rm = h('label', { class: 'src' }, h('input', { type: 'checkbox', onchange: (e) => e.target.checked ? removed.add(s.id) : removed.delete(s.id) }), ' remove scene');
      const img = h('img', { alt: '' });
      protectedFile(img, state.job, s.thumb);
      grid.append(h('div', { class: 'sc' }, img, h('div', {}, s.text, h('div', { class: 'src' }, `${s.source} ${s.kind}`), sel, rm)));
    }
    g.append(grid, h('p', {}), h('button', { class: 'primary', onclick: () => approve({ swaps: Object.entries(swaps).map(([scene_id, alt]) => ({ scene_id, alt })), remove: [...removed] }) }, 'Approve and render'));
  }
  g.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ---------------------------------------------------------------------- result
async function finalize(id) {
  let j; try { j = await api('GET', '/api/jobs/' + id); } catch (e) { return; }
  if (j.status === 'failed') { showFailure(j.error || 'The job failed.'); return; }
  if (j.status === 'cancelled') return;
  const r = j.result || {}, pub = r.publish || {}, el = $('#result'); el.hidden = false; el.replaceChildren();
  const short = j.format === 'short';
  if (j.status === 'needs_attention') el.append(h('p', { class: 'warnbox' }, '⚠ Finished, but the quality check found problems. Review them below before uploading.'));
  const video = h('video', { controls: true, preload: 'metadata' });
  protectedFile(video, id, 'final.mp4');
  const side = h('div', {}, h('h3', {}, pub.title || j.topic));
  const field = (label, text, rows) => { const ta = h('textarea', { readonly: true, rows: rows || 3 }); ta.value = text; return h('div', {}, h('b', {}, label), h('div', { class: 'copy' }, ta, h('button', { onclick: () => copyText(text) }, 'Copy'))); };
  if (pub.title) {
    side.append(field('Title', pub.title, 1), field('Description', pub.description || '', 8), field('Tags', (pub.tags || []).join(', '), 3));
    if (pub.pinned_comment) side.append(field('Pinned comment', pub.pinned_comment, 2));
  }
  el.append(h('div', { class: 'vwrap' + (short ? ' short' : '') }, video, side));
  if ((r.seo || {}).titles) el.append(h('h3', {}, 'Other title options'), h('ul', {}, ...r.seo.titles.map(t => h('li', {}, t.text))));
  const thumbs = (r.deliverables || []).filter(f => f.startsWith('thumbnails/'));
  if (thumbs.length) {
    const links = thumbs.map(f => {
      const img = h('img', { alt: 'thumbnail' });
      protectedFile(img, id, f);
      return h('a', { href: '#', onclick: async e => { e.preventDefault(); try { window.location.href = await fileUrl(id, f); } catch (err) { toast(err.message); } } }, img);
    });
    el.append(h('h3', {}, 'Thumbnails'), h('div', { class: 'thumbs' }, ...links));
  }
  if (pub.checklist) el.append(h('h3', {}, 'Before you upload'), h('ul', {}, ...pub.checklist.map(c => h('li', {}, c))));
  if ((r.qa || {}).checks) el.append(h('h3', {}, 'Quality check: ' + r.qa.status), ...r.qa.checks.map(c => h('div', { class: 'q' }, h('b', { class: c.status }, c.status), h('span', {}, `${c.name}: ${c.detail}`))));
  const downloads = (r.deliverables || []).map(f => h('a', { href: '#', onclick: async e => { e.preventDefault(); try { window.location.href = await fileUrl(id, f); } catch (err) { toast(err.message); } } }, f));
  el.append(h('h3', {}, 'Download'), h('div', { class: 'files' }, ...downloads));
  const publishing = h('section', { class: 'card', id: 'publish-panel' });
  el.append(publishing);
  renderPublishingPanel(j).catch(error => {
    publishing.replaceChildren(h('h3', {}, 'Publish this video'), h('p', { class: 'warnbox' }, error.message));
  });
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function renderPublishingPanel(job) {
  const panel = $('#publish-panel');
  if (!panel || state.job !== job.id) return;
  const [capabilities, preferences, batchResult, notifications] = await Promise.all([
    api('GET', '/api/publishing/capabilities'),
    api('GET', '/api/publishing/preferences'),
    api('GET', `/api/jobs/${encodeURIComponent(job.id)}/publish`),
    api('GET', '/api/publishing/notifications')
  ]);
  if (!panel.isConnected) return;
  const connections = capabilities.connections || [];
  const accountChecks = connections.map(account => {
    const check = h('input', { type: 'checkbox', value: account.id });
    return h('label', { class: 'chk' }, check, `${account.provider[0].toUpperCase() + account.provider.slice(1)} · ${account.account_name}`);
  });
  const title = h('input', { maxlength: 100, value: job.result?.publish?.title || job.topic });
  const description = h('textarea', { maxlength: 5000, rows: 4 });
  description.value = job.result?.publish?.description || '';
  const privacy = h('select', {}, h('option', { value: 'public' }, 'Public'),
    h('option', { value: 'unlisted' }, 'Unlisted / followers'), h('option', { value: 'private' }, 'Private'));
  const email = h('input', { type: 'email', maxlength: 254, value: preferences.notification_email || '',
    placeholder: 'Use your sign-in email, or enter a different address' });
  const batches = batchResult.batches || [];
  const active = batches.some(batch => ['queued', 'uploading'].includes(batch.status));
  const recentNotifications = (notifications.notifications || []).filter(item =>
    item.job_id === job.id && ['ready_for_review', 'publish_results'].includes(item.kind)).slice(0, 3);
  const notificationRows = recentNotifications.map(item => h('div', { class: 'jobrow' },
    h('span', { class: 'st' }, `${item.kind === 'ready_for_review' ? 'Ready email' : 'Results email'}: ${item.status}`),
    item.status !== 'sent' ? h('button', { class: 'ghost', disabled: !capabilities.resend_configured, onclick: async () => {
      try { await api('POST', `/api/publishing/notifications/${encodeURIComponent(item.id)}/retry`, {});
        await renderPublishingPanel(job); } catch (error) { toast(error.message); }
    } }, 'Retry email') : null));
  const platformLabels = { youtube: 'YouTube', instagram: 'Instagram', facebook: 'Facebook', tiktok: 'TikTok' };
  const platformCards = capabilities.platforms.map(platform => {
    const connectProvider = platform.id === 'instagram' || platform.id === 'facebook' ? 'meta' : platform.id;
    const button = h('button', { class: 'ghost', disabled: !platform.configured, onclick: async () => {
      try {
        const result = await api('POST', `/api/publishing/connect/${connectProvider}`, {});
        location.assign(result.authorization_url);
      } catch (error) { toast(error.message); }
    } }, platform.id === 'facebook' || platform.id === 'instagram' ? 'Connect with Meta' : `Connect ${platformLabels[platform.id]}`);
    return h('div', { class: 'jobrow' },
      h('div', { style: 'flex:1' }, h('b', {}, platformLabels[platform.id]),
        h('div', { class: 'muted' }, platform.requirements?.[job.format] || 'Official platform account required')),
      button);
  });
  const connectionList = accountChecks.length ? accountChecks : [h('p', { class: 'muted' }, 'Connect a platform account to begin.')];
  const publishButton = h('button', { class: 'primary', disabled: !connections.length || active, onclick: async () => {
    const targets = [...panel.querySelectorAll('input[type="checkbox"]:checked')].map(input => input.value);
    if (!targets.length) return toast('Select at least one connected account.');
    try {
      const platforms = targets.map(id => connections.find(account => account.id === id)?.account_name || 'account');
      if (!window.confirm(`Publish "${title.value.trim()}" to ${platforms.join(', ')}? Lumina will not start until you confirm.`)) return;
      await api('PUT', '/api/publishing/preferences', { notification_email: email.value.trim() });
      await api('POST', `/api/jobs/${encodeURIComponent(job.id)}/publish`, {
        targets, metadata: { title: title.value, description: description.value, privacy: privacy.value }
      });
      toast('Publishing started. Results and email status will appear here.');
      await renderPublishingPanel(job);
    } catch (error) { toast(error.message); }
  } }, active ? 'Publishing…' : 'Review and publish');
  const batchCards = batches.map(batch => h('div', { class: 'publish-batch' },
    h('div', { class: 'row' }, h('b', {}, `Publish ${new Date(batch.created * 1000).toLocaleString()}`),
      h('span', { class: 'st' }, batch.status)),
    ...batch.targets.map(target => h('div', { class: 'jobrow' },
      h('div', { style: 'flex:1' }, `${platformLabels[target.provider] || target.provider}: ${target.status}`,
        target.error ? h('div', { class: 'muted' }, target.error) : null),
      target.remote_url ? h('a', { href: target.remote_url, target: '_blank', rel: 'noopener noreferrer' }, 'View post') : null)),
    batch.status === 'completed' && batch.targets.some(target => target.status === 'failed')
      ? h('button', { class: 'ghost', onclick: async () => {
        try { await api('POST', `/api/publishing/batches/${encodeURIComponent(batch.id)}/retry`, {});
          await renderPublishingPanel(job); } catch (error) { toast(error.message); }
      } }, 'Retry failed platforms only') : null));
  panel.replaceChildren(h('h3', {}, 'Publish this video'),
    h('p', { class: 'muted' }, 'Review the platform accounts and post details. Publishing starts only after you confirm.'),
    h('h4', {}, 'Connect accounts'), ...platformCards,
    h('h4', {}, 'Choose destinations'), ...connectionList,
    h('h4', {}, 'Review post details'),
    h('label', { class: 'field' }, 'Title', title),
    h('label', { class: 'field' }, 'Description / caption', description),
    h('label', { class: 'field' }, 'Visibility', privacy),
    h('label', { class: 'field' }, 'Results email', email),
    h('p', { class: 'hint' }, capabilities.resend_configured
      ? 'Resend is configured. Lumina emails when the review package is ready and after publishing results are available.'
      : 'Publishing emails are paused until RESEND_API_KEY and RESEND_FROM_EMAIL are configured on the server.'),
    h('button', { class: 'ghost', onclick: async () => {
      try { await api('PUT', '/api/publishing/preferences', { notification_email: email.value.trim() });
        toast('Publishing email preference saved.'); } catch (error) { toast(error.message); }
    } }, 'Save email preference'),
    publishButton,
    h('h4', {}, 'Publishing history'),
    ...(batchCards.length ? batchCards : [h('p', { class: 'muted' }, 'No publishing attempts yet.')]),
    h('h4', {}, 'Email delivery'),
    ...(notificationRows.length ? notificationRows : [h('p', { class: 'muted' }, 'No publishing email has been queued yet.')]));
  if (state.publishTimer) clearTimeout(state.publishTimer);
  if (active) state.publishTimer = setTimeout(() => renderPublishingPanel(job).catch(error => toast(error.message)), 4000);
}

// --------------------------------------------------------------------- dialogs
function dlg(id, build) { const d = $('#' + id); build($('.dlg', d)); if (!d.open) d.showModal(); d.addEventListener('click', e => { if (e.target === d) d.close(); }, { once: true }); }
async function openJobs() {
  let list = []; try { list = (await api('GET', '/api/jobs')).jobs; } catch (e) { toast(e.message); return; }
  dlg('dlg-jobs', body => { body.replaceChildren(h('h2', {}, 'Your videos'), ...(list.length ? list.map(j => h('div', { class: 'jobrow', onclick: () => { $('#dlg-jobs').close(); openJob(j.id); } },
    h('div', { style: 'flex:1' }, j.topic, h('div', { class: 'muted' }, new Date(j.created * 1000).toLocaleString() + ' · ' + j.format + (j.parent ? ' · clip' : ''))), h('span', { class: 'st' }, j.status)))
    : [h('p', { class: 'muted' }, 'Nothing yet. Create your first video!')])); });
}
async function openVersions() {
  if (!state.job) return toast('Open a video project first.');
  let versions;
  try { versions = (await api('GET', `/api/jobs/${encodeURIComponent(state.job)}/versions`)).versions; }
  catch (error) { return toast(error.message); }
  dlg('dlg-versions', body => {
    body.replaceChildren(h('h2', {}, 'Project versions'),
      ...(versions.length ? versions.map(version => h('div', { class: 'jobrow' },
        h('div', { style: 'flex:1' }, version.label, h('div', { class: 'muted' }, new Date(version.created * 1000).toLocaleString())),
        h('button', { onclick: async e => {
          e.stopPropagation();
          if (!window.confirm(`Restore "${version.label}"? This replaces the project's current saved state.`)) return;
          try {
            await api('POST', `/api/versions/${encodeURIComponent(version.id)}/restore`);
            $('#dlg-versions').close();
            toast('Version restored.');
            openJob(state.job);
          } catch (error) { toast(error.message); }
        } }, 'Restore')))
        : [h('p', { class: 'muted' }, 'No saved versions yet. Save a version after a job is complete.')]));
  });
}
async function openCalendar() {
  let items;
  try { items = (await api('GET', '/api/calendar')).items; }
  catch (error) { return toast(error.message); }
  dlg('dlg-calendar', body => {
    body.replaceChildren(h('h2', {}, 'Content calendar'),
      ...(items.length ? items.map(item => h('div', { class: 'jobrow' },
        h('div', { style: 'flex:1' }, item.title, h('div', { class: 'muted' },
          `${item.status}${item.scheduled ? ' · ' + new Date(item.scheduled * 1000).toLocaleString() : ''}`)),
        item.job_id ? h('button', { onclick: () => { $('#dlg-calendar').close(); openJob(item.job_id); } }, 'Open') : null))
        : [h('p', { class: 'muted' }, 'Scheduled videos and completed projects appear here.')]));
  });
}
async function openExperiments() {
  let experiments;
  try { experiments = (await api('GET', '/api/experiments')).experiments; }
  catch (error) { return toast(error.message); }
  dlg('dlg-experiments', body => {
    body.replaceChildren(h('h2', {}, 'Experiments'),
      ...(experiments.length ? experiments.map(exp => {
        let variants = [], metrics = {};
        try { variants = JSON.parse(exp.variants); } catch (error) { /* render an empty list for invalid legacy records */ }
        try { metrics = JSON.parse(exp.metrics || '{}'); } catch (error) { /* render empty metrics for invalid legacy records */ }
        const report = h('pre', {}, JSON.stringify({ variants, metrics }, null, 2));
        const views = h('input', { type: 'number', min: '0', step: '1', value: metrics.views ?? '', placeholder: 'Views' });
        const ctr = h('input', { type: 'number', min: '0', max: '100', step: '0.01', value: metrics.ctr ?? '', placeholder: 'CTR %' });
        return h('div', { class: 'card' }, h('b', {}, `${exp.kind} · ${exp.job_id}`),
          h('p', { class: 'muted' }, `Started ${new Date(exp.created * 1000).toLocaleString()}`),
          report, h('div', { class: 'row' }, views, ctr,
            h('button', { onclick: async () => {
              const next = {};
              if (views.value !== '') next.views = Number(views.value);
              if (ctr.value !== '') next.ctr = Number(ctr.value);
              try {
                const updated = await api('PUT', `/api/experiments/${encodeURIComponent(exp.id)}`, { metrics: next });
                report.textContent = JSON.stringify({ variants, metrics: updated.metrics }, null, 2);
                toast('Experiment results saved.');
              } catch (error) { toast(error.message); }
            } }, 'Save results')));
      }) : [h('p', { class: 'muted' }, 'Thumbnail tests are prepared when a video is produced. Track results here.')]));
  });
}
async function openWorkspaces() {
  let workspaces;
  try { workspaces = (await api('GET', '/api/workspaces')).workspaces; }
  catch (error) { return toast(error.message); }
  dlg('dlg-workspaces', body => {
    const list = h('div');
    const manage = async workspace => {
      let roster;
      try { roster = await api('GET', `/api/workspaces/${encodeURIComponent(workspace.id)}/members`); }
      catch (error) { toast(error.message); return; }
      const renderRoster = () => {
        const members = roster.members.map(member => h('div', { class: 'jobrow' },
          h('div', { style: 'flex:1' }, member.user_id, h('div', { class: 'muted' }, member.role)),
          roster.can_manage && member.role !== 'owner' ? h('button', { onclick: async () => {
            if (!window.confirm(`Remove ${member.user_id} from ${workspace.name}?`)) return;
            try {
              await api('DELETE', `/api/workspaces/${encodeURIComponent(workspace.id)}/members/${encodeURIComponent(member.user_id)}`);
              roster = await api('GET', `/api/workspaces/${encodeURIComponent(workspace.id)}/members`);
              renderRoster();
              toast('Workspace member removed.');
            } catch (error) { toast(error.message); }
          } }, 'Remove') : null));
        const invitations = roster.invitations.map(invite => h('div', { class: 'jobrow' },
          h('div', { style: 'flex:1' }, invite.email, h('div', { class: 'muted' }, `${invite.role} · expires ${new Date(invite.expires * 1000).toLocaleDateString()}`)),
          h('button', { onclick: async () => {
            try {
              await api('DELETE', `/api/workspace-invitations/${encodeURIComponent(invite.id)}`);
              roster = await api('GET', `/api/workspaces/${encodeURIComponent(workspace.id)}/members`);
              renderRoster();
              toast('Invitation revoked.');
            } catch (error) { toast(error.message); }
          } }, 'Revoke')));
        const body = $('#workspaces-body');
        body.replaceChildren(h('h2', {}, workspace.name), h('p', { class: 'muted' }, 'Workspace members'),
          ...members, ...(invitations.length ? [h('h3', {}, 'Pending invitations'), ...invitations] : []),
          h('button', { onclick: openWorkspaces }, 'Back to workspaces'));
      };
      renderRoster();
    };
    const render = () => {
      const rows = workspaces.map(workspace => {
        const actions = [h('button', { onclick: () => {
          activeWorkspace = workspace.id;
          localStorage.setItem('lumina_workspace', workspace.id);
          toast(`Using ${workspace.name}`);
          $('#dlg-workspaces').close();
        } }, workspace.id === activeWorkspace ? 'Selected' : 'Use workspace')];
        if (workspace.role === 'owner') actions.push(h('button', { onclick: async () => {
          const email = window.prompt('Invite by email');
          if (!email) return;
          const role = window.prompt('Role: editor or reviewer', 'editor');
          if (!role) return;
          try {
            const invitation = await api('POST', `/api/workspaces/${encodeURIComponent(workspace.id)}/invite`, { email, role });
            const inviteUrl = `${location.origin}${location.pathname}?invite=${encodeURIComponent(invitation.token)}`;
            await navigator.clipboard.writeText(inviteUrl);
            toast('Invitation created and copied. Ask the teammate to sign in, then send them the invite token/link.');
          } catch (error) { toast(error.message); }
        } }, 'Invite'));
        actions.push(h('button', { onclick: () => manage(workspace) }, 'Members'));
        return h('div', { class: 'jobrow' },
          h('div', { style: 'flex:1' }, workspace.name, h('div', { class: 'muted' }, workspace.role)), ...actions);
      });
      list.replaceChildren(...(rows.length ? rows : [h('p', { class: 'muted' }, 'No team workspaces yet. Your personal workspace remains available.')]));
    };
    const create = h('button', { class: 'primary', onclick: async () => {
      const name = window.prompt('Name your workspace');
      if (!name) return;
      try {
        const workspace = await api('POST', '/api/workspaces', { name });
        workspaces.push(workspace);
        render();
      } catch (error) { toast(error.message); }
    } }, 'Create workspace');
    body.replaceChildren(h('h2', {}, 'Workspaces'), h('p', { class: 'muted' }, 'Personal and shared team workspaces.'), list, create);
    render();
  });
}
async function openLibrary() {
  let media;
  try { media = (await api('GET', '/api/media')).media; }
  catch (error) { return toast(error.message); }
  dlg('dlg-library', body => {
    const upload = h('input', { type: 'file', accept: 'video/*,image/*,audio/*' });
    const license = h('input', { placeholder: 'License (optional)' });
    const attribution = h('input', { placeholder: 'Attribution / source (optional)' });
    const list = media.map(item => h('div', { class: 'jobrow' },
      h('div', { style: 'flex:1' }, item.filename, h('div', { class: 'muted' }, `${item.media_type}${item.license ? ' · ' + item.license : ''}`)),
      h('button', { onclick: async () => {
        try {
          const { url } = await api('GET', `/api/media-link/${encodeURIComponent(item.id)}`);
          window.open(API + url, '_blank', 'noopener');
        } catch (error) { toast(error.message); }
      } }, 'Download'),
      h('button', { onclick: async () => {
        if (!window.confirm(`Delete ${item.filename}?`)) return;
        try { await api('DELETE', `/api/media/${encodeURIComponent(item.id)}`); $('#dlg-library').close(); openLibrary(); }
        catch (error) { toast(error.message); }
      } }, 'Delete')));
    const save = h('button', { class: 'primary', onclick: async e => {
      const file = upload.files[0];
      if (!file) return toast('Choose a media file first.');
      const mediaType = file.type.startsWith('video/') ? 'video' : file.type.startsWith('image/') ? 'image' : file.type.startsWith('audio/') ? 'audio' : '';
      if (!mediaType) return toast('Choose a video, image, or audio file.');
      if (file.size > 5 * 1024 * 1024) return toast('Media upload limit is 5 MB.');
      e.currentTarget.disabled = true;
      try {
        const data = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = () => reject(new Error('Could not read the selected file.'));
          reader.readAsDataURL(file);
        });
        await api('POST', '/api/media', { filename: file.name, media_type: mediaType, data, license: license.value, attribution: attribution.value, workspace_id: activeWorkspace || null });
        toast('Media added to your library.');
        $('#dlg-library').close();
        openLibrary();
      } catch (error) { toast(error.message); }
      finally { e.currentTarget.disabled = false; }
    } }, 'Upload to library');
    body.replaceChildren(h('h2', {}, 'Media library'), h('p', { class: 'muted' }, 'Reusable media with license and attribution notes. Uploads are limited to 5 MB.'),
      h('div', { class: 'kv' }, h('label', {}, 'Choose file'), upload),
      h('div', { class: 'kv' }, h('label', {}, 'License'), license),
      h('div', { class: 'kv' }, h('label', {}, 'Attribution'), attribution),
      save, h('h3', {}, 'Your media'), ...(list.length ? list : [h('p', { class: 'muted' }, 'No uploaded media yet.')]));
  });
}
async function openTimelineEditor() {
  if (!state.job) return toast('Open a video project first.');
  let scenes;
  try { scenes = (await api('GET', `/api/jobs/${encodeURIComponent(state.job)}/timeline`)).scenes; }
  catch (error) { return toast(error.message); }
  if (!scenes.length) return toast('Scene order is available during the footage review step.');
  dlg('dlg-timeline', body => {
    const render = () => {
      const rows = scenes.map((scene, index) => {
        const voice = h('input', { value: scene.voice_override || '', maxlength: 60, placeholder: 'Default voice' });
        const rate = h('input', { type: 'number', min: '-20', max: '20', step: '1', value: scene.rate_override || 0 });
        voice.addEventListener('input', () => { scene.voice_override = voice.value; });
        rate.addEventListener('input', () => { scene.rate_override = Number(rate.value); });
        return h('div', { class: 'jobrow' },
          h('div', { style: 'flex:1' }, scene.heading || `Scene ${index + 1}`, h('div', { class: 'muted' }, scene.text),
            h('div', { class: 'row' },
              h('label', { class: 'inline' }, 'Voice ID', voice),
              h('label', { class: 'inline' }, 'Pace % (-20 to +20)', rate))),
          h('button', { disabled: index === 0, onclick: () => { [scenes[index - 1], scenes[index]] = [scenes[index], scenes[index - 1]]; render(); } }, '↑'),
          h('button', { disabled: index === scenes.length - 1, onclick: () => { [scenes[index + 1], scenes[index]] = [scenes[index], scenes[index + 1]]; render(); } }, '↓'));
      });
      body.replaceChildren(h('h2', {}, 'Scene order and narration'), h('p', { class: 'muted' }, 'Reorder scenes and optionally set a voice ID or pace for each scene before narration is recorded. Leave voice empty and pace at 0 to use defaults.'),
        ...rows, h('button', { class: 'primary', onclick: async () => {
          try {
            await api('PUT', `/api/jobs/${encodeURIComponent(state.job)}/timeline`, {
              timeline: { scenes: scenes.map(scene => ({ id: scene.id, voice: scene.voice_override || '', rate: scene.rate_override || 0 })) }
            });
            $('#dlg-timeline').close();
            toast('Scene order and narration settings saved.');
          } catch (error) { toast(error.message); }
        } }, 'Save scene order'));
    };
    render();
  });
}
async function acceptInvitationIfPresent(user) {
  if (!invitationToken || !user || !supabase) return;
  try {
    const workspaces = await api('POST', '/api/workspace-invitations/accept', { token: invitationToken });
    if (workspaces.workspace_id) {
      activeWorkspace = workspaces.workspace_id;
      localStorage.setItem('lumina_workspace', activeWorkspace);
      const url = new URL(location.href);
      url.searchParams.delete('invite');
      url.hash = '';
      history.replaceState(null, '', url.pathname + url.search);
      toast('You joined the team workspace.');
    }
    async function initPublicReview() {
      $('#auth-shell').hidden = true;
      $('#app-header').hidden = true;
      $('#app-shell').hidden = true;
      $('#review-shell').hidden = false;
      $('#review-comment-form').addEventListener('submit', async event => {
        event.preventDefault();
        const body = $('#review-comment').value.trim();
        if (!body) return;
        try {
          await fetch(`${API}/api/review/${encodeURIComponent(reviewJob)}/comments?token=${encodeURIComponent(reviewToken)}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body })
          }).then(async response => {
            if (!response.ok) throw new Error((await response.json()).error || 'Could not save your comment.');
          });
          $('#review-comment').value = '';
          await loadPublicReview();
        } catch (error) { $('#review-status').textContent = error.message; }
      });
      await loadPublicReview();
    }
    async function loadPublicReview() {
      const status = $('#review-status');
      const content = $('#review-content');
      const comments = $('#review-comments');
      try {
        const response = await fetch(`${API}/api/review/${encodeURIComponent(reviewJob)}?token=${encodeURIComponent(reviewToken)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Review link is unavailable.');
        $('#review-title').textContent = data.job.topic;
        status.textContent = `${data.job.format === 'short' ? 'Short video' : 'Long video'} · ${data.job.status}`;
        const videoFile = data.files.find(file => file === 'final.mp4');
        content.replaceChildren(...(videoFile ? [h('video', { controls: true, preload: 'metadata', src: `${API}/api/review/${encodeURIComponent(reviewJob)}/file/final.mp4?token=${encodeURIComponent(reviewToken)}` })] : []),
          h('p', { class: 'muted' }, videoFile ? 'Review the video above and leave feedback in your comment.' : 'The video is not ready yet.'));
        comments.replaceChildren(h('h3', {}, 'Comments'), ...(data.comments || []).map(comment =>
          h('div', { class: 'jobrow' }, h('div', {}, comment.body, h('div', { class: 'muted' }, new Date(comment.created * 1000).toLocaleString())))));
      } catch (error) {
        status.textContent = error.message;
        $('#review-comment-form').hidden = true;
        content.replaceChildren();
        comments.replaceChildren();
      }
    }
  } catch (error) { toast('Could not accept invitation: ' + error.message); }
}
function openIdeas() {
  dlg('dlg-ideas', body => {
    const niche = h('input', { placeholder: 'Your niche, e.g. space, personal finance, cooking' }); const out = h('div');
    const go = async () => { out.replaceChildren(h('p', { class: 'muted' }, 'Thinking…')); try { const r = await api('POST', '/api/ideas', { niche: niche.value });
      out.replaceChildren(...r.ideas.map(i => h('div', { class: 'jobrow', onclick: () => { $('#topic').value = i.topic; $('#dlg-ideas').close(); } }, h('div', {}, h('b', {}, i.topic), h('div', { class: 'muted' }, i.why || ''))))); }
      catch (e) { out.replaceChildren(h('p', {}, e.message)); } };
    body.replaceChildren(h('h2', {}, 'Topic ideas'), h('div', { class: 'row' }, niche, h('button', { class: 'primary', onclick: go }, 'Suggest')), out);
  });
}
async function openProfileSettings() {
  if (!supabase || !session?.user) return toast('Profile settings are available after signing in.');
  try { await loadAccountData(session.user); }
  catch (e) { toast('Could not load profile settings: ' + e.message); return; }
  dlg('dlg-profile', body => {
    const fields = {};
    const field = (key, label, value, attrs = {}) => {
      fields[key] = h('input', { value: value || '', ...attrs });
      body.append(h('div', { class: 'kv' }, h('label', {}, label), fields[key]));
    };
    body.replaceChildren(h('h2', {}, 'Profile settings'));
    field('full_name', 'Full name', profile.full_name, { maxlength: 100, autocomplete: 'name' });
    field('email', 'Email address', session.user.email, { type: 'email', disabled: true });
    field('handle', 'Profile handle', profile.handle, { maxlength: 32, placeholder: 'yourname' });
    field('bio', 'About you', profile.bio, { maxlength: 500, placeholder: 'Tell us about your channel' });
    field('website', 'Website', profile.website, { type: 'url', maxlength: 2048, placeholder: 'https://' });
    field('avatar_url', 'Avatar image URL', profile.avatar_url, { type: 'url', maxlength: 2048, placeholder: 'https://' });
    field('channel_name', 'Channel name', profile.channel_name, { maxlength: 100 });
    field('niche', 'Channel niche', profile.niche, { maxlength: 200 });
    field('audience', 'Target audience', profile.audience, { maxlength: 200 });
    field('tone', 'Narration tone', profile.tone, { maxlength: 200 });
    field('accent', 'Brand accent (hex)', profile.accent, { pattern: '#?[0-9a-fA-F]{6}' });
    field('cta', 'Default call to action', profile.cta, { maxlength: 300 });
    const locale = h('select', {}, ...Object.entries(LANGS).map(([code, name]) => h('option', { value: code, selected: (profile.locale || 'en') === code }, name)));
    fields.locale = locale;
    body.append(h('div', { class: 'kv' }, h('label', {}, 'Preferred language'), locale));
    field('timezone', 'Time zone', profile.timezone, { maxlength: 80, placeholder: 'UTC' });
    const notifications = h('input', { type: 'checkbox', checked: profile.email_notifications !== false });
    fields.email_notifications = notifications;
    body.append(h('div', { class: 'kv' }, h('label', {}, 'Email notifications'), notifications));
    body.append(h('div', { class: 'row end' },
      h('button', { onclick: () => $('#dlg-profile').close() }, 'Cancel'),
      h('button', { class: 'primary', onclick: async e => {
        e.currentTarget.disabled = true;
        const values = Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.type === 'checkbox' ? input.checked : input.value.trim()]));
        try {
          const { error } = await supabase.from('profiles').upsert({ id: session.user.id, email: session.user.email || '', ...values }, { onConflict: 'id' });
          if (error) throw error;
          profile = { ...profile, ...values };
          const name = profile.full_name || session.user.email || 'Account';
          $('#profile-name').textContent = name.split(/\s+/)[0];
          $('#profile-avatar').textContent = name.trim().slice(0, 1).toUpperCase() || 'L';
          toast('Profile saved.');
          $('#dlg-profile').close();
        } catch (error) { toast('Could not save your profile: ' + error.message); }
        finally { e.currentTarget.disabled = false; }
      } }, 'Save profile')));
  });
}
async function openSettings(needToken) {
  let S = {}, B = {}, A = {}, Storage = {}, PublishingPrefs = {};
  if (!needToken) {
    try {
      [S, B, A, Storage, PublishingPrefs] = await Promise.all([
        api('GET', '/api/settings'), api('GET', '/api/brand'), api('GET', '/api/analytics'),
        api('GET', '/api/storage'), api('GET', '/api/publishing/preferences')
      ]);
    } catch (e) { toast(e.message); }
  }
  dlg('dlg-settings', body => {
    const keyRows = [['GEMINI_API_KEY', 'Gemini (AI)', 'https://aistudio.google.com/apikey'], ['OPENROUTER_API_KEY', 'OpenRouter (AI)', 'https://openrouter.ai/keys'],
      ['GROQ_API_KEY', 'Groq (AI, optional)', 'https://console.groq.com/keys'], ['CEREBRAS_API_KEY', 'Cerebras (AI, optional)', 'https://cloud.cerebras.ai'],
      ['PEXELS_API_KEY', 'Pexels (footage)', 'https://www.pexels.com/api/'], ['PIXABAY_API_KEY', 'Pixabay (footage)', 'https://pixabay.com/api/docs/']];
    const keyInputs = {};
    const server = h('input', { value: API, placeholder: 'Leave empty if this page is served by your studio' });
    const token = h('input', { type: 'password', value: TOKEN, placeholder: 'Access token from the terminal' });
    const sec = (t) => h('h3', {}, t);
    const kv = (label, input, extra) => h('div', { class: 'kv' }, h('label', {}, label), input, extra || h('span'));
    const nodes = [h('h2', {}, 'Settings'), sec('Connection'), kv('Studio server URL', server), kv('Access token', token),
      h('div', { class: 'row' }, h('button', { onclick: () => { API = server.value.trim().replace(/\/$/, ''); TOKEN = token.value.trim(); localStorage.setItem('lumina_api', API); localStorage.setItem('lumina_token', TOKEN); toast('Saved. Reloading…'); setTimeout(() => location.reload(), 500); } }, 'Save connection'),
        h('button', { onclick: async () => { try { const r = await api('POST', '/api/token/rotate'); TOKEN = r.token; localStorage.setItem('lumina_token', TOKEN); token.value = TOKEN; toast('New token created. Old links stop working.'); } catch (e) { toast(e.message); } } }, 'Create a new token'))];
    if (!needToken) {
      const notificationEmail = h('input', { type: 'email', maxlength: 254,
        value: PublishingPrefs.notification_email || '',
        placeholder: 'Use your sign-in email, or enter a different address' });
      nodes.push(sec('Publishing notifications'),
        kv('Email for ready and publishing results', notificationEmail),
        h('p', { class: 'hint' }, PublishingPrefs.resend_configured
          ? 'Resend is configured. Lumina sends a package-ready notice and a final per-platform publishing summary.'
          : 'Email delivery is paused until RESEND_API_KEY and RESEND_FROM_EMAIL are configured on the Railway server.'),
        h('div', { class: 'row' }, h('button', { onclick: async e => {
          e.currentTarget.disabled = true;
          try {
            await api('PUT', '/api/publishing/preferences', { notification_email: notificationEmail.value.trim() });
            toast('Publishing email preference saved.');
          } catch (error) { toast(error.message); }
          finally { e.currentTarget.disabled = false; }
        } }, 'Save publishing email')));
      for (const [name, label, link] of keyRows) { const i = h('input', { type: 'password', placeholder: S.keys && S.keys[name] ? '•••• saved (type to replace)' : 'paste key' }); keyInputs[name] = i; nodes.push(kv(label, i, h('a', { href: link, target: '_blank', rel: 'noopener' }, 'get free key'))); }
      nodes.push(h('div', { class: 'row' }, h('button', { class: 'primary', onclick: async () => { const keys = {}; for (const [k, i] of Object.entries(keyInputs)) if (i.value.trim()) keys[k] = i.value.trim(); if (!Object.keys(keys).length) return toast('Nothing to save'); try { await api('PUT', '/api/keys', { keys }); toast('Keys saved securely on the server'); $('#dlg-settings').close(); refreshStatus(); } catch (e) { toast(e.message); } } }, 'Save keys')),
        h('p', { class: 'hint' }, 'Keys are stored only on your studio server and are never shown again.'));
      const q = h('select', {}, ...['draft', 'standard', 'high'].map(v => h('option', { value: v, selected: (S.render || {}).quality === v }, v)));
      const voice = h('input', { value: (S.voice || {}).voice || '', placeholder: 'Auto (e.g. en-US-GuyNeural)' });
      const cap = h('input', { type: 'checkbox', checked: !!(S.render || {}).burn_captions_long });
      nodes.push(sec('Output'), kv('Quality', q), kv('Voice (optional)', voice), kv('Burn captions into long videos', cap),
        h('div', { class: 'row' }, h('button', { onclick: async () => { try { await api('PUT', '/api/settings', { 'render.quality': q.value, 'voice.voice': voice.value, 'render.burn_captions_long': cap.checked }); toast('Saved'); } catch (e) { toast(e.message); } } }, 'Save output settings')),
        h('p', { class: 'hint' }, `Voice engines: ${(S.voice_engines || []).join(', ')} · Music files: ${S.music_files} · Logo: ${S.logo ? 'yes' : 'none (add assets/brand/logo.png)'}`));
      const f = {}; const bf = (k, label, v, ph) => { f[k] = h('input', { value: v || '', placeholder: ph || '' }); nodes.push(kv(label, f[k])); };
      nodes.push(sec('Your channel (brand memory)')); bf('channel_name', 'Channel name', B.channel_name); bf('niche', 'Niche', B.niche); bf('tone', 'Narration tone', B.tone); bf('audience', 'Audience', B.audience);
      bf('accent', 'Accent colour (hex)', B.accent, 'FFD23F'); bf('cta', 'Call to action', B.cta); bf('banned', 'Phrases to avoid (comma separated)', (B.banned_phrases || []).join(', '));
      nodes.push(h('div', { class: 'row' }, h('button', { onclick: async () => { try { await api('PUT', '/api/brand', { channel_name: f.channel_name.value, niche: f.niche.value, tone: f.tone.value, audience: f.audience.value, accent: f.accent.value, cta: f.cta.value, banned_phrases: f.banned.value.split(',').map(s => s.trim()).filter(Boolean) }); toast('Channel saved'); } catch (e) { toast(e.message); } } }, 'Save channel')));
      const file = h('input', { type: 'file', accept: '.csv' });
      const analyticsSummary = h('p', { class: 'hint' },
        `${A.videos || 0} videos · ${Number(A.views || 0).toLocaleString()} views · ${Number(A.average_ctr || 0).toFixed(2)}% average CTR · ${Number(A.average_view_seconds || 0).toFixed(1)}s average view`);
      nodes.push(sec('Analytics'), analyticsSummary,
        h('p', { class: 'hint' }, 'Upload a CSV exported from YouTube Studio (Analytics → Content → Export). The studio learns which titles and lengths work for you.'),
        h('div', { class: 'row' }, file, h('button', { onclick: async () => {
          const t = file.files[0]; if (!t) return toast('Choose a CSV file');
          try {
            const r = await api('POST', '/api/analytics', { csv: await t.text() });
            A = await api('GET', '/api/analytics');
            analyticsSummary.textContent = `${A.videos} videos · ${Number(A.views).toLocaleString()} views · ${Number(A.average_ctr).toFixed(2)}% average CTR · ${Number(A.average_view_seconds).toFixed(1)}s average view`;
            toast(r.imported + ' videos imported');
          } catch (e) { toast(e.message); }
        } }, 'Import')));
      const quota = Object.entries(S.quota || {}).map(([p, v]) => `${p}: ` + Object.entries(v).map(([w, x]) => `${x.used}/${x.limit} ${w.replace('per_', '')}`).join(', ')).join(' · ');
      const usage = (S.llm_usage || []).map(u => `${u.provider} ${u.count} requests`).join(', ') || 'none yet today';
      nodes.push(sec('Storage'), h('p', { class: 'hint' },
        `${Storage.jobs || 0} projects · ${formatBytes(Storage.job_bytes)} project files · ${Storage.media_items || 0} media items · ${formatBytes(Storage.media_bytes)} library files · ${formatBytes(Storage.free_bytes)} free on the server`),
        sec('Usage today'), h('p', { class: 'hint' }, 'Stock: ' + (quota || '—')), h('p', { class: 'hint' }, 'AI: ' + usage),
        h('div', { class: 'row' },
          h('button', { onclick: async () => { try { const r = await api('POST', '/api/backup'); toast('Backup saved: ' + r.file); } catch (e) { toast(e.message); } } }, 'Back up my data now'),
          h('button', { onclick: async () => { try { await downloadAudit(); toast('Audit log downloaded.'); } catch (e) { toast(e.message); } } }, 'Export audit log')));
      if (supabase && session?.user) {
        const preferenceControls = {};
        const addPreference = (key, label, type, choices, defaultValue = false) => {
          const value = appPreferences[key];
          const input = type === 'select'
            ? h('select', {}, ...choices.map(([v, text]) => h('option', { value: v, selected: (value ?? defaultValue ?? choices[0][0]) === v }, text)))
            : h('input', { type: 'checkbox', checked: value ?? defaultValue });
          preferenceControls[key] = input;
          nodes.push(kv(label, input));
        };
        nodes.push(sec('Studio preferences'));
        addPreference('theme', 'Appearance', 'select', [['system', 'Use device setting'], ['dark', 'Dark'], ['light', 'Light']]);
        addPreference('default_format', 'Default video format', 'select', [['short', 'Short'], ['long', 'Long']]);
        addPreference('default_language', 'Default narration language', 'select', Object.entries(LANGS).map(([code, name]) => [code, name]));
        addPreference('default_duration', 'Default duration', 'select', [['30', '30 seconds'], ['45', '45 seconds'], ['60', '60 seconds'], ['75', '75 seconds'], ['90', '90 seconds']]);
        addPreference('review_script', 'Review script before production', 'checkbox');
        addPreference('review_scenes', 'Review footage before rendering', 'checkbox');
        addPreference('background_music', 'Include background music by default', 'checkbox', null, true);
        addPreference('repurpose_shorts', 'Create Shorts from long videos', 'checkbox');
        addPreference('autosave_topic', 'Remember the topic draft on this device', 'checkbox');
        addPreference('reduced_motion', 'Reduce interface motion', 'checkbox');
        addPreference('compact_feed', 'Use a compact activity feed', 'checkbox');
        addPreference('email_job_updates', 'Email me about completed videos', 'checkbox', null, true);
        addPreference('email_product_updates', 'Email me product updates', 'checkbox');
        addPreference('confirm_cancel', 'Confirm before cancelling a video', 'checkbox');
        addPreference('remember_last_job', 'Reopen the last active video', 'checkbox', null, true);
        nodes.push(h('div', { class: 'row' }, h('button', { onclick: async e => {
          e.currentTarget.disabled = true;
          const preferences = { ...appPreferences };
          for (const [key, input] of Object.entries(preferenceControls)) preferences[key] = input.type === 'checkbox' ? input.checked : input.value;
          try {
            const { error } = await supabase.from('user_settings').upsert({ user_id: session.user.id, preferences }, { onConflict: 'user_id' });
            if (error) throw error;
            appPreferences = preferences;
            applyPreferences();
            toast('Studio preferences saved.');
          } catch (error) { toast('Could not save preferences: ' + error.message); }
          finally { e.currentTarget.disabled = false; }
        } }, 'Save preferences')));
      }
    }
    body.replaceChildren(...nodes);
  });
}
async function loadAdminAccess() {
  const button = $('#btn-admin');
  button.hidden = true;
  adminOwner = false;
  adminCanChangeAccountStatus = false;
  if (!session?.access_token) return;
  try {
    const result = await api('GET', '/api/admin/capabilities');
    adminOwner = result.owner === true;
    adminCanChangeAccountStatus = result.capabilities.some(item =>
      item.id === 'account_activation' && item.permitted);
    button.hidden = !result.capabilities.some(item => item.permitted);
  } catch (error) {
    toast('Could not verify administrator access: ' + error.message);
  }
}
async function openAdminControl() {
  dlg('dlg-admin', body => {
    const tabs = ['Overview', 'Users', 'Agents', 'Controls', 'Settings', 'Operations', 'Audit', 'Capabilities'];
    const nav = h('nav', { class: 'admin-tabs', 'aria-label': 'Administrator sections' });
    const panel = h('section', { class: 'admin-panel', 'aria-live': 'polite' });
    const close = h('button', { class: 'ghost', onclick: () => $('#dlg-admin').close() }, 'Close');
    body.replaceChildren(h('div', { class: 'admin-heading' }, h('div', {}, h('span', { class: 'eyebrow' }, 'LUMINA STUDIO · OWNER CONTROL'),
      h('h2', {}, 'Admin Control Center')), close), nav, panel);
    const activate = async name => {
      nav.querySelectorAll('button').forEach(button => button.classList.toggle('on', button.textContent === name));
      panel.replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
      try { await renderAdminSection(name, panel); }
      catch (error) { panel.replaceChildren(h('p', { class: 'admin-error' }, error.message)); }
    };
    for (const name of tabs) nav.append(h('button', { class: 'ghost', onclick: () => activate(name) }, name));
    activate('Overview');
  });
}
async function renderAdminSection(name, panel) {
  const table = (headers, rows) => h('div', { class: 'admin-table-wrap' },
    h('table', { class: 'admin-table' }, h('thead', {}, h('tr', {}, ...headers.map(label => h('th', {}, label)))),
      h('tbody', {}, ...rows.map(row => h('tr', {}, ...row.map(value => h('td', {}, value)))))));
  const card = (label, value, note = '') => h('article', { class: 'admin-metric' },
    h('span', { class: 'muted' }, label), h('strong', {}, value == null ? '—' : String(value)),
    note ? h('small', {}, note) : null);
  const toggle = (label, checked, onChange, disabled = false) => {
    const input = h('input', { type: 'checkbox', checked, disabled, onchange: onChange });
    return h('label', { class: 'admin-toggle' }, input, h('span', {}, label));
  };
  if (name === 'Overview') {
    const d = await api('GET', '/api/admin/overview');
    panel.replaceChildren(h('div', { class: 'admin-metrics' },
      card('Owner identity', d.owner_configured ? 'Configured' : 'Not configured'),
      card('Users', d.users_total, d.users_error || (d.supabase_admin_configured ? 'Supabase Auth' : 'Admin API not configured')),
      card('Active jobs', d.active_jobs), card('AI requests', d.ai_requests),
      card('AI tokens', d.ai_tokens), card('Admin actions', d.admin_actions),
      card('Database', d.database.backend, d.database.connected ? 'Connected' : 'Unavailable'),
      card('Free disk space', formatBytes(d.storage.free_bytes))),
      h('h3', {}, 'Jobs by status'),
      table(['Status', 'Count'], Object.entries(d.jobs_by_status).map(([status, count]) => [status, count])),
      h('p', { class: 'hint' }, d.maintenance_mode ? 'Maintenance mode is enabled.' : 'Maintenance mode is off.'));
    return;
  }
  if (name === 'Users') {
    const filter = h('input', { type: 'search', placeholder: 'Search email or user ID', maxlength: 120 });
    const [d, roleData] = await Promise.all([
      api('GET', '/api/admin/users' + (new URLSearchParams(location.search).get('adminUserQuery') ? '?q=' +
        encodeURIComponent(new URLSearchParams(location.search).get('adminUserQuery')) : '')),
      api('GET', '/api/admin/roles'),
    ]);
    const email = h('input', { type: 'email', placeholder: 'person@example.com', maxlength: 254 });
    const rows = d.users.map(user => {
      const owner = session?.user?.id === user.id;
      const role = h('select', { disabled: owner || !adminOwner }, ...Object.keys(roleData.roles).map(value =>
        h('option', { value, selected: user.role === value }, value)));
      const status = h('select', { disabled: owner || (!adminOwner && !adminCanChangeAccountStatus) }, ...['active', 'inactive', 'suspended'].map(value =>
        h('option', { value, selected: user.status === value }, value)));
      const permissionSelect = h('select', { multiple: true, class: 'admin-permissions',
        disabled: owner || !adminOwner }, ...(roleData.roles[user.role] || []).map(permission =>
        h('option', { value: permission, selected: user.permissions.includes(permission) }, permission)));
      role.addEventListener('change', () => {
        permissionSelect.replaceChildren(...(roleData.roles[role.value] || []).map(permission =>
          h('option', { value: permission, selected: true }, permission)));
      });
      const canSave = adminOwner || adminCanChangeAccountStatus;
      const save = h('button', { class: 'ghost', disabled: owner || !canSave, onclick: async e => {
        e.currentTarget.disabled = true;
        try {
          const controls = adminOwner ? {
            role: role.value, status: status.value,
            permissions: [...permissionSelect.selectedOptions].map(option => option.value),
          } : { status: status.value };
          await api('PUT', `/api/admin/users/${encodeURIComponent(user.id)}`, controls);
          toast('Account controls saved.');
          await renderAdminSection('Users', panel);
        } catch (error) { toast(error.message); }
        finally { e.currentTarget.disabled = owner || !canSave; }
      } }, owner ? 'Exclusive owner' : 'Save');
      const remove = owner || !adminOwner ? '' : h('button', { class: 'ghost', onclick: async () => {
        const confirmation = window.prompt(`Type ${user.email} to delete the Auth account. Lumina application data will be retained.`);
        if (confirmation === null) return;
        try {
          await api('DELETE', `/api/admin/users/${encodeURIComponent(user.id)}`, { confirmation });
          toast('Auth account deleted; Lumina data retained.');
          await renderAdminSection('Users', panel);
        } catch (error) { toast(error.message); }
      } }, 'Delete');
      return [h('span', {}, user.email || user.id), role, status, permissionSelect, save, remove];
    });
    const invite = h('button', { class: 'primary', disabled: !adminOwner, onclick: async e => {
      e.currentTarget.disabled = true;
      try {
        await api('POST', '/api/admin/users', { email: email.value.trim() });
        toast('Invitation sent.');
        await renderAdminSection('Users', panel);
      } catch (error) { toast(error.message); }
      finally { e.currentTarget.disabled = false; }
    } }, 'Invite user');
    const search = h('button', { class: 'ghost', onclick: async () => {
      const url = new URL(location.href);
      if (filter.value.trim()) url.searchParams.set('adminUserQuery', filter.value.trim());
      else url.searchParams.delete('adminUserQuery');
      history.replaceState(null, '', url.pathname + url.search);
      await renderAdminSection('Users', panel);
    } }, 'Search');
    filter.value = new URLSearchParams(location.search).get('adminUserQuery') || '';
    panel.replaceChildren(h('h3', {}, `Users (${d.total}${d.has_more ? '+' : ''})`),
      h('div', { class: 'row admin-invite' }, filter, search),
      h('div', { class: 'row admin-invite' }, email, invite),
      h('p', { class: 'hint' }, 'Multiple select uses Ctrl-click (Windows/Linux) or Command-click (macOS). Only the Super Admin can change account controls.'),
      table(['Email / ID', 'Role', 'Status', 'Permission grants', 'Action', 'Account'], rows));
    return;
  }
  if (name === 'Agents') {
    const d = await api('GET', '/api/admin/agents');
    const rows = d.agents.map(agent => [h('span', {}, `${agent.number}. ${agent.title}`, h('small', { class: 'muted' }, ` · ${agent.id}`)),
      agent.critical ? 'Critical' : toggle(agent.enabled ? 'Enabled' : 'Disabled', agent.enabled, async e => {
        try {
          await api('PUT', `/api/admin/agents/${encodeURIComponent(agent.id)}`, { enabled: e.currentTarget.checked });
          toast('Agent setting saved.');
          await renderAdminSection('Agents', panel);
        } catch (error) { toast(error.message); e.currentTarget.checked = agent.enabled; }
      }),
      `${agent.runs_30d} runs · ${agent.failures_30d} failed/skipped`,
      agent.runtime_configuration_supported ? 'Configurable' : 'Runtime tuning unavailable']);
    panel.replaceChildren(h('p', { class: 'hint' }, 'Critical production steps cannot be disabled. Runtime parameter tuning is not implemented.'),
      table(['Agent', 'Control', 'Last 30 days', 'Configuration'], rows));
    return;
  }
  if (name === 'Controls') {
    const d = await api('GET', '/api/admin/features');
    const controls = Object.entries(d.features).map(([flag, enabled]) => toggle(flag.replaceAll('_', ' '), enabled, async e => {
      try {
        await api('PUT', `/api/admin/features/${encodeURIComponent(flag)}`, { enabled: e.currentTarget.checked });
        toast('Feature setting saved.');
      } catch (error) { toast(error.message); e.currentTarget.checked = enabled; }
    }));
    controls.push(toggle('Maintenance mode', d.maintenance_mode, async e => {
      try {
        await api('PUT', '/api/admin/maintenance', { enabled: e.currentTarget.checked });
        toast('Maintenance mode updated.');
      } catch (error) { toast(error.message); e.currentTarget.checked = d.maintenance_mode; }
    }));
    panel.replaceChildren(h('p', { class: 'hint' }, 'Changes are enforced by the API. Maintenance mode keeps health checks, admin routes, OAuth callbacks, and time-limited publishing media available.'),
      h('div', { class: 'admin-controls' }, controls));
    return;
  }
  if (name === 'Settings') {
    const [settings, integrations] = await Promise.all([
      api('GET', '/api/admin/settings'), api('GET', '/api/admin/integrations'),
    ]);
    panel.replaceChildren(h('p', { class: 'hint' }, 'Shared renderer and service settings are available in the standard Settings panel. Secrets are never returned here.'),
      h('div', { class: 'row' }, h('button', { class: 'primary', onclick: () => { $('#dlg-admin').close(); openSettings(false); } }, 'Open application settings')),
      h('h3', {}, 'Configured server variables'),
      table(['Variable', 'Status'], Object.entries(integrations.configured).map(([key, configured]) =>
        [key, configured ? 'Configured' : 'Not configured'])),
      h('h3', {}, 'Safe settings summary'), h('pre', { class: 'admin-pre' }, JSON.stringify(settings, null, 2)));
    return;
  }
  if (name === 'Operations') {
    const [health, integrations, notifications, backups, errors] = await Promise.all([
      api('GET', '/api/admin/health'), api('GET', '/api/admin/integrations'),
      api('GET', '/api/admin/notifications'), api('GET', '/api/admin/backups'),
      api('GET', '/api/admin/errors'),
    ]);
    const notificationRows = notifications.notifications.map(item => [
      `${item.kind} · ${item.status}`, item.job_id, `${item.attempts} attempts`,
      item.status === 'failed' ? h('button', { class: 'ghost', onclick: async () => {
        try { await api('POST', `/api/admin/notifications/${encodeURIComponent(item.id)}/retry`); toast('Notification queued for retry.'); }
        catch (error) { toast(error.message); }
      } }, 'Retry') : '',
    ]);
    panel.replaceChildren(h('div', { class: 'admin-metrics' },
      card('Database', health.database ? 'Connected' : 'Unavailable', health.database_backend),
      card('Maintenance', health.maintenance_mode ? 'Enabled' : 'Off'),
      card('Billing', 'Deferred', integrations.billing.reason)),
      h('h3', {}, 'Integration status'),
      table(['Integration', 'Status'], Object.entries(integrations.configured).map(([key, configured]) =>
        [key, configured ? 'Configured' : 'Not configured'])),
      h('h3', {}, 'Email notifications'), table(['Type / status', 'Job', 'Attempts', 'Action'], notificationRows),
      h('h3', {}, 'Backups'),
      h('div', { class: 'row' }, h('button', { class: 'primary', onclick: async e => {
        e.currentTarget.disabled = true;
        try { await api('POST', '/api/admin/backups'); toast('Database backup created.'); await renderAdminSection('Operations', panel); }
        catch (error) { toast(error.message); }
        finally { e.currentTarget.disabled = false; }
      } }, 'Create backup')),
      table(['File', 'Size'], backups.backups.map(file => [file.name, formatBytes(file.size_bytes)])),
      h('p', { class: 'hint' }, 'Restore is a manual recovery procedure and is not exposed as a one-click operation.'),
      h('h3', {}, 'Recent errors'), h('pre', { class: 'admin-pre' }, errors.available ? errors.lines.join('\n') : 'No error log is available.'));
    return;
  }
  if (name === 'Audit') {
    const [audit, sessions] = await Promise.all([api('GET', '/api/admin/audit'), api('GET', '/api/admin/sessions')]);
    panel.replaceChildren(h('h3', {}, 'Administrator and application audit'),
      table(['Time', 'Actor', 'Action', 'Detail'], audit.events.map(event => [
        new Date(event.ts * 1000).toLocaleString(), event.actor, event.action, event.detail,
      ])),
      h('h3', {}, 'Verified sign-in sessions'),
      table(['User', 'First seen', 'Last seen', 'User agent'], sessions.sessions.map(item => [
        item.user_id, new Date(item.first_seen * 1000).toLocaleString(), new Date(item.last_seen * 1000).toLocaleString(), item.user_agent,
      ])));
    return;
  }
  if (name === 'Capabilities') {
    const d = await api('GET', '/api/admin/capabilities');
    panel.replaceChildren(h('p', { class: 'hint' }, 'Capability status reflects implemented controls. Deferred and manual-only items are not represented as working features.'),
      table(['Capability', 'Status', 'Your access'], d.capabilities.map(item => [
        item.name, item.status.replaceAll('_', ' '), item.permitted ? 'Allowed' : 'Not granted',
      ])));
  }
}
async function refreshStatus() {
  const el = $('#status'); el.replaceChildren();
  try {
    const s = await api('GET', '/api/status');
    const dot = (ok, t) => h('span', {}, h('i', { class: ok ? 'ok' : '' }), t);
    el.append(dot(s.llm, 'AI'), dot(s.stock.some(x => x !== 'wikimedia'), 'Footage'), dot(s.ffmpeg, 'FFmpeg'), dot(s.voice_engines.includes('edge'), 'Voice'));
    state.meta = s.agents;
    const hint = []; if (!s.llm) hint.push('add a free AI key'); if (!s.stock.some(x => x !== 'wikimedia')) hint.push('add a free Pexels key'); if (!s.ffmpeg) hint.push('install FFmpeg');
    $('#create-hint').textContent = hint.length ? 'Before your first video: ' + hint.join(', ') + ' (Settings).' : '';
  } catch (e) { el.append(h('span', {}, h('i'), 'Offline')); }
}

// ------------------------------------------------------------------------ init
function init() {
  $('#year').textContent = new Date().getFullYear();
  setAuthMode('signin');
  if (reviewToken && reviewJob) { initPublicReview(); return; }
  $('#auth-form').addEventListener('submit', submitAuth);
  $('#auth-switch').addEventListener('click', () => setAuthMode(authMode === 'signin' ? 'signup' : 'signin'));
  $('#auth-google').addEventListener('click', () => startOAuth('google'));
  $('#auth-github').addEventListener('click', () => startOAuth('github'));
  $('#auth-forgot').addEventListener('click', async () => {
    if (!supabase) return authNotice('Supabase is not configured.', true);
    const email = $('#auth-email').value.trim();
    if (!email) return authNotice('Enter your email address first.');
    const { error } = await supabase.auth.resetPasswordForEmail(email, { redirectTo: window.location.origin + window.location.pathname });
    authNotice(error ? error.message : 'If an account exists for that address, a password reset email is on its way.', !!error);
  });
  $('#local-connect').addEventListener('click', async () => {
    const value = $('#local-token').value.trim();
    if (!value) return authNotice('Paste the local access token printed by python run.py.', true);
    TOKEN = value;
    localStorage.setItem('lumina_token', TOKEN);
    await showApp(null);
  });
  $('#btn-signout').addEventListener('click', async () => {
    if (supabase) {
      const { error } = await supabase.auth.signOut();
      if (error) return toast(error.message);
    }
    session = null;
    TOKEN = '';
    localStorage.removeItem('lumina_token');
    profile = null;
    showAuth();
  });
  const sel = $('#lang'); for (const [k, v] of Object.entries(LANGS)) sel.append(h('option', { value: k }, v));
  document.querySelectorAll('#format button').forEach(b => b.addEventListener('click', () => setFormat(b.dataset.v)));
  $('#len').addEventListener('input', lenLabel); setFormat('short');
  $('#btn-create').addEventListener('click', createJob);
  $('#btn-preflight').addEventListener('click', async e => {
    const button = e.currentTarget;
    button.disabled = true;
    $('#create-hint').textContent = 'Checking settings, providers, renderer, and available storage…';
    try {
      const result = await api('POST', '/api/preflight', { ...templateOptions(), topic: $('#topic').value.trim() });
      $('#create-hint').textContent = `${result.ready ? 'Ready to create.' : 'Resolve the blockers first.'} ` +
        result.checks.map(check => `${check.status.toUpperCase()}: ${check.name} — ${check.detail}`).join(' · ');
    } catch (error) { $('#create-hint').textContent = error.message; }
    finally { button.disabled = false; }
  });
  $('#template-select').addEventListener('change', () => {
    const template = templates.find(item => item.id === $('#template-select').value);
    if (template) applyTemplate(template);
  });
  $('#btn-template-save').addEventListener('click', async () => {
    const name = window.prompt('Name this video template');
    if (!name) return;
    try { await api('POST', '/api/templates', { name, params: templateOptions() }); await loadTemplates(); toast('Template saved.'); }
    catch (error) { toast(error.message); }
  });
  $('#btn-template-delete').addEventListener('click', async () => {
    const id = $('#template-select').value;
    if (!id) return toast('Choose a saved template first.');
    try { await api('DELETE', `/api/templates/${encodeURIComponent(id)}`); await loadTemplates(); toast('Template deleted.'); }
    catch (error) { toast(error.message); }
  });
  $('#topic').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) createJob(); });
  $('#btn-jobs').addEventListener('click', openJobs); $('#btn-settings').addEventListener('click', () => openSettings(false)); $('#btn-ideas').addEventListener('click', openIdeas);
  $('#btn-admin').addEventListener('click', openAdminControl);
  $('#btn-profile').addEventListener('click', openProfileSettings);
  $('#btn-versions').addEventListener('click', openVersions);
  $('#btn-calendar').addEventListener('click', openCalendar);
  $('#btn-library').addEventListener('click', openLibrary);
  $('#btn-workspaces').addEventListener('click', openWorkspaces);
  $('#btn-experiments').addEventListener('click', openExperiments);
  $('#btn-timeline').addEventListener('click', openTimelineEditor);
  $('#btn-preview').addEventListener('click', async () => {
    if (!state.job) return toast('Open a video project first.');
    const button = $('#btn-preview');
    button.disabled = true;
    try {
      await api('POST', `/api/jobs/${encodeURIComponent(state.job)}/preview`);
      const video = h('video', { controls: true, preload: 'metadata' });
      protectedFile(video, state.job, 'preview.mp4');
      $('#result').hidden = false;
      $('#result').replaceChildren(h('h2', {}, 'Preview render'), video);
      $('#result').scrollIntoView({ behavior: 'smooth', block: 'center' });
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  });
  $('#btn-review-link').addEventListener('click', async () => {
    if (!state.job) return toast('Open a video project first.');
    try {
      const link = await api('POST', `/api/jobs/${encodeURIComponent(state.job)}/review-link`);
      const url = `${location.origin}${location.pathname}?review=${encodeURIComponent(link.token)}&job=${encodeURIComponent(state.job)}`;
      await navigator.clipboard.writeText(url);
      toast('Read-only review link copied; it expires in seven days.');
    } catch (error) { toast(error.message); }
  });
  $('#btn-schedule').addEventListener('click', async () => {
    if (!state.job) return toast('Open a video project first.');
    const value = window.prompt('Schedule this video (local date/time, e.g. 2026-12-01 18:30)');
    if (!value) return;
    const scheduled = Date.parse(value.replace(' ', 'T')) / 1000;
    if (!Number.isFinite(scheduled)) return toast('Enter a valid local date and time.');
    try {
      await api('POST', `/api/jobs/${encodeURIComponent(state.job)}/schedule`, { scheduled });
      toast('Added to your content calendar.');
    } catch (error) { toast(error.message); }
  });
  $('#btn-save-version').addEventListener('click', async () => {
    if (!state.job) return toast('Open a video project first.');
    const label = window.prompt('Name this project version', 'Checkpoint');
    if (label === null) return;
    try {
      await api('POST', `/api/jobs/${encodeURIComponent(state.job)}/versions`, { label });
      toast('Project version saved.');
    } catch (error) { toast(error.message); }
  });
  $('#btn-new').addEventListener('click', () => { if (state.abort) state.abort.abort(); state.job = null; $('#live').hidden = true; $('#create').hidden = false; });
  $('#topic').addEventListener('input', () => {
    if (appPreferences.autosave_topic) localStorage.setItem('lumina_topic_draft', $('#topic').value);
  });
  $('#btn-cancel').addEventListener('click', async () => {
    if (appPreferences.confirm_cancel && !window.confirm('Cancel this video? This cannot be undone.')) return;
    try { await api('POST', `/api/jobs/${state.job}/cancel`); toast('Cancelling…'); } catch (e) { toast(e.message); }
  });
  if (supabase) {
    supabase.auth.onAuthStateChange((_event, nextSession) => {
      session = nextSession;
      TOKEN = nextSession?.access_token || '';
      setTimeout(() => {
        if (session !== nextSession) return;
        if (nextSession?.user) showApp(nextSession.user);
        else showAuth();
      }, 0);
    });
    supabase.auth.getSession().then(({ data, error }) => {
      if (error) authNotice(error.message, true);
      session = data.session;
      TOKEN = data.session?.access_token || '';
      if (data.session?.user) showApp(data.session.user);
      else showAuth();
    }).catch(e => authNotice(e.message, true));
  } else {
    showAuth();
    if (TOKEN) showApp(null);
  }
  setInterval(() => { if (!$('#app-shell').hidden && document.visibilityState === 'visible') refreshStatus(); }, 30000);
}
init();
})();
