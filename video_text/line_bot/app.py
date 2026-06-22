"""
YouTube 學習筆記助手 - LINE Bot (部署到 Render)

LINE 指令：
  監控 <頻道網址或名稱>   -> 把頻道加入監控清單
  清單                    -> 列出目前監控的頻道
  取消監控 <頻道網址或名稱> -> 移除監控
  查詢                    -> 檢查所有監控頻道是否有新影片
  轉錄 <影片網址或ID>      -> 抓字幕/轉逐字稿，整理成筆記回傳

注意：Render 免費方案的本機磁碟在重新部署或長時間休眠重啟後可能會被清空，
監控清單因此不保證永久保存；若需要長期保存，建議改存到外部資料庫
（例如 Render 自家的免費 PostgreSQL）。
"""

import json
import os
import sys
import threading
from datetime import date

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
    is_url, extract_video_id, resolve_video, resolve_channel,
    list_channel_videos_since, process_video, process_external_url,
)

LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CRON_SECRET = os.environ.get("CRON_SECRET")  # 用來保護 /check_all，避免外部任意呼叫

DATA_PATH = os.path.join(os.path.dirname(__file__), "watch_data.json")
LINE_TEXT_LIMIT = 4500  # LINE 單則訊息上限約 5000 字，留一點緩衝

app = Flask(__name__)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)


# ---------- 監控清單儲存 ----------

def load_data() -> dict:
    if not os.path.exists(DATA_PATH):
        return {}
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_data(data: dict):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user_record(data: dict, user_id: str) -> dict:
    return data.setdefault(user_id, {"channels": {}})


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

def handle_watch(user_id: str, query: str):
    try:
        _, channel_name = resolve_channel(query)
    except Exception as e:
        push_text(user_id, f"找不到頻道「{query}」：{e}\n請確認頻道網址或名稱是否正確。")
        return

    data = load_data()
    record = get_user_record(data, user_id)
    record["channels"][query] = date.today().isoformat()
    save_data(data)
    push_text(user_id, f"已加入監控：{channel_name}\n（從今天開始算起的新影片才會通知）")


def handle_unwatch(user_id: str, query: str):
    data = load_data()
    record = get_user_record(data, user_id)
    if record["channels"].pop(query, None) is not None:
        save_data(data)
        push_text(user_id, f"已取消監控：{query}")
    else:
        push_text(user_id, f"監控清單裡沒有找到：{query}")


def handle_list(user_id: str):
    data = load_data()
    record = get_user_record(data, user_id)
    if not record["channels"]:
        push_text(user_id, "目前沒有監控任何頻道，輸入「監控 <頻道網址或名稱>」開始監控。")
        return
    lines = ["目前監控中的頻道："]
    for ch, since in record["channels"].items():
        lines.append(f"- {ch}（自 {since} 起）")
    push_text(user_id, "\n".join(lines))


def handle_check(user_id: str):
    data = load_data()
    record = get_user_record(data, user_id)
    if not record["channels"]:
        push_text(user_id, "目前沒有監控任何頻道，輸入「監控 <頻道網址或名稱>」開始監控。")
        return

    today = date.today().isoformat()
    any_new = False
    for channel_query, since_date in list(record["channels"].items()):
        try:
            channel_name, videos = list_channel_videos_since(channel_query, since_date)
        except Exception as e:
            push_text(user_id, f"查詢「{channel_query}」失敗：{e}")
            continue

        if videos:
            any_new = True
            for v in videos:
                push_new_video_notice(user_id, channel_name, v)
        record["channels"][channel_query] = today

    save_data(data)
    if not any_new:
        push_text(user_id, "目前監控的頻道都沒有新影片。")


def check_all_users():
    """供排程（cron）呼叫：檢查所有使用者監控的頻道，主動推播有新影片的通知（含「產生逐字稿」按鈕）。
    沒有新影片時不主動打擾使用者。"""
    data = load_data()
    today = date.today().isoformat()
    for user_id, record in data.items():
        for channel_query, since_date in list(record.get("channels", {}).items()):
            try:
                channel_name, videos = list_channel_videos_since(channel_query, since_date)
            except Exception as e:
                print(f"查詢「{channel_query}」失敗：{e}", file=sys.stderr)
                continue
            for v in videos:
                push_new_video_notice(user_id, channel_name, v)
            record["channels"][channel_query] = today
    save_data(data)


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


def dispatch(user_id: str, text: str, reply_token: str):
    text = text.strip()

    if text in ("查詢", "check"):
        reply_text(reply_token, "好，查詢中...")
        threading.Thread(target=handle_check, args=(user_id,), daemon=True).start()
    elif text in ("清單", "list"):
        reply_text(reply_token, "查詢中...")
        threading.Thread(target=handle_list, args=(user_id,), daemon=True).start()
    elif text.startswith("監控"):
        query = text[len("監控"):].strip()
        if not query:
            reply_text(reply_token, "請輸入：監控 <頻道網址或名稱>")
            return
        reply_text(reply_token, "處理中...")
        threading.Thread(target=handle_watch, args=(user_id, query), daemon=True).start()
    elif text.startswith("取消監控"):
        query = text[len("取消監控"):].strip()
        if not query:
            reply_text(reply_token, "請輸入：取消監控 <頻道網址或名稱>")
            return
        reply_text(reply_token, "處理中...")
        threading.Thread(target=handle_unwatch, args=(user_id, query), daemon=True).start()
    elif text.startswith("轉錄"):
        query = text[len("轉錄"):].strip()
        if not query:
            reply_text(reply_token, "請輸入：轉錄 <影片網址或ID>")
            return
        threading.Thread(target=handle_transcribe, args=(user_id, query), daemon=True).start()
    else:
        reply_text(
            reply_token,
            "可用指令（也可以直接點下方按鈕）：\n"
            "監控 <頻道網址或名稱>\n"
            "取消監控 <頻道網址或名稱>\n"
            "清單\n"
            "查詢\n"
            "轉錄 <影片網址或ID>",
            quick_reply=MAIN_MENU_QUICK_REPLY,
        )


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
