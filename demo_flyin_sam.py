"""
demo_flyin_sam.py — OpenCV 偵測 + SAM 像素遮罩 + 飛入動畫 demo

流程：
  1. OpenCV 水平投影法找出元素粗略 bounding box
  2. SAM2 以各 box 為 prompt → 像素級遮罩（不受矩形裁切限制）
  3. 飛入動畫：SLIDE_DIST 近端位移 + opacity 淡入（不從螢幕外飛）
       → 文字永遠在畫面內，不會被截斷

裁切問題解法：
  - 舊做法：元素從螢幕外飛入 → 過渡中左側文字被截
  - 新做法：元素從距離終點 SLIDE_DIST px 的位置以透明→不透明飛入
            文字從一開始就在畫面內，完整顯示
"""
import os, sys, subprocess, time
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

FPS        = 24
W, H       = 1920, 1080
DEMO_DUR   = 15.0
DEMO_SLIDE = 0

ANIM_START       = 0.20
ELEM_GAP         = 0.55
ELEM_DUR         = 0.50
FADE_IN          = 0.30
ZOOM_RANGE       = 0.04
SLIDE_DIST       = 90      # 飛入位移距離（px）
TEXT_SETTLE_FRAC = 0.18    # 文字在 ELEM_DUR 的前 18% 內達到 100% opacity（約 0.09s）
MAX_ELEM         = 7
GAP_PX           = 10
MAX_BAND_H       = int(H * 0.28)
WIDE_BAND_FRAC   = 0.70    # 超過此寬度比例就嘗試垂直分割

SAM_MODEL = os.path.join(BASE, "sam2_b.pt")
PNG = os.path.join(ASSETS, f"slide_{DEMO_SLIDE:02d}.png")
MP3 = os.path.join(ASSETS, f"narr_{DEMO_SLIDE:02d}.mp3")
OUT = os.path.join(BASE, f"demo_flyin_sam{DEMO_SLIDE:02d}.mp4")


def ease_out(p):
    return 1 - (1 - float(np.clip(p, 0, 1))) ** 3


# ── 步驟 1：OpenCV 水平投影找粗略 box ────────────────────────────────────────

def detect_boxes(img_bgr):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    corners = [img_bgr[15,15], img_bgr[15,-15], img_bgr[-15,15], img_bgr[-15,-15]]
    bg_bgr   = np.median(corners, axis=0).astype(np.uint8)
    bg_bright = int(bg_bgr.mean()) > 128

    if bg_bright:
        bg_val  = float(np.median(gray[:25, :]))
        content = (np.abs(gray.astype(np.float32) - bg_val) > 10).astype(np.uint8)
    else:
        content = (cv2.Canny(gray, 15, 60) > 0).astype(np.uint8)

    kernel1d = np.ones(4, dtype=np.float32) / 4
    h_proj   = np.convolve(content.sum(axis=1).astype(np.float32), kernel1d, mode='same')
    is_content = h_proj > (W * 0.012)

    bands, in_band, y0 = [], False, 0
    for y in range(H):
        if is_content[y] and not in_band:
            y0, in_band = y, True
        elif not is_content[y] and in_band:
            if y - y0 > 10:
                bands.append([y0, y])
            in_band = False
    if in_band:
        bands.append([y0, H])

    merged = []
    for b in bands:
        if merged and b[0] - merged[-1][1] < GAP_PX:
            merged[-1][1] = b[1]
        else:
            merged.append(b[:])

    # 強制分割過高的帶
    split = []
    for y1, y2 in merged:
        if y2 - y1 > MAX_BAND_H:
            s = y1 + (y2 - y1) // 4
            e = y1 + 3 * (y2 - y1) // 4
            sub = h_proj[s:e]
            if len(sub) > 0:
                mi = int(sub.argmin()) + s
                sv, pv = h_proj[mi], h_proj[y1:y2].max()
                if sv < pv * 0.60 and mi - y1 > 20 and y2 - mi > 20:
                    split += [[y1, mi], [mi, y2]]
                    continue
        split.append([y1, y2])
    merged = split[:MAX_ELEM]

    PAD, raw_boxes = 15, []
    for y1, y2 in merged:
        strip = content[y1:y2, :]
        xi = np.where(strip.sum(axis=0) > 0)[0]
        if len(xi) == 0:
            continue
        x1, x2 = int(xi[0]), int(xi[-1] + 1)
        raw_boxes.append((y1, x1, y2, x2))

    # ── 對寬帶做垂直分割（把標題和圖片分開）────────────────────────────────────
    boxes = []
    for y1, x1, y2, x2 in raw_boxes:
        band_w = x2 - x1
        if band_w > W * WIDE_BAND_FRAC:
            # 垂直投影找分割點
            v_proj = content[y1:y2, :].sum(axis=0).astype(np.float32)
            kv = np.ones(12, np.float32) / 12
            v_proj = np.convolve(v_proj, kv, mode='same')
            s = int(W * 0.28)
            e = int(W * 0.72)
            sub = v_proj[s:e]
            if len(sub) > 0:
                mi    = int(sub.argmin()) + s
                sv    = v_proj[mi]
                pv    = v_proj.max()
                # 只在明顯垂直谷底分割（谷 < 峰的 25%）
                if sv < pv * 0.25 and mi - x1 > 40 and x2 - mi > 40:
                    # 左半
                    xi_l = np.where(content[y1:y2, :mi].sum(axis=0) > 0)[0]
                    if len(xi_l):
                        boxes.append((max(0,y1-PAD), max(0,int(xi_l[0])-PAD),
                                      min(H,y2+PAD), min(W,mi+PAD)))
                    # 右半
                    xi_r = np.where(content[y1:y2, mi:].sum(axis=0) > 0)[0]
                    if len(xi_r):
                        boxes.append((max(0,y1-PAD), max(0,mi-PAD),
                                      min(H,y2+PAD), min(W,int(xi_r[-1])+mi+PAD)))
                    continue
        boxes.append((max(0,y1-PAD), max(0,x1-PAD), min(H,y2+PAD), min(W,x2+PAD)))

    return bg_bgr, boxes[:MAX_ELEM]


