"""
YouTube 學習筆記助手 - LINE Bot (部署到 Render)

LINE 指令：
  監控 <頻道網址或名稱>   -> 把頻道加入監控清單
  清單                    -> 列出目前監控的頻道
  取消監控 <頻道網址或名稱> -> 移除監控
  查詢                    -> 檢查所有監控頻道是否有新影片
  轉錄 <影片網址或ID>      -> 抓字幕/轉逐字稿，整理成筆記回傳

監控清單存在 Upstash Redis（外部、免費、不會過期），不受 Render 免費方案
「重新部署/休眠重啟會清空本機磁碟」的限制；本機開發沒設定 REDIS_URL 時，
會自動退回用本機 watch_data.json 檔案（單純方便本機測試，部署到 Render 時
請務必設定 REDIS_URL）。
"""

import json
import os
import re
import sys
import threading
import urllib.parse
from datetime import date, timedelta

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest,
    PushMessageRequest, TextMessage, QuickReply, QuickReplyItem, MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from yt_notes_assistant import (  # noqa: E402
    is_url, is_channel_url, extract_video_id, resolve_video, resolve_channel,
    list_channel_videos_since, process_video, process_external_url,
)

LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CRON_SECRET = os.environ.get("CRON_SECRET")  # 用來保護 /check_all，避免外部任意呼叫

LINE_TEXT_LIMIT = 4500  # LINE 單則訊息上限約 5000 字，留一點緩衝

# 等待使用者回答頻道網址/名稱的對話狀態：user_id -> "watch" | "unwatch" | "transcribe"。
# 只存在記憶體裡（Render 免費方案是單一 worker，不會有多個 process 互相看不到的問題；
# 重新部署或重啟會清空，使用者只需要重新點一次按鈕即可，影響很小）。
pending_action: dict[str, str] = {}

app = Flask(__name__)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)


# ---------- 監控清單儲存 ----------
#
# 兩種後端，介面統一是這四個函式：
#   get_watched_channels(user_id) -> {channel_query: since_date}
#   add_watched_channel(user_id, channel_query, since_date)        # 新增或更新 since_date
#   remove_watched_channel(user_id, channel_query) -> bool          # 回傳原本是否存在
#   get_all_watched_channels() -> {user_id: {channel_query: since_date}}
#
# 有設定 REDIS_URL 時用 Upstash Redis（外部、免費額度不過期，不受 Render 重新部署/
# 休眠重啟清空本機磁碟的影響）；每個 Redis 指令本身就是原子操作，不需要額外上鎖。
# 沒有設定時退回本機 JSON 檔，方便本機開發測試（但部署到 Render 沒接 Redis 的話，
# 監控清單還是會在重新部署或長時間休眠後消失）。

REDIS_URL = os.environ.get("REDIS_URL")


def _normalize_channel_query(q: str) -> str:
    """『取消監控』比對容錯：使用者貼網址時常見的編碼/大小寫/結尾斜線差異
    （例如瀏覽器網址列複製出來的是解碼後的中文，但當初輸入監控時是 %xx 編碼形式），
    直接比對原始字串容易找不到要刪除的項目，導致清單刪不掉。"""
    try:
        q = urllib.parse.unquote(q)
    except Exception:
        pass
    return q.strip().rstrip("/").casefold()


if REDIS_URL:
    import redis

    _redis = redis.from_url(REDIS_URL, decode_responses=True)
    _USERS_SET_KEY = "watch:users"

    def _channels_key(user_id: str) -> str:
        return f"watch:{user_id}:channels"

    def get_watched_channels(user_id: str) -> dict:
        return _redis.hgetall(_channels_key(user_id))

    def add_watched_channel(user_id: str, channel_query: str, since_date: str):
        _redis.hset(_channels_key(user_id), channel_query, since_date)
        _redis.sadd(_USERS_SET_KEY, user_id)

    def remove_watched_channel(user_id: str, channel_query: str) -> bool:
        key = _channels_key(user_id)
        if _redis.hdel(key, channel_query) > 0:
            return True
        target = _normalize_channel_query(channel_query)
        for stored_query in _redis.hkeys(key):
            if _normalize_channel_query(stored_query) == target:
                return _redis.hdel(key, stored_query) > 0
        return False

    def get_all_watched_channels() -> dict:
        user_ids = _redis.smembers(_USERS_SET_KEY)
        return {uid: _redis.hgetall(_channels_key(uid)) for uid in user_ids}

