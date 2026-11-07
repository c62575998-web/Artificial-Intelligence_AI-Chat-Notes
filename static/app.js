const $ = name => document.getElementById(name);
const element = (tag, value, className = '') => {const node = document.createElement(tag); node.textContent = value; node.className = className; return node;};
const status = (message, error = false) => {$('status').textContent = message; $('status').className = error ? 'error' : '';};
const timeOf = value => {if (!value) return ''; const date = new Date(value); return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('zh-TW', {hour:'2-digit',minute:'2-digit'}).format(date);};
let conversationId = Number(localStorage.getItem('chat-notes-conversation') || 0);
let notes = [], selected = null, currentPreview = null, requestId = null;
let sending = false;

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...options});
  const value = await response.json();
  if (!response.ok) throw Object.assign(new Error(value.error || `HTTP ${response.status}`), {saved: value.saved});
  return value;
}

function showPage() {
  const page = ['chat', 'notes', 'sources', 'search'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'chat';
  for (const name of ['chat', 'notes', 'sources', 'search']) {
    $(`page-${name}`).hidden = name !== page;
    const link = document.querySelector(`[data-page="${name}"]`);
    if (name === page) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  window.scrollTo(0, 0);
}

async function ensureConversation() {
  if (conversationId) {
    try {await api(`/api/conversations/${conversationId}/messages`); return;}
    catch {conversationId = 0;}
  }
  const result = await api('/api/conversations', {method: 'POST'});
  conversationId = result.id;
  localStorage.setItem('chat-notes-conversation', conversationId);
}

async function loadMessages() {
  const messages = await api(`/api/conversations/${conversationId}/messages`);
  const host = $('messages'); host.replaceChildren();
  if (!messages.length) {
    const intro = element('div', '', 'chat-intro');
    intro.append(element('strong', '開始一段對話'), element('p', '你的訊息會顯示在右側，系統回覆在左側。要整理筆記時，請描述明確的主題與事實。'));
    host.append(intro); return;
  }
  for (const message of messages) {
    const row = element('div', '', `message-row ${message.role}`);
    const content = element('div', '', 'message-content');
    content.append(element('div', message.text, 'message-bubble'));
    if (timeOf(message.sent_at)) content.append(element('time', timeOf(message.sent_at), 'message-time'));
    row.append(element('div', '記', 'message-avatar'), content);
    host.append(row);
  }
  host.scrollTop = host.scrollHeight;
}

function renderNotes() {
  const host = $('notes'); host.replaceChildren();
  if (!notes.length) {host.append(element('p', '尚無筆記。先到聊天頁送出訊息，再按「整理對話」。', 'empty')); return;}
  for (const [index, note] of notes.entries()) {
    const block = element('article', '', 'note');
    const heading = element('div', '', 'note-head');
    heading.append(element('h2', note.title), element('span', String(index + 1).padStart(2, '0'), 'note-index'));
    block.append(heading);
    for (const claim of note.claims) {
      const button = element('button', claim.sentence, 'claim');
      button.type = 'button'; button.onclick = () => selectClaim(claim.id, true);
      block.append(button);
    }
    host.append(block);
  }
}

function renderClaimList() {
  const host = $('claim-list'); host.replaceChildren();
  const entries = notes.flatMap(n => n.claims.map(c => ({note: n, claim: c})));
  if (!entries.length) {host.append(element('p', '還沒有可查閱的句子。', 'empty')); return;}
  for (const {note, claim} of entries) {
    const button = element('button', '', `claim-choice${claim.id === selected ? ' active' : ''}`);
    button.type = 'button'; button.append(element('small', note.title), element('span', claim.sentence));
    button.onclick = () => selectClaim(claim.id);
    host.append(button);
  }
}

async function renderSelected() {
  const claim = notes.flatMap(n => n.claims).find(c => c.id === selected);
  if (!claim) {
    $('selected').textContent = '請先選擇一個筆記句子。';
    $('sources').replaceChildren(); $('revision-form').hidden = true;
    return;
  }
  $('selected').textContent = claim.sentence;
  $('revision-form').hidden = false;
  const sources = await api(`/api/claims/${claim.id}/sources`);
  $('sources').replaceChildren(...sources.map(item => element('div', `來源訊息 #${item.message_id}：${item.text}`, 'source')));
}

async function loadNotes() {
  notes = await api('/api/notes');
  renderNotes(); renderClaimList(); await renderSelected();
}

async function selectClaim(claimId, navigate = false) {
  selected = claimId; currentPreview = null; requestId = null; $('preview').replaceChildren();
  renderClaimList(); await renderSelected();
  if (navigate) location.hash = '#sources';
}

$('chat-form').onsubmit = async event => {
  event.preventDefault();
  if (sending) return;
  sending = true; document.querySelector('#chat-form button').disabled = true;
  try {
    await api(`/api/conversations/${conversationId}/messages`, {method:'POST', body:JSON.stringify({text:$('chat-text').value})});
    $('chat-text').value = ''; $('chat-text').style.height = ''; await loadMessages(); status('');
  } catch (error) {
    if (error.saved) {$('chat-text').value = ''; $('chat-text').style.height = ''; await loadMessages();}
    status(error.message, true);
  } finally {sending = false; document.querySelector('#chat-form button').disabled = false; $('chat-text').focus();}
};

$('chat-text').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {event.preventDefault(); $('chat-form').requestSubmit();}
});
$('chat-text').addEventListener('input', event => {
  event.target.style.height = 'auto';
  event.target.style.height = `${Math.min(event.target.scrollHeight, 140)}px`;
});

