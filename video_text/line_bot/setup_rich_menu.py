"""
一次性腳本：建立並設定 LINE Bot 的 Rich Menu（聊天室下方常駐按鈕列）。

跟程式裡的 Quick Reply 不一樣：Quick Reply 貼在某一則訊息下面，使用者再傳
一句話就會消失；Rich Menu 是釘在整個聊天室下方，不管什麼時候打開聊天室都會
看到，這支腳本就是用來建立、上傳圖片、並設成所有使用者的預設選單。

只需要在自己電腦上執行一次，不用跟著 Render 部署：

    pip install pillow requests
    set LINE_CHANNEL_ACCESS_TOKEN=你的token        (PowerShell: $env:LINE_CHANNEL_ACCESS_TOKEN="...")
    python setup_rich_menu.py

執行時會自動找電腦上的中文字型；如果找不到，可以用第一個參數指定字型檔路徑：

    python setup_rich_menu.py "C:\\Windows\\Fonts\\msjh.ttc"

之後如果想換按鈕文字或版面，改這個檔案重新執行一次即可（會自動刪掉舊的預設
Rich Menu，不會在帳號裡越疊越多）。
"""

import os
import sys
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
if not ACCESS_TOKEN:
    sys.exit("請先設定環境變數 LINE_CHANNEL_ACCESS_TOKEN")

API_BASE = "https://api.line.me/v2/bot"
DATA_API_BASE = "https://api-data.line.me/v2/bot"
HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

WIDTH, HEIGHT = 2500, 843
BUTTONS = ["監控", "取消監控", "清單", "查詢", "轉錄"]
COL_WIDTH = WIDTH // len(BUTTONS)

# 常見的中文字型路徑，依平台找第一個存在的來用
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msjh.ttc",        # Microsoft JhengHei（繁中，Windows 內建）
    r"C:\Windows\Fonts\msjhbd.ttc",
    r"C:\Windows\Fonts\mingliu.ttc",
    "/System/Library/Fonts/PingFang.ttc",            # macOS
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux
]


def find_font(size: int, override_path: str | None) -> ImageFont.FreeTypeFont:
    candidates = [override_path] if override_path else FONT_CANDIDATES
    for path in candidates:
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size)
    sys.exit(
        "找不到可顯示中文的字型，請用參數指定字型檔路徑，例如：\n"
        r'  python setup_rich_menu.py "C:\Windows\Fonts\msjh.ttc"'
    )


def build_image(font_path: str | None) -> Image.Image:
    font = find_font(100, font_path)
    img = Image.new("RGB", (WIDTH, HEIGHT), "#182236")
    draw = ImageDraw.Draw(img)
    for i, label in enumerate(BUTTONS):
        x0 = i * COL_WIDTH
        if i > 0:
            draw.line([(x0, 0), (x0, HEIGHT)], fill="#2d4a7a", width=4)
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((x0 + (COL_WIDTH - tw) / 2, (HEIGHT - th) / 2 - bbox[1]), label, font=font, fill="#ffffff")
    return img


def area(i: int, label: str) -> dict:
    return {
        "bounds": {"x": i * COL_WIDTH, "y": 0, "width": COL_WIDTH, "height": HEIGHT},
        "action": {"type": "message", "label": label, "text": label},
    }


def main():
    font_path = sys.argv[1] if len(sys.argv) > 1 else None

    # 刪除舊的預設 Rich Menu（如果有），避免帳號裡累積一堆用不到的選單
    r = requests.get(f"{API_BASE}/user/all/richmenu", headers=HEADERS)
    if r.status_code == 200:
        old_id = r.json().get("richMenuId")
        if old_id:
            requests.delete(f"{API_BASE}/richmenu/{old_id}", headers=HEADERS)
            print(f"已刪除舊的 Rich Menu：{old_id}")

    body = {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,
        "name": "main-menu",
        "chatBarText": "選單",
        "areas": [area(i, label) for i, label in enumerate(BUTTONS)],
    }
    r = requests.post(f"{API_BASE}/richmenu", headers={**HEADERS, "Content-Type": "application/json"}, json=body)
    r.raise_for_status()
    rich_menu_id = r.json()["richMenuId"]
    print(f"已建立 Rich Menu：{rich_menu_id}")

    img = build_image(font_path)
    buf = BytesIO()
    img.save(buf, format="PNG")
    r = requests.post(
        f"{DATA_API_BASE}/richmenu/{rich_menu_id}/content",
        headers={**HEADERS, "Content-Type": "image/png"},
        data=buf.getvalue(),
    )
    r.raise_for_status()
    print("已上傳選單圖片")

    r = requests.post(f"{API_BASE}/user/all/richmenu/{rich_menu_id}", headers=HEADERS)
    r.raise_for_status()
    print("已設為所有使用者的預設選單，打開聊天室就會看到按鈕了！")


if __name__ == "__main__":
    main()