else:
    DATA_PATH = os.path.join(os.path.dirname(__file__), "watch_data.json")
    # 保護 watch_data.json 的讀寫：每個指令都在背景執行緒處理，若連續快速傳多個監控請求，
    # 兩個執行緒可能幾乎同時「讀檔→修改→存檔」，較晚存檔的會用舊資料整批覆蓋掉，
    # 導致前面加入的頻道被蓋掉、清單只剩最後一筆。用鎖把讀寫過程串行化。
    _data_lock = threading.Lock()

    def _load_json() -> dict:
        if not os.path.exists(DATA_PATH):
            return {}
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_json(data: dict):
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_watched_channels(user_id: str) -> dict:
        with _data_lock:
            data = _load_json()
            return dict(data.get(user_id, {}).get("channels", {}))

    def add_watched_channel(user_id: str, channel_query: str, since_date: str):
        with _data_lock:
            data = _load_json()
            data.setdefault(user_id, {"channels": {}})["channels"][channel_query] = since_date
            _save_json(data)

    def remove_watched_channel(user_id: str, channel_query: str) -> bool:
        with _data_lock:
            data = _load_json()
            record = data.setdefault(user_id, {"channels": {}})
            if record["channels"].pop(channel_query, None) is not None:
                _save_json(data)
                return True
            target = _normalize_channel_query(channel_query)
            for stored_query in list(record["channels"]):
                if _normalize_channel_query(stored_query) == target:
                    del record["channels"][stored_query]
                    _save_json(data)
                    return True
            return False

    def get_all_watched_channels() -> dict:
        with _data_lock:
            data = _load_json()
            return {uid: dict(rec.get("channels", {})) for uid, rec in data.items()}


# ---------- LINE 訊息輔助 ----------

# 主選單快速回覆按鈕：點擊等同直接輸入該指令文字，文字輸入仍照常可用，兩者並存。
MAIN_MENU_QUICK_REPLY = QuickReply(items=[
    QuickReplyItem(action=MessageAction(label="監控", text="監控")),
    QuickReplyItem(action=MessageAction(label="取消監控", text="取消監控")),
    QuickReplyItem(action=MessageAction(label="清單", text="清單")),
    QuickReplyItem(action=MessageAction(label="查詢", text="查詢")),
    QuickReplyItem(action=MessageAction(label="轉錄", text="轉錄")),
])


def push_text(user_id: str, text: str, quick_reply: QuickReply | None = MAIN_MENU_QUICK_REPLY):
    """quick_reply 預設附上主選單按鈕，只會附在最後一段（LINE 慣例：按鈕跟在這輪對話的最後一則訊息上）。"""
    chunks = [text[i:i + LINE_TEXT_LIMIT] for i in range(0, len(text), LINE_TEXT_LIMIT)] or [""]
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            message = TextMessage(text=chunk, quickReply=quick_reply if is_last else None)
            api.push_message(PushMessageRequest(to=user_id, messages=[message]))


def reply_text(reply_token: str, text: str, quick_reply: QuickReply | None = None):
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(ReplyMessageRequest(
            replyToken=reply_token,
            messages=[TextMessage(text=text[:LINE_TEXT_LIMIT], quickReply=quick_reply)],
        ))


def push_new_video_notice(user_id: str, channel_name: str, video: dict):
    """通知有新影片，並附上「產生逐字稿」快速回覆按鈕，點擊後等同輸入「轉錄 <video_id>」。"""
    text = f"【{channel_name}】有新影片：\n{video.get('upload_date', '')} {video['title']}\n{video['url']}"
    quick_reply = QuickReply(items=[
        QuickReplyItem(action=MessageAction(label="產生逐字稿", text=f"轉錄 {video['id']}")),
    ])
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.push_message(PushMessageRequest(
            to=user_id,
            messages=[TextMessage(text=text[:LINE_TEXT_LIMIT], quickReply=quick_reply)],
        ))


# ---------- 指令處理（耗時工作丟到背景執行緒，用 push 回覆）----------

# 「監控 <頻道> <YYYY-MM-DD>」結尾可選帶一個起始日期，蓋掉預設的「今天」。
# 主要用途：重新監控已在清單裡的頻道、把 since_date 往回調，找回因為
# 之前查詢靜默失敗（或單純太久沒查）而被跳過的舊影片。
WATCH_DATE_SUFFIX_RE = re.compile(r"^(.*\S)\s+(\d{4}-\d{2}-\d{2})$")


