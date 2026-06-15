"""
demo_flyin.py — 元素飛入效果 demo（僅 slide_00，前 15 秒）

做法：
  1. OpenCV 偵測內容區塊（中等膨脹，保留 3~5 個獨立元素）
  2. 建立乾淨背景（將各元素區域填滿背景色）
  3. 各元素依序以不同方向飛入：
       最寬 & 最高的元素 → fly_top（標題從上滑入）
       圖片類（接近正方形）→ zoom（縮放進場）
       其餘 → 交替 fly_left / fly_right
  4. cubic ease-out 緩動，各元素間隔 0.45s
"""
import os, sys, subprocess
import numpy as np
import cv2
import imageio_ffmpeg

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE   = r"D:\wi\260612"
ASSETS = os.path.join(BASE, "pres_pdf_assets")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FFPROBE = FFMPEG.replace("ffmpeg-win", "ffprobe-win")
if not os.path.exists(FFPROBE):
    FFPROBE = FFMPEG.replace("ffmpeg", "ffprobe")

FPS       = 24
W, H      = 1920, 1080
DEMO_DUR  = 15.0
DEMO_SLIDE = 0        # 改這個數字可以換投影片（0~11）

ANIM_START = 0.20     # 第一個元素出現時間
ELEM_GAP   = 0.50     # 每個元素之間間隔
ELEM_DUR   = 0.45     # 每個元素動畫時長
FADE_IN    = 0.30     # 整體淡入
ZOOM_START = 0.40     # zoom 效果初始縮放比
ZOOM_RANGE = 0.04     # Ken Burns
MAX_ELEM   = 6

PNG = os.path.join(ASSETS, f"slide_{DEMO_SLIDE:02d}.png")
MP3 = os.path.join(ASSETS, f"narr_{DEMO_SLIDE:02d}.mp3")
OUT = os.path.join(BASE, f"demo_flyin_slide{DEMO_SLIDE:02d}.mp4")


# ── ease-out cubic ────────────────────────────────────────────────────────────

def ease_out(p):
    p = float(np.clip(p, 0, 1))
    return 1 - (1 - p) ** 3


# ── 偵測元素區塊 ──────────────────────────────────────────────────────────────

