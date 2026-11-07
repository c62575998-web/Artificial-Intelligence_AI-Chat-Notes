import test from 'node:test';
import assert from 'node:assert/strict';
import {emptyState, addMessage, organize, preview, confirmRevision, search} from '../docs/core.mjs';

test('offline demo records sources and keeps history through a correction', () => {
  const state = emptyState();
  addMessage(state, '主題：看展；人物：小安；日期：2026-04-10；地點：展館入口');
  assert.equal(organize(state).created.length, 3);
  const claim = state.notes[0].claims.find(c => c.sentence.includes('日期'));
  const memory = state.memories.find(m => m.id === claim.memoryId);
  assert.equal(state.messages.find(m => m.id === memory.sources[0]).text.startsWith('主題：看展'), true);
  state.notes.push({id: state.nextId++, topic: '看展', title: '本月活動', claims: [{id: state.nextId++, memoryId: memory.id, sentence: claim.sentence, version: 1}]});
  const draft = preview(state, claim.id, '2026-04-12', 'change', '2026-04-04', '日期改為 4 月 12 日');
  assert.equal(draft.affected.length, 2);
  assert.equal(state.notes[0].claims.find(c => c.id === claim.id).sentence.includes('2026-04-10'), true);
  confirmRevision(state, draft);
  assert.equal(state.notes.flatMap(n => n.claims).filter(c => c.sentence.includes('2026-04-12')).length, 2);
  assert.equal(search(state, '看展', '2026-04-03').some(m => m.value === '2026-04-10'), true);
  assert.equal(search(state, '看展', '2026-04-05').some(m => m.value === '2026-04-12'), true);
  assert.equal(state.notes[0].claims.some(c => c.sentence.includes('小安')), true);
});

test('conflicting new fact requires a revision', () => {
  const state = emptyState();
  addMessage(state, '主題：看展；日期：2026-04-10'); organize(state);
  addMessage(state, '主題：看展；日期：2026-04-12');
  assert.equal(organize(state).skipped.length, 1);
  assert.equal(state.memories.length, 1);
});
