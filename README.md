# 對話記憶與主題筆記

依「具來源追溯與修正機制的對話記憶與主題筆記系統」提案製作的可操作原型。單一 Flask 程序提供繁體中文網頁與 API；SQLite 保存原始訊息、記憶版本、筆記句子及來源。搜尋使用 jieba 分詞的 BM25；設定 Embedding API 後也會計算向量相似度，使用 RRF 融合排序。

## 啟動

需要 Python 3.12。雲端環境已有 `/workspace/.venvs/ai-chat-notes`，可直接使用：

```bash
cd /workspace/Artificial-Intelligence_AI-Chat-Notes
/workspace/.venvs/ai-chat-notes/bin/python app.py
```

在其他環境安裝：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

開啟 `http://127.0.0.1:5000`。資料庫預設放在目前工作目錄的 `chat_notes.sqlite3`；可用 `CHAT_NOTES_DB` 指定其他位置。測試：

```bash
/workspace/.venvs/ai-chat-notes/bin/pytest -q
```

首次使用可送出 `主題：看展；人物：小安；日期：2026-04-10；地點：展館入口`，再按「整理筆記」。選取筆記句子可查看原話、預覽修正、取消或確認。若同一項記憶被兩份筆記直接引用，確認後會一起更新。沒有模型設定時，系統可用此明確欄位格式整理，並以已儲存的事實回答簡單查詢。

## 模型設定

若使用相容 OpenAI Chat Completions／Embeddings 的服務，可在執行程序設定 `MODEL_BASE_URL`（以 `/v1` 結尾）、`MODEL_API_KEY`、`CHAT_MODEL` 與 `EMBEDDING_MODEL`。模型金鑰不要放進儲存庫。僅設定前三項可啟用自然聊天與非結構化訊息擷取；四項齊全才啟用向量檢索。未設定時仍可離線演示資料流程。模型輸出的事實會經欄位檢查，但語意正確性仍需人工核對。

## 目前範圍

已實作：訊息保存、明確欄位或模型擷取、累積筆記、來源查閱、詞彙與選用向量檢索、錯誤更正與狀態改變、歷史版本、修正預覽與取消、直接引用筆記同步提交，以及 `request_id` 重送去重。

尚待研究階段完成：提案中的 12 組開發、36 組正式與 10 組探索案例資料與評分；每週／每月重複規則；完整模型提示驗證、來源語意核對、四題追問與 B／C／D 比較。目前自然語句的事實擷取需要外部模型；離線模式採明確欄位輸入。這些能力尚不能宣稱通過提案的正式驗收。
