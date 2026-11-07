import {emptyState, addMessage, organize, preview, confirmRevision, search} from './core.mjs';

const storageKey = 'ai-chat-notes-pages-demo-v1';
const $ = name => document.getElementById(name);
const element = (tag, value, className = '') => {const node = document.createElement(tag); node.textContent = value; node.className = className; return node;};
const status = (message, error = false) => {$('status').textContent = message; $('status').className = error ? 'error' : '';};
let state;
try {
  const saved = JSON.parse(localStorage.getItem(storageKey));
  state = saved && Array.isArray(saved.messages) && Array.isArray(saved.memories) && Array.isArray(saved.notes) ? saved : emptyState();
} catch {state = emptyState();}
let selected = null, draft = null;
const save = () => localStorage.setItem(storageKey, JSON.stringify(state));

function showPage() {
  const page = ['chat', 'notes', 'sources', 'search'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'chat';
  for (const name of ['chat', 'notes', 'sources', 'search']) {
    $(`page-${name}`).hidden = name !== page;
    const link = document.querySelector(`[data-page="${name}"]`);
    if (name === page) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  document.title = `${{chat:'聊天',notes:'主題筆記',sources:'來源與修正',search:'查詢記憶'}[page]}｜對話記憶`;
  window.scrollTo(0, 0);
}

function renderMessages() {
  const host = $('messages'); host.replaceChildren();
  if (!state.messages.length) {host.append(element('p', '對話會出現在這裡。先輸入一段生活事件吧。', 'empty')); return;}
  for (const message of state.messages) {
    const bubble = element('div', '', `message ${message.role}`);
    bubble.append(element('span', message.role === 'user' ? '你' : '系統', 'speaker'), element('span', message.text));
    host.append(bubble);
  }
  host.scrollTop = host.scrollHeight;
}

function renderNotes() {
  const host = $('notes'); host.replaceChildren();
  if (!state.notes.length) {host.append(element('p', '尚無筆記。先到聊天頁送出訊息，再按「整理對話」。', 'empty')); return;}
  for (const [index, note] of state.notes.entries()) {
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
  const entries = state.notes.flatMap(n => n.claims.map(c => ({note: n, claim: c})));
  if (!entries.length) {host.append(element('p', '還沒有可查閱的句子。', 'empty')); return;}
  for (const {note, claim} of entries) {
    const button = element('button', '', `claim-choice${claim.id === selected ? ' active' : ''}`);
    button.type = 'button'; button.append(element('small', note.title), element('span', claim.sentence));
    button.onclick = () => selectClaim(claim.id);
    host.append(button);
  }
}

function renderSelected() {
  const claim = state.notes.flatMap(n => n.claims).find(c => c.id === selected);
  if (!claim) {
    $('selected').textContent = '請先選擇一個筆記句子。';
    $('sources').replaceChildren(); $('revision-form').hidden = true;
    return;
  }
  $('selected').textContent = claim.sentence;
  $('revision-form').hidden = false;
  const memory = state.memories.find(m => m.id === claim.memoryId);
  $('sources').replaceChildren(...(memory?.sources || []).map(sourceId => {
    const message = state.messages.find(m => m.id === sourceId);
    return element('div', `來源訊息 #${sourceId}：${message?.text || '找不到訊息'}`, 'source');
  }));
}

function render() {renderMessages(); renderNotes(); renderClaimList(); renderSelected();}

function selectClaim(claimId, navigate = false) {
  selected = claimId; draft = null; $('preview').replaceChildren();
  renderClaimList(); renderSelected();
  if (navigate) location.hash = '#sources';
}

$('chat-form').onsubmit = event => {
  event.preventDefault();
  try {addMessage(state, $('chat-text').value); save(); $('chat-text').value = ''; render(); status('訊息已保存於此瀏覽器。');}
  catch (error) {status(error.message, true);}
};

$('organize').onclick = () => {
  try {
    const result = organize(state); save(); render();
    status(`整理完成，新增 ${result.created.length} 項記憶。${result.skipped.length ? `有 ${result.skipped.length} 項與現有內容衝突，請到來源與修正頁處理。` : ''}`);
    if (result.created.length) location.hash = '#notes';
  } catch (error) {status(error.message, true);}
};

$('search-form').onsubmit = event => {
  event.preventDefault();
  const items = search(state, $('search-text').value, $('as-of').value || null);
  $('results').replaceChildren(...items.map(m => {
    const item = element('div', '', 'result');
    item.append(element('small', `${m.topic} / 版本 ${m.version}`), element('span', `${m.relation}：${m.value}`));
    return item;
  }));
  if (!items.length) $('results').append(element('p', '沒有找到相關記憶。', 'empty'));
};

$('revision-form').onsubmit = event => {
  event.preventDefault();
  try {
    draft = preview(state, selected, $('new-value').value, $('kind').value, $('effective-from').value, $('reason').value);
    const host = $('preview'); host.replaceChildren(); host.className = 'preview';
    const old = state.memories.find(m => m.id === draft.oldId);
    host.append(element('h3', '修正預覽'), element('p', `原值：${old.value} → 新值：${draft.newValue}`));
    for (const claim of draft.affected) host.append(element('div', `${claim.title}：${claim.sentence} → ${old.relation}：${draft.newValue}。`, 'source'));
    const confirm = element('button', '確認提交', 'button-dark'); confirm.type = 'button'; confirm.onclick = () => {
      try {const count = draft.affected.length; confirmRevision(state, draft); save(); draft = null; host.replaceChildren(); render(); status(`修正完成，更新 ${count} 個句子。`);}
      catch (error) {status(error.message, true);}
    };
    const cancel = element('button', '取消', 'button-outline'); cancel.type = 'button'; cancel.onclick = () => {draft = null; host.replaceChildren(); status('已取消修正，資料沒有改變。');};
    host.append(confirm, cancel);
    status(`將更新 ${draft.affected.length} 個筆記句子。`);
  } catch (error) {status(error.message, true);}
};

$('reset').onclick = () => {
  if (!window.confirm('確定清除此瀏覽器的展示資料？')) return;
  state = emptyState(); selected = null; draft = null; save();
  $('preview').replaceChildren(); $('results').replaceChildren(); render(); status('本機展示資料已清除。');
};

window.addEventListener('hashchange', showPage);
render(); showPage();