def _split_watch_query(text: str) -> tuple[str, str]:
    """回傳 (channel_query, since_date)；沒帶日期或日期格式不合法則 since_date 為今天。"""
    m = WATCH_DATE_SUFFIX_RE.match(text.strip())
    if not m:
        return text.strip(), date.today().isoformat()
    channel_part, date_part = m.group(1), m.group(2)
    try:
        date.fromisoformat(date_part)
    except ValueError:
        return text.strip(), date.today().isoformat()
    return channel_part, date_part


def handle_watch(user_id: str, query: str):
    query, since_date = _split_watch_query(query)
    try:
        _, channel_name = resolve_channel(query)
    except Exception as e:
        push_text(user_id, f"找不到頻道「{query}」：{e}\n請確認頻道網址或名稱是否正確。")
        return

    add_watched_channel(user_id, query, since_date)
    push_text(user_id, f"已加入監控：{channel_name}\n（自 {since_date} 起的新影片才會通知）")


def handle_unwatch(user_id: str, query: str):
    if remove_watched_channel(user_id, query):
        push_text(user_id, f"已取消監控：{query}")
    else:
        push_text(user_id, f"監控清單裡沒有找到：{query}")


def handle_list(user_id: str):
    channels = get_watched_channels(user_id)
    if not channels:
        push_text(user_id, "目前沒有監控任何頻道，輸入「監控 <頻道網址或名稱>」開始監控。")
        return
    lines = ["目前監控中的頻道："]
    for ch, since in channels.items():
        lines.append(f"- {ch}（自 {since} 起）")
    push_text(user_id, "\n".join(lines))


def handle_check(user_id: str):
    channels_snapshot = get_watched_channels(user_id)
    if not channels_snapshot:
        push_text(user_id, "目前沒有監控任何頻道，輸入「監控 <頻道網址或名稱>」開始監控。")
        return

    today = date.today().isoformat()
    any_new = False
    for channel_query, since_date in channels_snapshot.items():
        try:
            channel_name, videos = list_channel_videos_since(channel_query, since_date)
        except Exception as e:
            push_text(user_id, f"查詢「{channel_query}」失敗：{e}")
            continue

        if videos:
            any_new = True
            for v in videos:
                push_new_video_notice(user_id, channel_name, v)
        add_watched_channel(user_id, channel_query, today)

    if not any_new:
        push_text(user_id, "目前監控的頻道都沒有新影片。")


def check_all_users():
    """供排程（cron）呼叫：檢查所有使用者監控的頻道，主動推播有新影片的通知（含「產生逐字稿」按鈕）。
    沒有新影片時不主動打擾使用者。"""
    today = date.today().isoformat()
    for user_id, channels in get_all_watched_channels().items():
        for channel_query, since_date in channels.items():
            try:
                channel_name, videos = list_channel_videos_since(channel_query, since_date)
            except Exception as e:
                print(f"查詢「{channel_query}」失敗：{e}", file=sys.stderr)
                continue
            for v in videos:
                push_new_video_notice(user_id, channel_name, v)
            add_watched_channel(user_id, channel_query, today)


HELP_TEXT = (
    "可用指令（也可以直接點下方按鈕）：\n"
    "監控 <頻道網址或名稱> [起始日期 YYYY-MM-DD]\n"
    "取消監控 <頻道網址或名稱>\n"
    "清單\n"
    "查詢\n"
    "轉錄 <影片網址或ID>"
)


def handle_maybe_channel(user_id: str, text: str):
    """沒比對到任何指令、也沒有待處理動作時，主動判斷這句話是不是頻道網址：
    是的話直接當成監控請求處理；不是的話顯示可用指令。

    只接受看起來像 YouTube 頻道網址的輸入（is_channel_url），不對純文字做模糊名稱搜尋——
    實測發現 resolve_channel() 的搜尋備援對任意文字幾乎都「找得到」某個頻道
    （例如打「謝謝」會配對到完全不相關的頻道），若不限制，幾乎任何聊天訊息都會被
    誤判成監控請求。"""
    if not is_channel_url(text):
        push_text(user_id, HELP_TEXT)
        return
    try:
        resolve_channel(text)
    except Exception:
        push_text(user_id, f"「{text}」看起來不是有效的頻道網址。\n\n{HELP_TEXT}")
        return
    handle_watch(user_id, text)