def detect_elements(img_bgr):
    """
    水平投影法：掃描每行的非背景像素數，找出獨立的內容帶。
    保證各元素垂直不重疊，每段文字 / 圖片各自獨立飛入。
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    # 背景色（由四角取樣）
    samples = [img_bgr[15, 15], img_bgr[15, -15], img_bgr[-15, 15], img_bgr[-15, -15]]
    bg_bgr    = np.median(samples, axis=0).astype(np.uint8)
    bg_bright = int(bg_bgr.mean()) > 128

    if bg_bright:
        bg_val  = float(np.median(gray[:25, :]))
        content = (np.abs(gray.astype(np.float32) - bg_val) > 10).astype(np.uint8)
    else:
        edges   = cv2.Canny(gray, 15, 60)
        content = (edges > 0).astype(np.uint8)

    # ── 水平投影：每行非背景像素數 ───────────────────────────────────────────
    h_proj = content.sum(axis=1).astype(np.float32)

    # 輕度平滑：只填平字體行內的細碎空白，保留元素間的間距
    kernel1d = np.ones(4, dtype=np.float32) / 4
    h_proj   = np.convolve(h_proj, kernel1d, mode='same')

    is_content = h_proj > (W * 0.012)   # 該行 > 1.2% 寬度有內容

    # ── 找連續內容帶（content band）─────────────────────────────────────────
    bands, in_band, y_start = [], False, 0
    for y in range(H):
        if is_content[y] and not in_band:
            y_start, in_band = y, True
        elif not is_content[y] and in_band:
            if y - y_start > 10:          # 忽略高度 < 10px 的碎片
                bands.append([y_start, y])
            in_band = False
    if in_band:
        bands.append([y_start, H])

    # ── 合併間隔 < GAP_PX 的相鄰帶（同一個元素被空白行斷開）────────────────
    GAP_PX = 10
    merged = []
    for b in bands:
        if merged and b[0] - merged[-1][1] < GAP_PX:
            merged[-1][1] = b[1]
        else:
            merged.append(b[:])

    # ── 強制分割過高的帶（> 28% 投影片高度）：找投影谷底自動切開 ────────────
    MAX_BAND_H = int(H * 0.28)
    split_bands = []
    for y1, y2 in merged:
        if y2 - y1 > MAX_BAND_H:
            # 在中段 1/4 ~ 3/4 找最低密度行
            s = y1 + (y2 - y1) // 4
            e = y1 + 3 * (y2 - y1) // 4
            sub = h_proj[s:e]
            if len(sub) > 0:
                min_idx   = int(sub.argmin()) + s
                split_val = h_proj[min_idx]
                peak_val  = h_proj[y1:y2].max()
                # 只在明顯谷底分割（谷 < 峰的 60%）
                if split_val < peak_val * 0.60 and min_idx - y1 > 20 and y2 - min_idx > 20:
                    split_bands.append([y1, min_idx])
                    split_bands.append([min_idx, y2])
                    continue
        split_bands.append([y1, y2])
    merged = split_bands[:MAX_ELEM]

    # ── 對每個帶找橫向範圍，加 padding ──────────────────────────────────────
    PAD = 18
    boxes = []
    for y1, y2 in merged:
        strip = content[y1:y2, :]
        x_idx = np.where(strip.sum(axis=0) > 0)[0]
        if len(x_idx) == 0:
            continue
        x1, x2 = int(x_idx[0]), int(x_idx[-1] + 1)
        boxes.append((
            max(0, y1 - PAD), max(0, x1 - PAD),
            min(H, y2 + PAD), min(W, x2 + PAD)
        ))

    # ── 指派飛入效果 ─────────────────────────────────────────────────────────
    regions   = []
    lr_toggle = 0
    for i, (y1, x1, y2, x2) in enumerate(boxes):
        ew, eh = x2 - x1, y2 - y1
        aspect   = ew / max(1, eh)
        is_title = (i == 0 and y1 < H * 0.30)
        is_image = (0.6 < aspect < 2.5 and ew * eh > W * H * 0.05)

        if is_title:
            effect = 'fly_top'
        elif is_image and not is_title:
            effect = 'zoom'
        else:
            effect = 'fly_left' if lr_toggle % 2 == 0 else 'fly_right'
            lr_toggle += 1

        regions.append((y1, x1, y2, x2, effect))
        print(f"  元素{i+1}: y({y1}→{y2}) x({x1}→{x2})  aspect={aspect:.1f}  → {effect}")

    return bg_bgr, regions


# ── 生成單一幀 ────────────────────────────────────────────────────────────────

def make_frame(img_rgb, clean_bg_rgb, bg_color_rgb, regions, t, zoom_f, dur):
    fade_out_st = dur - 0.45

    # 全域淡入
    if t < FADE_IN:
        fade = t / FADE_IN
    elif t > fade_out_st:
        fade = max(0.0, (dur - t) / 0.45)
    else:
        fade = 1.0

    # Ken Burns（套用在乾淨背景上）
    z = zoom_f
    if abs(z - 1.0) > 1e-4:
        nw, nh = int(W / z), int(H / z)
        x0, y0 = (W - nw) // 2, (H - nh) // 2
        base = cv2.resize(clean_bg_rgb[y0:y0+nh, x0:x0+nw], (W, H),
                          interpolation=cv2.INTER_LINEAR)
    else:
        base = clean_bg_rgb.copy()

    result = base.astype(np.float32) * fade

    # 各元素飛入
    for i, (y1, x1, y2, x2, effect) in enumerate(regions):
        t_start = ANIM_START + i * ELEM_GAP
        p = ease_out((t - t_start) / ELEM_DUR)
        if p <= 0:
            continue

        elem = img_rgb[y1:y2, x1:x2]
        eh, ew = elem.shape[:2]
        elem_f = elem.astype(np.float32) * fade

        if effect == 'fly_top':
            # 從上方滑入（元素從 y = -eh 移動到 y = y1）
            off_y = int(-(1 - p) * (y1 + eh))
            off_x = 0
            _blit(result, elem_f, y1 + off_y, x1 + off_x)

        elif effect == 'fly_left':
            # 從左方飛入
            off_x = int(-(1 - p) * (x1 + ew))
            _blit(result, elem_f, y1, x1 + off_x)

        elif effect == 'fly_right':
            # 從右方飛入
            off_x = int((1 - p) * (W - x1))
            _blit(result, elem_f, y1, x1 + off_x)

        elif effect == 'fly_bottom':
            off_y = int((1 - p) * (H - y1))
            _blit(result, elem_f, y1 + off_y, x1)

        elif effect == 'zoom':
            # 從中心縮放放大進場
            scale = ZOOM_START + (1.0 - ZOOM_START) * p
            sw = max(1, int(ew * scale))
            sh = max(1, int(eh * scale))
            scaled = cv2.resize(elem, (sw, sh), interpolation=cv2.INTER_LINEAR)
            cy = y1 + (eh - sh) // 2
            cx = x1 + (ew - sw) // 2
            _blit(result, scaled.astype(np.float32) * fade, cy, cx)

    return np.clip(result, 0, 255).astype(np.uint8)


def _blit(canvas, elem_f, dst_y, dst_x):
    """將 elem_f 貼到 canvas 的 (dst_y, dst_x)，自動裁切超出邊界的部分。"""
    eh, ew = elem_f.shape[:2]
    cy1 = max(0, dst_y);    cx1 = max(0, dst_x)
    cy2 = min(H, dst_y+eh); cx2 = min(W, dst_x+ew)
    if cy2 <= cy1 or cx2 <= cx1:
        return
    ey1 = cy1 - dst_y;  ex1 = cx1 - dst_x
    ey2 = ey1 + (cy2-cy1); ex2 = ex1 + (cx2-cx1)
    canvas[cy1:cy2, cx1:cx2] = elem_f[ey1:ey2, ex1:ex2]


# ── 主流程 ────────────────────────────────────────────────────────────────────

print(f"載入 slide_{DEMO_SLIDE:02d}.png …")
img_bgr = cv2.imread(PNG)
img_bgr = cv2.resize(img_bgr, (W, H))
img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

print("偵測元素區塊：")
bg_bgr, regions = detect_elements(img_bgr)
bg_rgb = bg_bgr[[2, 1, 0]]  # BGR→RGB

# 建立乾淨背景：純背景色，飛入前不顯示任何文字或內容
clean_bg = np.full((H, W, 3), bg_rgb, dtype=np.uint8)

if not regions:
    print("  ⚠️ 未偵測到元素，使用整張淡入")
    regions = [(0, 0, H, W, 'fade')]

n_elem  = len(regions)
all_done = ANIM_START + (n_elem - 1) * ELEM_GAP + ELEM_DUR
print(f"共 {n_elem} 個元素，全部就位於 {all_done:.2f}s")

n_frames = int(DEMO_DUR * FPS)
zooms = np.linspace(1.0, 1.0 + ZOOM_RANGE, n_frames, dtype=np.float32)

cmd = [
    FFMPEG, "-y",
    "-f", "rawvideo", "-vcodec", "rawvideo",
    "-s", f"{W}x{H}", "-pix_fmt", "rgb24", "-r", str(FPS),
    "-i", "pipe:0",
    "-i", MP3,
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
    "-pix_fmt", "yuv420p", "-r", str(FPS),
    "-c:a", "aac", "-b:a", "128k",
    "-t", str(DEMO_DUR),
    OUT,
]
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

print(f"生成 {n_frames} 幀…", end=" ", flush=True)
import time as _time
t0 = _time.time()
try:
    for fi in range(n_frames):
        t = fi / FPS
        frame = make_frame(img_rgb, clean_bg, bg_rgb, regions,
                           t, float(zooms[fi]), DEMO_DUR)
        proc.stdin.write(frame.tobytes())
finally:
    proc.stdin.close()
    proc.wait()

elapsed = _time.time() - t0
size_kb = os.path.getsize(OUT) // 1024
print(f"完成 {elapsed:.0f}s  →  {OUT}  ({size_kb} KB)")
print(f"效果摘要：")
for i, (y1, x1, y2, x2, effect) in enumerate(regions):
    print(f"  元素{i+1} [{effect}]  出現於 {ANIM_START + i*ELEM_GAP:.2f}s")
