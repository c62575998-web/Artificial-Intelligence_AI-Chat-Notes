// Offline GitHub Pages demo. All data stays in this browser's localStorage.
export const emptyState = () => ({nextId: 1, messages: [], memories: [], notes: [], revisions: []});
const fields = new Set(['人物', '日期', '地點', '時間', '決定', '偏好', '狀態', '待確認']);
const id = state => state.nextId++;
const sentence = memory => `${memory.relation}：${memory.value}。`;

export function parseFacts(text) {
  const pairs = [...text.matchAll(/(?:^|[；;\n])\s*([^：:\n；;]+)\s*[：:]\s*([^；;\n]+)/g)];
  const values = Object.fromEntries(pairs.map(([, key, value]) => [key.trim(), value.trim().replace(/。$/, '')]));
  const topic = values['主題'] || values['事件'];
  return topic ? Object.entries(values).filter(([key, value]) => fields.has(key) && value).map(([relation, value]) => ({topic, relation, value})) : [];
}

export function addMessage(state, text) {
  if (!text.trim()) throw new Error('請輸入訊息');
  const sentAt = new Date().toISOString();
  state.messages.push({id: id(state), role: 'user', text: text.trim(), organized: false, sentAt});
  state.messages.push({id: id(state), role: 'assistant', text: '訊息已保存。按「整理筆記」可擷取明確的主題與事實。', sentAt});
}

export function organize(state) {
  const created = [], skipped = [];
  for (const message of state.messages.filter(item => item.role === 'user' && !item.organized)) {
    const facts = parseFacts(message.text);
    if (!facts.length) continue;
    for (const fact of facts) {
      const same = state.memories.find(m => m.status === 'current' && m.topic === fact.topic && m.relation === fact.relation && m.value === fact.value);
      if (same) { if (!same.sources.includes(message.id)) same.sources.push(message.id); continue; }
      if (state.memories.some(m => m.status === 'current' && m.topic === fact.topic && m.relation === fact.relation)) {
        skipped.push(fact); continue;
      }
      const memory = {id: id(state), logicalId: id(state), version: 1, ...fact, status: 'current', validFrom: null, validTo: null, sources: [message.id]};
      state.memories.push(memory);
      let note = state.notes.find(n => n.topic === fact.topic);
      if (!note) { note = {id: id(state), topic: fact.topic, title: fact.topic, claims: []}; state.notes.push(note); }
      note.claims.push({id: id(state), sentence: sentence(memory), memoryId: memory.id, version: 1});
      created.push(memory.id);
    }
    message.organized = true;
  }
  return {created, skipped};
}

export function preview(state, claimId, newValue, kind, effectiveFrom, reason) {
  if (!newValue?.trim() || !reason?.trim()) throw new Error('請填寫新內容與修正說明');
  if (!['correction', 'change'].includes(kind)) throw new Error('請選擇修正類型');
  if (kind === 'change' && !/^\d{4}-\d{2}-\d{2}$/.test(effectiveFrom || '')) throw new Error('狀態改變需要生效日期');
  if (effectiveFrom && effectiveFrom > new Date().toLocaleDateString('sv-SE')) throw new Error('目前版本尚不支援未來生效日期');
  const claim = state.notes.flatMap(n => n.claims).find(c => c.id === claimId);
  const old = state.memories.find(m => m.id === claim?.memoryId && m.status === 'current');
  if (!old) throw new Error('找不到目前版本');
  if (old.value === newValue.trim()) throw new Error('新舊內容相同');
  const affected = state.notes.flatMap(n => n.claims.map(c => ({...c, title: n.title}))).filter(c => c.memoryId === old.id);
  return {oldId: old.id, expectedVersion: old.version, newValue: newValue.trim(), kind, effectiveFrom: kind === 'change' ? effectiveFrom : null, reason: reason.trim(), affected};
}

export function confirmRevision(state, draft) {
  const old = state.memories.find(m => m.id === draft.oldId);
  if (!old || old.status !== 'current' || old.version !== draft.expectedVersion) throw new Error('版本已變更，請重新預覽');
  old.status = draft.kind === 'correction' ? 'error' : 'superseded';
  old.validTo = draft.effectiveFrom;
  const source = {id: id(state), role: 'user', text: draft.reason, organized: true};
  state.messages.push(source);
  const next = {...old, id: id(state), version: old.version + 1, value: draft.newValue, status: 'current', validFrom: draft.effectiveFrom, validTo: null, sources: [source.id]};
  state.memories.push(next);
  for (const claim of state.notes.flatMap(n => n.claims).filter(c => c.memoryId === old.id)) {
    claim.memoryId = next.id;
    claim.sentence = sentence(next);
    claim.version++;
  }
  state.revisions.push({oldId: old.id, newId: next.id, reason: draft.reason});
  return next;
}

export function search(state, query, asOf = null) {
  const day = asOf || new Date().toLocaleDateString('sv-SE');
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  return state.memories.filter(m =>
    ((m.status === 'current' && (!m.validFrom || m.validFrom <= day)) ||
     (m.status === 'superseded' && (!m.validFrom || m.validFrom <= day) && m.validTo > day)) &&
    terms.some(term => `${m.topic}${m.relation}${m.value}`.toLowerCase().includes(term)));
}