def handle_transcribe(user_id: str, query: str):
    push_text(user_id, f"收到，開始處理：{query}\n（轉逐字稿可能需要幾分鐘，完成後會再通知你）")
    try:
        if is_url(query) and extract_video_id(query) is None:
            path = process_external_url(query, output_dir="/tmp")
        else:
            video_id, title = resolve_video(query)
            path = process_video(video_id, title, output_dir="/tmp")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        push_text(user_id, content)
    except Exception as e:
        push_text(user_id, f"處理失敗：{e}")


def run_safely(func, *args):
    """背景執行緒的進入點：捕捉任何沒被各 handler 自己處理掉的例外
    （例如 Redis 連線問題），回報錯誤給使用者，而不是讓執行緒悄悄死掉、
    使用者只看到「處理中」之後完全沒有下文。"""
    user_id = args[0]
    try:
        func(*args)
    except Exception as e:
        push_text(user_id, f"處理時發生錯誤：{e}")


def dispatch(user_id: str, text: str, reply_token: str):
    text = text.strip()

    if text in ("查詢", "check"):
        pending_action.pop(user_id, None)
        reply_text(reply_token, "好，查詢中...")
        threading.Thread(target=run_safely, args=(handle_check, user_id,), daemon=True).start()
        return
    if text in ("清單", "list"):
        pending_action.pop(user_id, None)
        reply_text(reply_token, "查詢中...")
        threading.Thread(target=run_safely, args=(handle_list, user_id,), daemon=True).start()
        return
    if text.startswith("監控"):
        query = text[len("監控"):].strip()
        if not query:
            pending_action[user_id] = "watch"
            today_str = date.today().isoformat()
            example_str = (date.today() - timedelta(days=7)).isoformat()
            reply_text(
                reply_token,
                "請輸入要監控的頻道網址或名稱：\n"
                f"（預設從今天 {today_str} 起算新影片；"
                "如果要從更早的日期開始算，可以在後面加上日期，例如：\n"
                f"頻道網址或名稱 {example_str}）",
            )
            return
        pending_action.pop(user_id, None)
        reply_text(reply_token, "處理中...")
        threading.Thread(target=run_safely, args=(handle_watch, user_id, query), daemon=True).start()
        return
    if text.startswith("取消監控"):
        query = text[len("取消監控"):].strip()
        if not query:
            pending_action[user_id] = "unwatch"
            reply_text(reply_token, "請輸入要取消監控的頻道網址或名稱：")
            return
        pending_action.pop(user_id, None)
        reply_text(reply_token, "處理中...")
        threading.Thread(target=run_safely, args=(handle_unwatch, user_id, query), daemon=True).start()
        return
    if text.startswith("轉錄"):
        query = text[len("轉錄"):].strip()
        if not query:
            pending_action[user_id] = "transcribe"
            reply_text(reply_token, "請輸入要轉錄的影片網址或 ID：")
            return
        pending_action.pop(user_id, None)
        threading.Thread(target=run_safely, args=(handle_transcribe, user_id, query), daemon=True).start()
        return

    # 不是已知指令：如果使用者前一句是按按鈕／打指令但沒帶參數，這句就當作補上的參數。
    pending = pending_action.pop(user_id, None)
    if pending == "watch":
        reply_text(reply_token, "處理中...")
        threading.Thread(target=run_safely, args=(handle_watch, user_id, text), daemon=True).start()
        return
    if pending == "unwatch":
        reply_text(reply_token, "處理中...")
        threading.Thread(target=run_safely, args=(handle_unwatch, user_id, text), daemon=True).start()
        return
    if pending == "transcribe":
        threading.Thread(target=run_safely, args=(handle_transcribe, user_id, text), daemon=True).start()
        return

    # 沒有待處理動作、也不是已知指令：主動判斷這句話是不是頻道名稱/網址。
    reply_text(reply_token, "處理中...")
    threading.Thread(target=run_safely, args=(handle_maybe_channel, user_id, text), daemon=True).start()


@handler.add(MessageEvent, message=TextMessageContent)
def on_message(event):
    dispatch(event.source.user_id, event.message.text, event.reply_token)


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@app.route("/", methods=["GET"])
def health():
    return "ok"


@app.route("/check_all", methods=["POST", "GET"])
def check_all():
    """給排程服務（例如 Render Cron Job）定時呼叫的端點，主動檢查所有人監控的頻道。
    用 CRON_SECRET 驗證呼叫者，避免任何人都能打這個端點觸發大量查詢。"""
    if not CRON_SECRET or request.args.get("secret") != CRON_SECRET:
        abort(403)
    threading.Thread(target=check_all_users, daemon=True).start()
    return "started"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
