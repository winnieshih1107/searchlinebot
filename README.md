# YouTube 學習筆記 LINE Bot

一個 LINE 聊天機器人：監控你關注的 YouTube 頻道有沒有新影片，並把影片（或任何 yt-dlp 支援的音訊/影音連結）自動整理成 Markdown 學習筆記，直接回傳到 LINE 對話裡。

**完全不需要任何 LLM API key**，純粹用開源工具拼起來：

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：解析網址、用名稱搜尋影片、列出頻道影片、下載音訊
- [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api)：抓取 YouTube 官方字幕
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)：影片沒有官方字幕時，本機語音辨識當備案
- [jieba](https://github.com/fxsjy/jieba)：中文斷詞，用詞頻統計做重點句評分（規則式摘要，不是 LLM 摘要）

---

## 可用指令

在 LINE 裡直接輸入文字指令：

| 指令 | 說明 |
|------|------|
| `監控 <頻道網址或名稱>` | 把頻道加入監控清單，從今天起有新影片就會主動推播通知 |
| `清單` | 列出目前監控中的頻道 |
| `取消監控 <頻道網址或名稱>` | 移除監控 |
| `查詢` | 手動立即檢查所有監控頻道是否有新影片 |
| `轉錄 <影片網址或ID>` | 抓字幕/語音辨識，整理成學習筆記後直接回傳文字 |

收到新影片通知時，訊息會附上「產生逐字稿」快速回覆按鈕，點一下就等同輸入 `轉錄 <video_id>`。

---

## 架構

- **`video_text/line_bot/app.py`** — Flask + [line-bot-sdk](https://github.com/line/line-bot-sdk-python) v3 寫的 webhook server，是實際部署的 LINE Bot 本體。
  - `POST /callback`：LINE 平台的 webhook 進入點。
  - `POST /check_all?secret=<CRON_SECRET>`：給外部排程服務呼叫，定時檢查所有使用者監控的頻道並主動推播。
  - 監控清單存在 `video_text/line_bot/watch_data.json`（每個 LINE 使用者各自一份 channel 清單）。
  - 抓字幕/轉錄等耗時工作丟到背景執行緒處理，完成後用 `push_message` 把結果推回去，避免 LINE webhook 逾時。
- **`video_text/yt_notes_assistant.py`** — 核心邏輯（被 `app.py` import），也可以單獨當 CLI 工具跑：解析頻道/影片、抓字幕、Whisper 備援轉錄、jieba 關鍵字與重點摘要、輸出 Markdown 筆記。
- **`video_text/yt_notes_gui.py`** — 同一套核心邏輯的桌面版 tkinter GUI，可勾選頻道影片清單批次產生筆記、匯出 Excel。
- **`video_text/podcast_notes_assistant.py` / `podcast_notes_gui.py`** — 同樣概念但處理 Podcast（RSS feed / SoundCloud / Spotify 等），目前是獨立 CLI / GUI 工具，尚未接進 LINE Bot。

---

## 部署（Render）

專案根目錄的 [`render.yaml`](render.yaml) 已設定好：

```
buildCommand: pip install -r video_text/line_bot/requirements.txt
startCommand: gunicorn --chdir video_text/line_bot --timeout 120 app:app
```

部署步驟：

1. 在 [Render](https://render.com) 用這個 GitHub repo 建立 Web Service（會自動讀到 `render.yaml`）。
2. 設定環境變數：
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_CHANNEL_SECRET`
   - `CRON_SECRET`（自訂一個密碼字串，保護 `/check_all` 端點不被任意呼叫）
3. 到 [LINE Developers Console](https://developers.line.biz/) 把 Webhook URL 設成 `https://<你的服務網址>/callback`。
4. Render 免費方案沒有內建排程服務，改用外部免費排程（例如 [cron-job.org](https://cron-job.org)）定時打：
   ```
   POST https://<你的服務網址>/check_all?secret=<CRON_SECRET>
   ```
5. **再加一個 keep-alive 排程**：Render 免費方案閒置 15 分鐘會自動休眠，下次請求進來要「冷啟動」
   （重新載入 Python、jieba 字典、faster-whisper 等套件），實測冷啟動超過 30 秒，常常會讓
   LINE 的 reply token 在 Bot 處理完之前就過期，使用者就會看到「處理中」後完全沒反應。
   在 cron-job.org 再排一個排程，每 10 分鐘打一次健康檢查端點，讓服務不會閒置太久進入休眠：
   ```
   GET https://<你的服務網址>/
   ```

> ⚠️ Render 免費方案的本機磁碟在重新部署或長時間休眠重啟後可能會被清空，監控清單（`watch_data.json`）因此不保證永久保存。若需要長期保存，建議改存到外部資料庫（例如 Render 自家的免費 PostgreSQL）。

---

## 本機開發

```bash
cd video_text/line_bot
pip install -r requirements.txt
export LINE_CHANNEL_ACCESS_TOKEN=...
export LINE_CHANNEL_SECRET=...
export CRON_SECRET=...
python app.py
```

本機測試 webhook 可搭配 [ngrok](https://ngrok.com/) 之類的工具把 `http://localhost:10000/callback` 暴露成公開網址，貼到 LINE Developers Console 的 Webhook URL。
