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

function render() {
  $('messages').replaceChildren(...state.messages.map(m => element('div', `${m.role === 'user' ? '你' : '系統'}：${m.text}`, 'message')));
  $('notes').replaceChildren();
  for (const note of state.notes) {
    const block = element('div', ''); block.append(element('h3', note.title));
    for (const claim of note.claims) {
      const button = element('button', claim.sentence, 'claim');
      button.onclick = () => selectClaim(claim.id);
      block.append(button);
    }
    $('notes').append(block);
  }
  if (!state.notes.length) $('notes').append(element('p', '尚無筆記。'));
  if (selected) selectClaim(selected);
}

function selectClaim(claimId) {
  const claim = state.notes.flatMap(n => n.claims).find(c => c.id === claimId);
  if (!claim) return;
  selected = claim.id; draft = null;
  $('preview').replaceChildren();
  $('selected').textContent = `選取句子：${claim.sentence}`;
  $('revision-form').hidden = false;
  const memory = state.memories.find(m => m.id === claim.memoryId);
  $('sources').replaceChildren(...(memory?.sources || []).map(sourceId => {
    const message = state.messages.find(m => m.id === sourceId);
    return element('div', `來源訊息 #${sourceId}：${message?.text || '找不到訊息'}`, 'source');
  }));
}

$('chat-form').onsubmit = event => {
  event.preventDefault();
  try {addMessage(state, $('chat-text').value); save(); $('chat-text').value = ''; render(); status('訊息已保存於此瀏覽器。');}
  catch (error) {status(error.message, true);}
};

$('organize').onclick = () => {
  try {
    const result = organize(state); save(); render();
    status(`整理完成，新增 ${result.created.length} 項記憶。${result.skipped.length ? `有 ${result.skipped.length} 項與現有內容衝突，請從筆記提出修正。` : ''}`);
  } catch (error) {status(error.message, true);}
};

$('search-form').onsubmit = event => {
  event.preventDefault();
  const items = search(state, $('search-text').value, $('as-of').value || null);
  $('results').replaceChildren(...items.map(m => element('div', `${m.topic}｜${m.relation}：${m.value}（版本 ${m.version}）`, 'source')));
  if (!items.length) $('results').append(element('p', '沒有找到相關記憶。'));
};

$('revision-form').onsubmit = event => {
  event.preventDefault();
  try {
    draft = preview(state, selected, $('new-value').value, $('kind').value, $('effective-from').value, $('reason').value);
    const host = $('preview'); host.replaceChildren(); host.className = 'preview';
    const old = state.memories.find(m => m.id === draft.oldId);
    host.append(element('h3', '修正預覽'), element('p', `原值：${old.value} → 新值：${draft.newValue}`));
    for (const claim of draft.affected) host.append(element('div', `${claim.title}：${claim.sentence} → ${old.relation}：${draft.newValue}。`, 'source'));
    const confirm = element('button', '確認提交'); confirm.onclick = () => {
      try {const count = draft.affected.length; confirmRevision(state, draft); save(); render(); status(`修正完成，更新 ${count} 個句子。`); draft = null;}
      catch (error) {status(error.message, true);}
    };
    const cancel = element('button', '取消', 'secondary'); cancel.onclick = () => {draft = null; host.replaceChildren(); status('已取消修正，資料沒有改變。');};
    host.append(confirm, cancel);
    status(`將更新 ${draft.affected.length} 個筆記句子。`);
  } catch (error) {status(error.message, true);}
};

$('reset').onclick = () => {
  if (!window.confirm('確定清除此瀏覽器的展示資料？')) return;
  state = emptyState(); selected = null; draft = null; save();
  $('sources').replaceChildren(); $('preview').replaceChildren(); $('revision-form').hidden = true;
  $('selected').textContent = '請點選一個筆記句子。';
  render(); status('本機展示資料已清除。');
};

render();