$('organize').onclick = async () => {
  try {
    const result = await api(`/api/conversations/${conversationId}/organize`, {method:'POST'});
    await loadNotes();
    status(`整理完成，新增 ${result.created.length} 項記憶。${result.skipped?.length ? `有 ${result.skipped.length} 項與現有內容衝突，請到來源與修正頁處理。` : ''}`);
    if (result.created.length) location.hash = '#notes';
  } catch (error) {status(error.message, true);}
};

$('search-form').onsubmit = async event => {
  event.preventDefault();
  try {
    const params = new URLSearchParams({q:$('search-text').value});
    if ($('as-of').value) params.set('as_of', $('as-of').value);
    const results = await api(`/api/search?${params}`);
    $('results').replaceChildren(...results.map(m => {
      const item = element('div', '', 'result');
      item.append(element('small', `${m.topic} / 版本 ${m.version}`), element('span', `${m.relation}：${m.value}`));
      return item;
    }));
    if (!results.length) $('results').append(element('p', '沒有找到相關記憶。', 'empty'));
  } catch (error) {status(error.message, true);}
};

$('revision-form').onsubmit = async event => {
  event.preventDefault();
  try {
    const result = await api('/api/revisions/preview', {method:'POST', body:JSON.stringify({claim_id:selected,new_value:$('new-value').value,kind:$('kind').value,effective_from:$('effective-from').value,reason:$('reason').value})});
    currentPreview = result.preview_id; requestId = crypto.randomUUID();
    const host = $('preview'); host.replaceChildren(); host.className = 'preview';
    host.append(element('h3', '修正預覽'), element('p', `原值：${result.before.value} → 新值：${result.after.value}`));
    for (const claim of result.affected) host.append(element('div', `${claim.title}：${claim.sentence} → ${claim.after}`, 'source'));
    const confirm = element('button', '確認提交', 'button-dark'); confirm.type = 'button'; confirm.onclick = async () => {
      try {
        const response = await api('/api/revisions/confirm', {method:'POST', body:JSON.stringify({preview_id:currentPreview,request_id:requestId})});
        currentPreview = null; host.replaceChildren(); await loadNotes();
        status(`修正完成，更新 ${response.updated_claims} 個句子。`);
      } catch (error) {status(error.message, true);}
    };
    const cancel = element('button', '取消', 'button-outline'); cancel.type = 'button'; cancel.onclick = async () => {
      try {await api(`/api/revisions/preview/${currentPreview}`, {method:'DELETE'}); currentPreview = null; host.replaceChildren(); status('已取消修正，資料沒有改變。');}
      catch (error) {status(error.message, true);}
    };
    host.append(confirm, cancel);
    status(`將更新 ${result.affected.length} 個筆記句子。`);
  } catch (error) {status(error.message, true);}
};

window.addEventListener('hashchange', showPage);
ensureConversation().then(async () => {await Promise.all([loadMessages(), loadNotes()]); showPage();}).catch(error => status(error.message, true));
showPage();
