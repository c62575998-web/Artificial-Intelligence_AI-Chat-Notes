import uuid

import pytest

from app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.setenv("CHAT_NOTES_DB", str(tmp_path / "test.sqlite3"))
    return create_app().test_client()


def post(client, path, data):
    return client.post(path, json=data)


def make_note(client):
    conversation = post(client, "/api/conversations", {}).json["id"]
    message = "主題：看展；人物：小安；日期：2026-04-10；地點：展館入口"
    assert post(client, f"/api/conversations/{conversation}/messages", {"text": message}).status_code == 201
    result = post(client, f"/api/conversations/{conversation}/organize", {})
    assert result.status_code == 200
    assert len(result.json["created"]) == 3
    note = client.get("/api/notes").json[0]
    date_claim = next(c for c in note["claims"] if "日期" in c["sentence"])
    return conversation, note, date_claim


def test_organize_search_and_sources(client):
    conversation, note, claim = make_note(client)
    assert "2026-04-10" in claim["sentence"]
    sources = client.get(f"/api/claims/{claim['id']}/sources").json
    assert len(sources) == 1
    assert sources[0]["text"].startswith("主題：看展")
    assert any(m["relation"] == "日期" for m in client.get("/api/search?q=看展日期").json)
    assert post(client, f"/api/conversations/{conversation}/organize", {}).json["created"] == []


def test_preview_cancel_and_update_all_direct_claims(client):
    _, note, claim = make_note(client)
    memories = client.get("/api/memories").json
    date_memory = next(m for m in memories if m["relation"] == "日期")
    assert post(client, "/api/notes", {"title": "本月活動", "memory_ids": [date_memory["id"]]}).status_code == 201
    draft = {"claim_id": claim["id"], "new_value": "2026-04-12", "kind": "change", "effective_from": "2026-04-04", "reason": "日期改為 4 月 12 日"}
    preview = post(client, "/api/revisions/preview", draft).json
    assert len(preview["affected"]) == 2
    assert client.delete("/api/revisions/preview/" + preview["preview_id"]).status_code == 200
    assert any("2026-04-10" in c["sentence"] for n in client.get("/api/notes").json for c in n["claims"])
    preview = post(client, "/api/revisions/preview", draft).json
    request_id = uuid.uuid4().hex
    result = post(client, "/api/revisions/confirm", {"preview_id": preview["preview_id"], "request_id": request_id})
    assert result.status_code == 200
    assert result.json["updated_claims"] == 2
    assert post(client, "/api/revisions/confirm", {"preview_id": preview["preview_id"], "request_id": request_id}).json["repeated"]
    notes = client.get("/api/notes").json
    assert sum("2026-04-12" in c["sentence"] for n in notes for c in n["claims"]) == 2
    assert any("小安" in c["sentence"] for n in notes for c in n["claims"])
    old = next(m for m in client.get("/api/memories").json if m["id"] == date_memory["id"])
    assert old["status"] == "superseded"
    assert any(m["value"] == "2026-04-10" for m in client.get("/api/search?q=看展日期&as_of=2026-04-03").json)
    assert any(m["value"] == "2026-04-12" for m in client.get("/api/search?q=看展日期&as_of=2026-04-05").json)


def test_correction_removes_wrong_version_and_conflict(client):
    _, _, claim = make_note(client)
    base = {"claim_id": claim["id"], "new_value": "2026-04-11", "kind": "correction", "reason": "原訊息日期記錯"}
    first = post(client, "/api/revisions/preview", base).json
    second = post(client, "/api/revisions/preview", base | {"new_value": "2026-04-12"}).json
    assert post(client, "/api/revisions/confirm", {"preview_id": first["preview_id"], "request_id": "r1"}).status_code == 200
    assert post(client, "/api/revisions/confirm", {"preview_id": second["preview_id"], "request_id": "r2"}).status_code == 409
    memories = client.get("/api/memories").json
    assert any(m["value"] == "2026-04-10" and m["status"] == "error" for m in memories)
    assert all(m["value"] != "2026-04-10" for m in client.get("/api/search?q=看展日期").json)


def test_failed_search_write_rolls_back_revision(client, monkeypatch):
    import app

    _, _, claim = make_note(client)
    preview = post(client, "/api/revisions/preview", {"claim_id": claim["id"], "new_value": "2026-04-12", "kind": "correction", "reason": "日期原本記錯"}).json
    before_notes = client.get("/api/notes").json
    before_memories = client.get("/api/memories").json

    def fail(*args):
        raise RuntimeError("simulated search failure")

    monkeypatch.setattr(app, "save_search", fail)
    response = post(client, "/api/revisions/confirm", {"preview_id": preview["preview_id"], "request_id": "failed"})
    assert response.status_code == 500
    assert client.get("/api/notes").json == before_notes
    assert client.get("/api/memories").json == before_memories


def test_model_can_extract_unstructured_message(client, monkeypatch):
    import app

    monkeypatch.setattr(app, "model_completion", lambda system, user: '[{"topic":"看展","relation":"日期","value":"2026-04-10"}]')
    conversation = post(client, "/api/conversations", {}).json["id"]
    assert post(client, f"/api/conversations/{conversation}/messages", {"text": "和小安約了下週看展"}).status_code == 201
    result = post(client, f"/api/conversations/{conversation}/organize", {})
    assert len(result.json["created"]) == 1


def test_conflicting_new_message_requires_explicit_revision(client):
    conversation, _, _ = make_note(client)
    post(client, f"/api/conversations/{conversation}/messages", {"text": "主題：看展；日期：2026-04-12"})
    result = post(client, f"/api/conversations/{conversation}/organize", {})
    assert result.json["created"] == []
    assert len(result.json["skipped"]) == 1
    assert len([m for m in client.get("/api/memories").json if m["relation"] == "日期"]) == 1


def test_user_message_survives_model_outage(client, monkeypatch):
    import httpx
    import app

    conversation = post(client, "/api/conversations", {}).json["id"]

    def unavailable(*args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(app, "model_completion", unavailable)
    response = post(client, f"/api/conversations/{conversation}/messages", {"text": "今天去看展"})
    assert response.status_code == 502
    messages = client.get(f"/api/conversations/{conversation}/messages").json
    assert len(messages) == 1 and messages[0]["text"] == "今天去看展"
