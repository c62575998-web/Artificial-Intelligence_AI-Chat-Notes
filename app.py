"""Traditional Chinese chat memory and traceable topic notes prototype."""

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import jieba
import numpy as np
from flask import Flask, jsonify, render_template, request


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS conversations (
 id INTEGER PRIMARY KEY, started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id),
 role TEXT NOT NULL CHECK(role IN ('user','assistant')), text TEXT NOT NULL,
 sent_at TEXT NOT NULL, organized INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS memories (
 id INTEGER PRIMARY KEY, logical_id TEXT NOT NULL, version INTEGER NOT NULL,
 topic TEXT NOT NULL, relation TEXT NOT NULL, value TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('current','superseded','error')),
 valid_from TEXT, valid_to TEXT, recorded_at TEXT NOT NULL,
 UNIQUE(logical_id, version)
);
CREATE TABLE IF NOT EXISTS memory_sources (
 memory_id INTEGER NOT NULL REFERENCES memories(id), message_id INTEGER NOT NULL REFERENCES messages(id),
 PRIMARY KEY(memory_id,message_id)
);
CREATE TABLE IF NOT EXISTS memory_search (
 memory_id INTEGER PRIMARY KEY REFERENCES memories(id), search_text TEXT NOT NULL,
 text_hash TEXT NOT NULL, tokens_json TEXT NOT NULL, vector_json TEXT,
 embedding_model TEXT
);
CREATE TABLE IF NOT EXISTS notes (
 id INTEGER PRIMARY KEY, topic TEXT NOT NULL, title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS note_claims (
 id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id),
 sentence TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS claim_refs (
 claim_id INTEGER NOT NULL REFERENCES note_claims(id), memory_id INTEGER NOT NULL REFERENCES memories(id),
 PRIMARY KEY(claim_id,memory_id)
);
CREATE TABLE IF NOT EXISTS revision_previews (
 id TEXT PRIMARY KEY, claim_id INTEGER NOT NULL REFERENCES note_claims(id),
 old_memory_id INTEGER NOT NULL REFERENCES memories(id), expected_version INTEGER NOT NULL,
 new_value TEXT NOT NULL, kind TEXT NOT NULL, effective_from TEXT,
 reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
 request_id TEXT PRIMARY KEY, preview_id TEXT NOT NULL, old_memory_id INTEGER NOT NULL,
 new_memory_id INTEGER NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
 reason TEXT NOT NULL, recorded_at TEXT NOT NULL
);
"""

FIELDS = {"日期", "地點", "人物", "時間", "決定", "偏好", "狀態", "待確認"}


def now():
    return datetime.now(timezone.utc).isoformat()


def db():
    conn = sqlite3.connect(os.environ.get("CHAT_NOTES_DB", "chat_notes.sqlite3"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def setup_db():
    with db() as conn:
        conn.executescript(SCHEMA)


def row(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()


def required(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"請填寫 {key}")
    return value.strip()


def iso_day(value):
    if value is None or value == "":
        return None
    date.fromisoformat(value)
    return value


def tokens(text):
    return [part.lower() for part in jieba.cut(text) if part.strip()]


def search_text(topic, relation, value):
    return f"主題：{topic}；關係：{relation}；值：{value}"


def embedding(text):
    base = os.environ.get("MODEL_BASE_URL")
    key = os.environ.get("MODEL_API_KEY")
    model = os.environ.get("EMBEDDING_MODEL")
    if not (base and key and model):
        return None, None
    response = httpx.post(
        base.rstrip("/") + "/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "input": text}, timeout=30.0,
    )
    response.raise_for_status()
    vector = response.json()["data"][0]["embedding"]
    array = np.asarray(vector, dtype=float)
    if array.ndim != 1 or not len(array) or not np.isfinite(array).all() or not np.linalg.norm(array):
        raise ValueError("無效的 Embedding 向量")
    return vector, model


def save_search(conn, memory_id, topic, relation, value):
    text = search_text(topic, relation, value)
    vector, model = embedding(text)
    conn.execute(
        "INSERT INTO memory_search VALUES (?,?,?,?,?,?)",
        (memory_id, text, hashlib.sha256(text.encode()).hexdigest(),
         json.dumps(tokens(text), ensure_ascii=False), json.dumps(vector) if vector else None, model),
    )


def claim_sentence(relation, value):
    return f"{relation}：{value}。"


def parse_facts(text):
    """Extract explicit facts from a constrained, transparent demo input format."""
    pairs = re.findall(r"(?:^|[；;\n])\s*([^：:\n；;]+)\s*[：:]\s*([^；;\n]+)", text)
    values = {key.strip(): value.strip().rstrip("。") for key, value in pairs}
    topic = values.get("主題") or values.get("事件")
    if not topic:
        return []
    return [(topic, key, value) for key, value in values.items() if key in FIELDS and value]


def model_completion(system, user):
    base = os.environ.get("MODEL_BASE_URL")
    key = os.environ.get("MODEL_API_KEY")
    model = os.environ.get("CHAT_MODEL")
    if not (base and key and model):
        return None
    response = httpx.post(
        base.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "temperature": 0, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}]},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def extract_facts(text):
    explicit = parse_facts(text)
    if explicit:
        return explicit
    output = model_completion(
        "從繁體中文訊息擷取明確、已確定的事實。只回傳 JSON 陣列，每項包含 topic、relation、value。"
        "relation 限定日期、地點、人物、時間、決定、偏好、狀態、待確認。"
        "不可推測未提及資訊；沒有合適事實時回傳空陣列。",
        text,
    )
    if output is None:
        return []
    try:
        items = json.loads(output)
        if not isinstance(items, list):
            return []
        return [(x["topic"].strip(), x["relation"].strip(), x["value"].strip())
                for x in items if isinstance(x, dict) and
                all(isinstance(x.get(k), str) and x[k].strip() for k in ("topic", "relation", "value"))
                and x["relation"].strip() in FIELDS]
    except (ValueError, KeyError, TypeError):
        return []


def answer(conn, text):
    results = search(conn, text, 5)
    evidence = [{"id": item["id"], "topic": item["topic"], "relation": item["relation"],
                 "value": item["value"], "valid_from": item["valid_from"]} for item in results]
    modeled = model_completion(
        "你是繁體中文聊天助手。回答可以自然，但涉及使用者過往事實時只能依提供的記憶。"
        "沒有證據就說不知道；不可把待確認資訊寫成已確定。",
        json.dumps({"message": text, "memories": evidence}, ensure_ascii=False),
    )
    if modeled is not None:
        return modeled, results
    if "?" in text or "？" in text or "哪" in text or "什麼" in text:
        if results:
            facts = "；".join(f"{item['relation']}：{item['value']}" for item in results[:3])
            return f"目前記錄：{facts}。請從筆記查看來源。", results
        return "目前沒有可確認的相關記憶。", []
    return "訊息已保存。按「整理筆記」可擷取明確的主題與事實。", []


def search(conn, query, limit=10, as_of=None):
    as_of = as_of or date.today().isoformat()
    rows = conn.execute("""SELECT m.*, s.search_text, s.tokens_json, s.vector_json
      FROM memories m JOIN memory_search s ON s.memory_id=m.id
      WHERE (m.status='current' AND (m.valid_from IS NULL OR m.valid_from<=?))
         OR (m.status='superseded' AND (m.valid_from IS NULL OR m.valid_from<=?) AND m.valid_to>?)
      ORDER BY m.id""", (as_of, as_of, as_of)).fetchall()
    if not rows:
        return []
    query_tokens = tokens(query)
    docs = [json.loads(r["tokens_json"]) for r in rows]
    avglen = sum(map(len, docs)) / len(docs)
    df = {term: sum(term in doc for doc in docs) for term in set(query_tokens)}
    bm = []
    for doc in docs:
        score = 0.0
        for term in query_tokens:
            freq = doc.count(term)
            if freq:
                idf = np.log1p((len(docs) - df[term] + .5) / (df[term] + .5))
                score += idf * freq * 2.5 / (freq + 1.5 * (1 - .75 + .75 * len(doc) / avglen))
        bm.append(float(score))
    lexical = sorted(range(len(rows)), key=lambda i: bm[i], reverse=True)
    ranked = {i: (1 / (60 + rank + 1) if bm[i] > 0 else 0) for rank, i in enumerate(lexical)}
    if any(r["vector_json"] for r in rows):
        qv, _ = embedding(query)
        if qv is not None:
            similarities = {}
            for i, r in enumerate(rows):
                if r["vector_json"]:
                    vector = np.asarray(json.loads(r["vector_json"]), dtype=float)
                    if len(vector) == len(qv) and np.linalg.norm(vector):
                        similarities[i] = float(np.dot(vector, qv) / (np.linalg.norm(vector) * np.linalg.norm(qv)))
            for rank, i in enumerate(sorted(similarities, key=similarities.get, reverse=True)):
                ranked[i] += 1 / (60 + rank + 1)
    indices = sorted(ranked, key=ranked.get, reverse=True)
    return [dict(rows[i]) | {"score": round(ranked[i], 5)} for i in indices if ranked[i] > 0][:limit]


def create_app(database=None):
    app = Flask(__name__)
    if database:
        os.environ["CHAT_NOTES_DB"] = str(database)
    setup_db()

    @app.errorhandler(ValueError)
    def bad_request(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(sqlite3.IntegrityError)
    def conflict(error):
        return jsonify(error="資料衝突，請重新讀取"), 409

    @app.errorhandler(httpx.HTTPError)
    def model_error(error):
        return jsonify(error="模型服務暫時無法回應；請稍後重試"), 502

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/api/conversations")
    def new_conversation():
        with db() as conn:
            cur = conn.execute("INSERT INTO conversations(started_at) VALUES (?)", (now(),))
            return jsonify(id=cur.lastrowid), 201

    @app.get("/api/conversations/<int:conversation_id>/messages")
    def messages(conversation_id):
        with db() as conn:
            items = conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()
            return jsonify([dict(item) for item in items])

    @app.post("/api/conversations/<int:conversation_id>/messages")
    def chat(conversation_id):
        text = required(request.get_json() or {}, "text")
        with db() as conn:
            if not row(conn, "SELECT id FROM conversations WHERE id=?", (conversation_id,)):
                return jsonify(error="找不到對話"), 404
            conn.execute("INSERT INTO messages(conversation_id,role,text,sent_at) VALUES (?,'user',?,?)", (conversation_id, text, now()))
        with db() as conn:
            try:
                reply, sources = answer(conn, text)
            except httpx.HTTPError:
                return jsonify(error="訊息已保存，但模型服務暫時無法回應", saved=True), 502
            cur = conn.execute("INSERT INTO messages(conversation_id,role,text,sent_at) VALUES (?,'assistant',?,?)", (conversation_id, reply, now()))
            return jsonify(reply=reply, message_id=cur.lastrowid, memory_ids=[s["id"] for s in sources]), 201

    @app.post("/api/conversations/<int:conversation_id>/organize")
    def organize(conversation_id):
        with db() as conn:
            messages = conn.execute("SELECT * FROM messages WHERE conversation_id=? AND role='user' AND organized=0 ORDER BY id", (conversation_id,)).fetchall()
            if not messages:
                return jsonify(created=[], message="沒有待整理的訊息")
            parsed = [(m["id"], *fact) for m in messages for fact in extract_facts(m["text"])]
            if not parsed:
                return jsonify(error="請使用「主題：...；日期：...」等明確欄位描述，才能整理筆記"), 422
            created = []
            skipped = []
            for message_id, topic, relation, value in parsed:
                existing = row(conn, "SELECT id FROM memories WHERE topic=? AND relation=? AND value=? AND status='current'", (topic, relation, value))
                if existing:
                    conn.execute("INSERT OR IGNORE INTO memory_sources VALUES (?,?)", (existing["id"], message_id))
                    continue
                previous = row(conn, "SELECT * FROM memories WHERE topic=? AND relation=? AND status='current' ORDER BY id DESC", (topic, relation))
                if previous:
                    # A changed value is only accepted through the explicit correction flow.
                    skipped.append({"topic": topic, "relation": relation, "value": value,
                                    "reason": "此欄位已有不同的目前值；請從筆記提出修正"})
                    continue
                logical_id = uuid.uuid4().hex
                cur = conn.execute("INSERT INTO memories(logical_id,version,topic,relation,value,status,recorded_at) VALUES (?,?,?,?,?,'current',?)", (logical_id, 1, topic, relation, value, now()))
                memory_id = cur.lastrowid
                save_search(conn, memory_id, topic, relation, value)
                conn.execute("INSERT INTO memory_sources VALUES (?,?)", (memory_id, message_id))
                note = row(conn, "SELECT id FROM notes WHERE topic=? ORDER BY id LIMIT 1", (topic,))
                if not note:
                    note_id = conn.execute("INSERT INTO notes(topic,title) VALUES (?,?)", (topic, topic)).lastrowid
                else:
                    note_id = note["id"]
                claim_id = conn.execute("INSERT INTO note_claims(note_id,sentence) VALUES (?,?)", (note_id, claim_sentence(relation, value))).lastrowid
                conn.execute("INSERT INTO claim_refs VALUES (?,?)", (claim_id, memory_id))
                created.append(memory_id)
            conn.executemany("UPDATE messages SET organized=1 WHERE id=?", [(m["id"],) for m in messages])
            return jsonify(created=created, skipped=skipped)

    @app.get("/api/notes")
    def notes():
        with db() as conn:
            result = []
            for note in conn.execute("SELECT * FROM notes ORDER BY id DESC"):
                claims = conn.execute("SELECT * FROM note_claims WHERE note_id=? ORDER BY id", (note["id"],)).fetchall()
                result.append(dict(note) | {"claims": [dict(c) for c in claims]})
            return jsonify(result)

    @app.post("/api/notes")
    def create_note():
        data = request.get_json() or {}
        title = required(data, "title")
        memory_ids = data.get("memory_ids", [])
        if not isinstance(memory_ids, list) or not memory_ids:
            raise ValueError("請選擇記憶")
        with db() as conn:
            memories = [row(conn, "SELECT * FROM memories WHERE id=? AND status='current'", (mid,)) for mid in memory_ids]
            if any(m is None for m in memories):
                raise ValueError("記憶不存在或已失效")
            topic = memories[0]["topic"]
            note_id = conn.execute("INSERT INTO notes(topic,title) VALUES (?,?)", (topic, title)).lastrowid
            for memory in memories:
                claim_id = conn.execute("INSERT INTO note_claims(note_id,sentence) VALUES (?,?)", (note_id, claim_sentence(memory["relation"], memory["value"]))).lastrowid
                conn.execute("INSERT INTO claim_refs VALUES (?,?)", (claim_id, memory["id"]))
            return jsonify(id=note_id), 201

    @app.get("/api/memories")
    def memories():
        with db() as conn:
            items = conn.execute("SELECT * FROM memories ORDER BY id DESC").fetchall()
            return jsonify([dict(item) for item in items])

    @app.get("/api/claims/<int:claim_id>/sources")
    def sources(claim_id):
        with db() as conn:
            items = conn.execute("""SELECT m.id memory_id,m.value,m.version,m.status,msg.id message_id,msg.text,msg.sent_at
              FROM claim_refs r JOIN memories m ON m.id=r.memory_id
              JOIN memory_sources s ON s.memory_id=m.id JOIN messages msg ON msg.id=s.message_id
              WHERE r.claim_id=? ORDER BY msg.id""", (claim_id,)).fetchall()
            return jsonify([dict(item) for item in items])

    @app.get("/api/search")
    def find():
        query = required(request.args, "q")
        as_of = iso_day(request.args.get("as_of"))
        with db() as conn:
            return jsonify(search(conn, query, as_of=as_of))

    @app.post("/api/revisions/preview")
    def preview():
        data = request.get_json() or {}
        claim_id = data.get("claim_id")
        new_value = required(data, "new_value")
        reason = required(data, "reason")
        kind = data.get("kind")
        if kind not in ("correction", "change"):
            raise ValueError("kind 必須是 correction 或 change")
        effective = iso_day(data.get("effective_from"))
        if kind == "change" and not effective:
            raise ValueError("狀態改變需要生效日期")
        if effective and effective > date.today().isoformat():
            raise ValueError("目前版本尚不支援未來生效日期")
        with db() as conn:
            old = row(conn, """SELECT m.* FROM note_claims c JOIN claim_refs r ON r.claim_id=c.id
              JOIN memories m ON m.id=r.memory_id WHERE c.id=? AND m.status='current'""", (claim_id,))
            if not old:
                return jsonify(error="找不到目前版本"), 404
            if old["value"] == new_value:
                raise ValueError("新舊內容相同")
            affected = conn.execute("""SELECT c.id,c.sentence,c.version,n.title FROM note_claims c
              JOIN claim_refs r ON r.claim_id=c.id JOIN notes n ON n.id=c.note_id
              WHERE r.memory_id=? ORDER BY c.id""", (old["id"],)).fetchall()
            preview_id = uuid.uuid4().hex
            conn.execute("INSERT INTO revision_previews VALUES (?,?,?,?,?,?,?,?,?)", (preview_id, claim_id, old["id"], old["version"], new_value, kind, effective, reason, now()))
            return jsonify(preview_id=preview_id, before=dict(old), after={"value": new_value, "kind": kind, "effective_from": effective},
                           affected=[dict(c) | {"after": claim_sentence(old["relation"], new_value)} for c in affected])

    @app.delete("/api/revisions/preview/<preview_id>")
    def cancel(preview_id):
        with db() as conn:
            conn.execute("DELETE FROM revision_previews WHERE id=?", (preview_id,))
            return jsonify(cancelled=True)

    @app.post("/api/revisions/confirm")
    def confirm():
        data = request.get_json() or {}
        preview_id = required(data, "preview_id")
        request_id = required(data, "request_id")
        with db() as conn:
            prior = row(conn, "SELECT * FROM revisions WHERE request_id=?", (request_id,))
            if prior:
                return jsonify(request_id=request_id, memory_id=prior["new_memory_id"], repeated=True)
            preview = row(conn, "SELECT * FROM revision_previews WHERE id=?", (preview_id,))
            if not preview:
                return jsonify(error="預覽已取消或不存在"), 404
            old = row(conn, "SELECT * FROM memories WHERE id=?", (preview["old_memory_id"],))
            if old["status"] != "current" or old["version"] != preview["expected_version"]:
                return jsonify(error="版本已變更，請重新預覽"), 409
            affected = conn.execute("SELECT claim_id FROM claim_refs WHERE memory_id=?", (old["id"],)).fetchall()
            new_status = "error" if preview["kind"] == "correction" else "superseded"
            conn.execute("UPDATE memories SET status=?,valid_to=? WHERE id=?", (new_status, preview["effective_from"], old["id"]))
            cur = conn.execute("""INSERT INTO memories(logical_id,version,topic,relation,value,status,valid_from,recorded_at)
              VALUES (?,?,?,?,?,'current',?,?)""", (old["logical_id"], old["version"] + 1, old["topic"], old["relation"], preview["new_value"], preview["effective_from"], now()))
            new_id = cur.lastrowid
            save_search(conn, new_id, old["topic"], old["relation"], preview["new_value"])
            # The correction request is itself evidence; original messages remain immutable.
            source = row(conn, "SELECT msg.conversation_id FROM memory_sources s JOIN messages msg ON msg.id=s.message_id WHERE s.memory_id=? LIMIT 1", (old["id"],))
            if source:
                msg_id = conn.execute("INSERT INTO messages(conversation_id,role,text,sent_at,organized) VALUES (?,'user',?,?,1)", (source["conversation_id"], preview["reason"], now())).lastrowid
                conn.execute("INSERT INTO memory_sources VALUES (?,?)", (new_id, msg_id))
            for claim in affected:
                conn.execute("UPDATE note_claims SET sentence=?,version=version+1 WHERE id=?", (claim_sentence(old["relation"], preview["new_value"]), claim["claim_id"]))
                conn.execute("DELETE FROM claim_refs WHERE claim_id=? AND memory_id=?", (claim["claim_id"], old["id"]))
                conn.execute("INSERT INTO claim_refs VALUES (?,?)", (claim["claim_id"], new_id))
            conn.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?)", (request_id, preview_id, old["id"], new_id, json.dumps(dict(old), ensure_ascii=False), json.dumps({"value":preview["new_value"]}, ensure_ascii=False), preview["reason"], now()))
            conn.execute("DELETE FROM revision_previews WHERE id=?", (preview_id,))
            return jsonify(request_id=request_id, memory_id=new_id, updated_claims=len(affected), repeated=False)

    return app


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