# ── 步驟 2：SAM box-prompt 精確遮罩 ──────────────────────────────────────────

def sam_box_masks(png_path, boxes):
    """對每個 box 執行 SAM box-prompt，回傳像素遮罩 list"""
    print("載入 SAM2 模型…", end=" ", flush=True)
    from ultralytics import SAM
    model = SAM(SAM_MODEL)
    print("完成")

    masks = []
    for i, (y1, x1, y2, x2) in enumerate(boxes):
        print(f"  SAM box {i+1}/{len(boxes)}: ({x1},{y1},{x2},{y2})…", end=" ", flush=True)
        t0 = time.time()
        # ultralytics SAM bboxes 格式: [x1, y1, x2, y2]
        results = model(png_path, bboxes=[[x1, y1, x2, y2]], verbose=False)
        elapsed = time.time() - t0

        if results and results[0].masks is not None and len(results[0].masks.data) > 0:
            # 取信心最高的那個遮罩
            raw = results[0].masks.data[0].cpu().numpy()
            mask = cv2.resize(raw.astype(np.uint8), (W, H),
                              interpolation=cv2.INTER_NEAREST).astype(bool)
            pct = mask.sum() / (W * H) * 100
            print(f"{elapsed:.0f}s  面積={pct:.1f}%")
            masks.append(mask)
        else:
            # SAM 失敗 → 退回矩形遮罩
            print(f"{elapsed:.0f}s  SAM 失敗，用矩形代替")
            fallback = np.zeros((H, W), dtype=bool)
            fallback[y1:y2, x1:x2] = True
            masks.append(fallback)

    return masks


# ── 步驟 3：指派飛入效果 ──────────────────────────────────────────────────────

def assign_effects(boxes, masks):
    elements, lr = [], 0
    for i, ((y1, x1, y2, x2), mask) in enumerate(zip(boxes, masks)):
        ew, eh = x2 - x1, y2 - y1
        aspect   = ew / max(1, eh)
        is_title = (i == 0 and y1 < H * 0.30)
        is_image = (0.5 < aspect < 2.5 and mask.sum() > W * H * 0.04 and not is_title)

        if is_title:
            effect = 'fly_top'
        elif is_image:
            effect = 'zoom'
        else:
            effect = 'fly_left' if lr % 2 == 0 else 'fly_right'
            lr += 1

        elements.append((mask, (y1, x1, y2, x2), effect))
        print(f"  元素{i+1} [{effect}]  y({y1}→{y2}) x({x1}→{x2})  aspect={aspect:.1f}")

    return elements


# ── 步驟 4：逐幀動畫（像素遮罩 + 近端位移 + opacity 淡入）────────────────────

def _blit_alpha(canvas_f, src_f, dst_y, dst_x, alpha):
    """將 src_f 以 alpha 混合貼到 canvas_f 的 (dst_y, dst_x)"""
    sh, sw = src_f.shape[:2]
    cy1 = max(0, dst_y);    cx1 = max(0, dst_x)
    cy2 = min(H, dst_y+sh); cx2 = min(W, dst_x+sw)
    if cy2 <= cy1 or cx2 <= cx1:
        return
    ey1 = cy1 - dst_y;  ex1 = cx1 - dst_x
    ey2 = ey1+(cy2-cy1); ex2 = ex1+(cx2-cx1)
    bg = canvas_f[cy1:cy2, cx1:cx2]
    fg = src_f[ey1:ey2, ex1:ex2]
    canvas_f[cy1:cy2, cx1:cx2] = bg * (1 - alpha) + fg * alpha


def _blit_mask_alpha(canvas_f, img_rgb, mask, off_y, off_x, alpha):
    """用 SAM 遮罩提取像素，以 alpha 混合貼到 (offset_y, offset_x)"""
    ys, xs = np.where(mask)
    if len(ys) == 0:
        return
    dst_y = ys + off_y
    dst_x = xs + off_x
    valid  = (dst_y >= 0) & (dst_y < H) & (dst_x >= 0) & (dst_x < W)
    src_px = img_rgb[ys[valid], xs[valid]].astype(np.float32)
    dys, dxs = dst_y[valid], dst_x[valid]
    canvas_f[dys, dxs] = canvas_f[dys, dxs] * (1 - alpha) + src_px * alpha


