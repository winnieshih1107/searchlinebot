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

### 按鈕（兩種，可以跟手動輸入並存）

- **Quick Reply**：大部分回覆訊息下方都會附上「監控／取消監控／清單／查詢／轉錄」5 個按鈕，貼在單一則訊息上，使用者再傳下一句話就會消失。點按鈕等同直接打出該指令文字，純文字輸入完全不受影響。
- **Rich Menu**（常駐選單，選用）：跟 Quick Reply 不同，Rich Menu 釘在整個聊天室下方，不管什麼時候打開聊天室都看得到。設定方式見下方「Rich Menu 設定」。

點「監控」「取消監控」「轉錄」這幾個需要參數的按鈕後，Bot 會先回覆提示訊息（例如「請輸入要監控的頻道網址或名稱：」），並記住這個使用者正在等哪個指令的輸入；下一句話不管打什麼，都會直接當成該指令的參數處理，不需要重新打一次指令文字。

另外，即使完全沒打任何指令、也沒有正在等待輸入，只要訊息內容是 YouTube 頻道網址（`youtube.com/@...`、`/channel/...` 等），Bot 會自動判斷並當成監控請求處理。

---

## 架構

- **`video_text/line_bot/app.py`** — Flask + [line-bot-sdk](https://github.com/line/line-bot-sdk-python) v3 寫的 webhook server，是實際部署的 LINE Bot 本體。
  - `POST /callback`：LINE 平台的 webhook 進入點。
  - `POST /check_all?secret=<CRON_SECRET>`：給外部排程服務呼叫，定時檢查所有使用者監控的頻道並主動推播。
  - 監控清單預設存在 Upstash Redis（設定 `REDIS_URL` 才會啟用，見下方「監控清單持久化」），每個
    LINE 使用者各自一份 channel 清單；沒設定 `REDIS_URL` 時退回本機 `watch_data.json`，方便本機開發。
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
   - `REDIS_URL`（監控清單的永久儲存，見下方「監控清單持久化」）
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

### 監控清單持久化（REDIS_URL）

Render 免費方案的本機磁碟在重新部署或長時間休眠重啟後會被清空，監控清單如果只存在本機
`watch_data.json`，每次部署都會不見。解法是接一個外部、免費額度不過期的 [Upstash](https://upstash.com)
Redis：

1. 到 [upstash.com](https://upstash.com) 免費註冊，建立一個 Redis database（選免費方案）。
2. 在該 database 的詳細頁面找到連線字串，格式類似：
   ```
   rediss://default:<password>@<host>:<port>
   ```
3. 到 Render 後台 → Environment 頁籤，新增 `REDIS_URL`，值貼上面那串連線字串，存檔讓它重新部署。
4. 部署完成後，監控清單就會存在 Upstash，不會再因為 Render 重新部署/休眠重啟而消失。

沒有設定 `REDIS_URL` 時，程式會自動退回用本機 `watch_data.json`（方便本機開發測試），但部署到
Render 上沒接 Redis 的話，監控清單還是不保證永久保存。

---

## Rich Menu 設定（選用）

`video_text/line_bot/setup_rich_menu.py` 是一支**一次性腳本**，用來建立常駐在聊天室下方的
Rich Menu（5 個按鈕：監控／取消監控／清單／查詢／轉錄）。只需要在自己電腦上執行一次，不用
跟著 Render 部署：

```bash
cd video_text/line_bot
pip install pillow requests
export LINE_CHANNEL_ACCESS_TOKEN=...
python setup_rich_menu.py
```

腳本會自動找電腦上的中文字型畫按鈕圖片（Windows 預設找微軟正黑體），呼叫 LINE API 建立、上傳、
並設成所有使用者的預設選單；之後想換按鈕文字或版面，改檔案內容重新執行一次即可，舊選單會自動
清掉，不會在帳號裡越疊越多。找不到中文字型的話可以用參數指定字型檔路徑：

```bash
python setup_rich_menu.py "C:\Windows\Fonts\msjh.ttc"
```

---

## 本機開發

```bash
cd video_text/line_bot
pip install -r requirements.txt
export LINE_CHANNEL_ACCESS_TOKEN=...
export LINE_CHANNEL_SECRET=...
export CRON_SECRET=...
export REDIS_URL=...   # 選用，沒設定會用本機 watch_data.json
python app.py
```

本機測試 webhook 可搭配 [ngrok](https://ngrok.com/) 之類的工具把 `http://localhost:10000/callback` 暴露成公開網址，貼到 LINE Developers Console 的 Webhook URL。