def make_frame(img_rgb, clean_bg, elements, t, zoom_f, dur):
    fade_out_st = dur - 0.45
    if t < FADE_IN:
        global_fade = t / FADE_IN
    elif t > fade_out_st:
        global_fade = max(0.0, (dur - t) / 0.45)
    else:
        global_fade = 1.0

    # Ken Burns on clean background
    z = zoom_f
    if abs(z - 1.0) > 1e-4:
        nw, nh = int(W / z), int(H / z)
        x0, y0 = (W - nw) // 2, (H - nh) // 2
        base = cv2.resize(clean_bg[y0:y0+nh, x0:x0+nw], (W, H),
                          interpolation=cv2.INTER_LINEAR)
    else:
        base = clean_bg

    result = base.astype(np.float32) * global_fade

    for i, (mask, (y1, x1, y2, x2), effect) in enumerate(elements):
        t_start = ANIM_START + i * ELEM_GAP
        p = ease_out((t - t_start) / ELEM_DUR)
        if p <= 0:
            continue

        eh = y2 - y1 + 1
        ew = x2 - x1 + 1

        if effect in ('fly_top', 'fly_left', 'fly_right'):
            # 文字：快速淡入（前 TEXT_SETTLE_FRAC 就達到 100%）+ 全程位移
            fast_alpha = min(p / max(TEXT_SETTLE_FRAC, 1e-6), 1.0) * global_fade
            if effect == 'fly_top':
                off_y = int(-SLIDE_DIST * (1 - p))
                _blit_mask_alpha(result, img_rgb, mask, off_y, 0, fast_alpha)
            elif effect == 'fly_left':
                off_x = int(-SLIDE_DIST * (1 - p))
                _blit_mask_alpha(result, img_rgb, mask, 0, off_x, fast_alpha)
            else:  # fly_right
                off_x = int(SLIDE_DIST * (1 - p))
                _blit_mask_alpha(result, img_rgb, mask, 0, off_x, fast_alpha)

        elif effect == 'zoom':
            # 圖片：縮放 0.6x → 1.0x + 漸進淡入
            alpha = p * global_fade
            scale = 0.60 + 0.40 * p
            sw = max(1, int(ew * scale))
            sh = max(1, int(eh * scale))
            elem = img_rgb[y1:y2, x1:x2]
            scaled = cv2.resize(elem, (sw, sh), interpolation=cv2.INTER_LINEAR)
            cy = y1 + (eh - sh) // 2
            cx = x1 + (ew - sw) // 2
            _blit_alpha(result, scaled.astype(np.float32), cy, cx, alpha)

    return np.clip(result, 0, 255).astype(np.uint8)


# ── 主流程 ────────────────────────────────────────────────────────────────────

print(f"載入 slide_{DEMO_SLIDE:02d}.png …")
img_bgr = cv2.imread(PNG)
img_bgr = cv2.resize(img_bgr, (W, H))
img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

print("OpenCV 偵測元素位置…")
bg_bgr, boxes = detect_boxes(img_bgr)
bg_rgb = bg_bgr[[2, 1, 0]]
print(f"  偵測到 {len(boxes)} 個 box")
for i, (y1,x1,y2,x2) in enumerate(boxes):
    print(f"  box{i+1}: y({y1}→{y2}) x({x1}→{x2})")

print("\nSAM box-prompt 精確分割…")
masks = sam_box_masks(PNG, boxes)

print("\n指派飛入效果：")
elements = assign_effects(boxes, masks)

# 乾淨背景（純背景色）
bg_rgb_full = np.full((H, W, 3), bg_rgb, dtype=np.uint8)

n_elem   = len(elements)
all_done = ANIM_START + (n_elem - 1) * ELEM_GAP + ELEM_DUR
print(f"\n共 {n_elem} 個元素，全部就位於 {all_done:.2f}s")

n_frames = int(DEMO_DUR * FPS)
zooms    = np.linspace(1.0, 1.0 + ZOOM_RANGE, n_frames, dtype=np.float32)

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
t0 = time.time()
try:
    for fi in range(n_frames):
        t = fi / FPS
        frame = make_frame(img_rgb, bg_rgb_full, elements,
                           t, float(zooms[fi]), DEMO_DUR)
        proc.stdin.write(frame.tobytes())
finally:
    proc.stdin.close()
    proc.wait()

elapsed = time.time() - t0
size_kb  = os.path.getsize(OUT) // 1024
print(f"完成 {elapsed:.0f}s  →  {OUT}  ({size_kb} KB)")
print("\n效果摘要（像素遮罩 + 近端滑入 + 淡入，無裁切）：")
for i, (_, (y1,x1,y2,x2), effect) in enumerate(elements):
    t_s = ANIM_START + i * ELEM_GAP
    print(f"  元素{i+1} [{effect:10s}] @{t_s:.2f}s  位移 {SLIDE_DIST}px + opacity 淡入")
