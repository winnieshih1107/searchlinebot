"""
animate_enhanced.py
強化版投影片動畫生成器

動畫效果：
1. 整張投影片淡入 + 輕微放大進場（scale 1.04→1.00，0.5s）
2. 標題區（頂部）：從上往下 wipe reveal（curtain 效果）
3. 內容/文字區：交替左→右 wipe reveal
4. 圖片區（寬高比 > 2.5 的區域）：亮度 + 輕微 Ken Burns 縮放
5. 區域進場時有亮光閃爍邊緣（leading edge glow）
6. 全亮定格 → 淡出
"""

import os, sys, subprocess
import numpy as np
import imageio_ffmpeg
import cv2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE    = r"D:\wi\260612"
ASSETS  = os.path.join(BASE, "pres_pdf_assets")
TMP_DIR = os.path.join(ASSETS, "enh_tmp")
OUT_MP4 = os.path.join(BASE, "hw6_presentation_enhanced.mp4")
FFMPEG  = imageio_ffmpeg.get_ffmpeg_exe()

FPS      = 24
N_SLIDES = 12
W, H     = 1920, 1080

# ── 動畫時序（秒） ────────────────────────────────────────────────────────────
ENTRY_DUR    = 0.50   # 進場（淡入+縮放）
DIM_DUR      = 0.30   # 整體變暗
REVEAL_GAP   = 0.28   # 各區域間隔
REVEAL_DUR   = 0.50   # 每個區域 wipe 時長
FULL_HOLD    = 0.40   # 全亮靜止
NARR_DELAY   = 0.45   # 旁白延遲
FADE_OUT     = 0.55   # 淡出

BASE_BRIGHT  = 0.18   # 暗底亮度
MAX_REGIONS  = 8

os.makedirs(TMP_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Easing
# ─────────────────────────────────────────────────────────────────────────────
def ease_out_cubic(p):
    p = max(0.0, min(1.0, p))
    return 1.0 - (1.0 - p) ** 3

def ease_in_out_sine(p):
    p = max(0.0, min(1.0, p))
    return (1.0 - np.cos(np.pi * p)) / 2.0

def ease_out_quart(p):
    p = max(0.0, min(1.0, p))
    return 1.0 - (1.0 - p) ** 4


# ─────────────────────────────────────────────────────────────────────────────
# 區域偵測 — 回傳 (mask, region_type)
#   region_type: "title" / "image" / "text"
# ─────────────────────────────────────────────────────────────────────────────
def detect_regions(img_bgr):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (55, 16))
    dilated = cv2.dilate(thresh, kernel, iterations=2)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    raw = []
    total = H * W
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        ratio = (w * h) / total
        if ratio < 0.015 or ratio > 0.72:
            continue
        if h < 12 or w < 40:
            continue
        raw.append((x, y, w, h))

    if len(raw) < 3:
        raw = [(0, i * H // 4, W, H // 4) for i in range(4)]

    # 去重（IoU > 0.55 保留大的）
    def iou(a, b):
        ax1, ay1, ax2, ay2 = a[0], a[1], a[0]+a[2], a[1]+a[3]
        bx1, by1, bx2, by2 = b[0], b[1], b[0]+b[2], b[1]+b[3]
        ix = max(0, min(ax2, bx2) - max(ax1, bx1))
        iy = max(0, min(ay2, by2) - max(ay1, by1))
        inter = ix * iy
        union = a[2]*a[3] + b[2]*b[3] - inter
        return inter / max(union, 1)

    keep = []
    for i, a in enumerate(raw):
        if any(i != j and iou(a, b) > 0.55 and b[2]*b[3] > a[2]*a[3]
               for j, b in enumerate(raw)):
            continue
        keep.append(a)

    # 依 y 排序
    keep.sort(key=lambda r: r[1])
    keep = keep[:MAX_REGIONS]

    # 標記類型
    result = []
    pad = 8
    for idx, (x, y, w, h) in enumerate(keep):
        m = np.zeros((H, W), dtype=np.float32)
        m[max(0,y-pad):min(H,y+h+pad), max(0,x-pad):min(W,x+w+pad)] = 1.0

        # 判斷類型
        if idx == 0 and y < H * 0.22 and w > W * 0.35:
            rtype = "title"
        elif w / max(h, 1) > 2.2 or w * h / total > 0.25:
            rtype = "image"
        else:
            rtype = "text"

        result.append((m, rtype, (x, y, w, h)))

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 建立每幀的亮度圖（in-place vectorized）
# ─────────────────────────────────────────────────────────────────────────────
def build_brightness(out, tmp, col_vec, row_vec, regions, t, total_dur, slide_idx):
    """
    out     : (H, W) float32  輸出亮度（0→1）
    tmp     : (H, W) float32  暫存
    col_vec : (W,)   float32  0..W-1
    row_vec : (H,)   float32  0..H-1
    regions : list of (mask_f32, rtype, bbox, reveal_start)
    """
    # ── 整體暗底 ──────────────────────────────────────────────────────────────
    dim_start = ENTRY_DUR
    dim_p = ease_out_cubic(min(1.0, max(0.0, (t - dim_start) / DIM_DUR)))
    base_b = 1.0 - (1.0 - BASE_BRIGHT) * dim_p   # 1.0 → BASE_BRIGHT
    out[:] = base_b

    # ── 各區域 reveal ─────────────────────────────────────────────────────────
    for k_idx, (mask_f32, rtype, (rx, ry, rw, rh), rs) in enumerate(regions):
        if t < rs:
            continue
        p = ease_out_quart(min(1.0, (t - rs) / REVEAL_DUR))

        if rtype == "title":
            # 從上往下 wipe（row reveal）
            reveal_row = ry + p * (rh + 16)
            row_progress = np.clip((reveal_row - row_vec) / max(rh * 0.08, 1.0),
                                   0.0, 1.0)  # (H,)
            np.multiply(mask_f32, row_progress[:, np.newaxis], out=tmp)
            np.maximum(out, tmp, out=out)

            # leading edge glow
            if p < 0.95:
                glow_dist = np.abs(row_vec - reveal_row)
                glow_strength = np.clip(1.0 - glow_dist / max(rh * 0.06, 12.0),
                                        0.0, 1.0) * 0.35
                np.multiply(mask_f32, glow_strength[:, np.newaxis], out=tmp)
                np.add(out, tmp, out=out)

        elif rtype == "image":
            # 亮度 fade-in（無方向性，圖片感）
            np.multiply(mask_f32, p, out=tmp)
            np.maximum(out, tmp, out=out)

        else:
            # 文字：left→right wipe（奇偶交替方向）
            right_to_left = (k_idx % 2 == 1)
            col_min = max(0, rx - 8)
            col_max = min(W, rx + rw + 8)
            col_span = max(col_max - col_min, 1)

            if right_to_left:
                reveal_col = col_max - p * col_span
                col_progress = np.clip((col_vec - reveal_col) / max(col_span * 0.06, 8.0),
                                       0.0, 1.0)
            else:
                reveal_col = col_min + p * col_span
                col_progress = np.clip((reveal_col - col_vec) / max(col_span * 0.06, 8.0),
                                       0.0, 1.0)  # (W,)

            np.multiply(mask_f32, col_progress[np.newaxis, :], out=tmp)
            np.maximum(out, tmp, out=out)

            # leading edge glow
            if p < 0.95:
                glow_dist = np.abs(col_vec - reveal_col)
                glow_strength = np.clip(1.0 - glow_dist / max(col_span * 0.04, 10.0),
                                        0.0, 1.0) * 0.28
                np.multiply(mask_f32, glow_strength[np.newaxis, :], out=tmp)
                np.add(out, tmp, out=out)

    # ── 所有 reveal 完成後 → 全亮 ────────────────────────────────────────────
    if regions:
        anim_end = regions[-1][3] + REVEAL_DUR + FULL_HOLD
        if t >= anim_end:
            finish_p = ease_out_cubic(min(1.0, (t - anim_end) / 0.30))
            np.subtract(1.0, out, out=tmp)
            np.multiply(tmp, finish_p, out=tmp)
            np.add(out, tmp, out=out)

    np.clip(out, 0.0, 1.2, out=out)  # 允許輕微過曝（glow）

    # ── 淡出 ──────────────────────────────────────────────────────────────────
    fade_start = total_dur - FADE_OUT
    if t >= fade_start:
        fade = max(0.0, (total_dur - t) / FADE_OUT)
        np.multiply(out, fade, out=out)


# ─────────────────────────────────────────────────────────────────────────────
# 進場縮放（Ken Burns entry：scale 1.04 → 1.00）
# ─────────────────────────────────────────────────────────────────────────────
_ENTRY_SCALE_MAX = 1.04

def get_crop_for_frame(t):
    """傳回 (x_off, y_off, crop_w, crop_h)，在進場 ENTRY_DUR 秒內收斂至全幅"""
    p = ease_out_cubic(min(1.0, t / ENTRY_DUR))
    scale = _ENTRY_SCALE_MAX - (_ENTRY_SCALE_MAX - 1.0) * p
    crop_w = int(W / scale)
    crop_h = int(H / scale)
    x_off = (W - crop_w) // 2
    y_off = (H - crop_h) // 2
    return x_off, y_off, crop_w, crop_h


# ─────────────────────────────────────────────────────────────────────────────
# 音訊時長
# ─────────────────────────────────────────────────────────────────────────────
def get_audio_duration(mp3_path):
    ffprobe = FFMPEG.replace("ffmpeg", "ffprobe")
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", mp3_path]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
        return float(out)
    except Exception:
        cmd2 = [FFMPEG, "-i", mp3_path, "-f", "null", "-"]
        r = subprocess.run(cmd2, capture_output=True, text=True)
        for line in r.stderr.split("\n"):
            if "Duration" in line:
                h2, m2, s2 = line.split("Duration:")[1].split(",")[0].strip().split(":")
                return int(h2)*3600 + int(m2)*60 + float(s2)
        return 30.0


# ─────────────────────────────────────────────────────────────────────────────
# 片段編碼
# ─────────────────────────────────────────────────────────────────────────────
def encode_segment(slide_rgb, raw_regions, mp3_path, out_path, slide_idx):
    narr_dur = get_audio_duration(mp3_path)
    n_regions = len(raw_regions)

    # 計算每個 region 的 reveal_start
    anim_begin = ENTRY_DUR + DIM_DUR
    regions_with_time = []
    for k, (mf, rtype, bbox) in enumerate(raw_regions):
        rs = anim_begin + k * REVEAL_GAP
        regions_with_time.append((mf, rtype, bbox, rs))

    anim_end_t = anim_begin + n_regions * REVEAL_GAP + REVEAL_DUR + FULL_HOLD
    total_dur  = max(NARR_DELAY + narr_dur, anim_end_t + 0.3) + FADE_OUT
    total_frames = int(total_dur * FPS)
    narr_delay_ms = int(NARR_DELAY * 1000)

    # 預計算 vectors
    col_vec = np.arange(W, dtype=np.float32)
    row_vec = np.arange(H, dtype=np.float32)

    slide_f32 = slide_rgb.astype(np.float32)

    # 預分配 buffer
    brightness = np.empty((H, W),    dtype=np.float32)
    tmp_buf    = np.empty((H, W),    dtype=np.float32)
    frame_f32  = np.empty((H, W, 3), dtype=np.float32)
    frame_u8   = np.empty((H, W, 3), dtype=np.uint8)
    crop_buf   = np.empty((H, W, 3), dtype=np.uint8)

    silent_mp4 = out_path.replace(".mp4", "_silent.mp4")
    cmd_video  = [
        FFMPEG, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{W}x{H}", "-pix_fmt", "rgb24",
        "-r", str(FPS), "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", silent_mp4,
    ]
    proc = subprocess.Popen(cmd_video, stdin=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)

    entry_frames = int(ENTRY_DUR * FPS)
    for f_idx in range(total_frames):
        t = f_idx / FPS

        # 進場縮放（僅 entry_frames 內做 resize）
        if f_idx < entry_frames:
            x_off, y_off, crop_w, crop_h = get_crop_for_frame(t)
            region = slide_rgb[y_off:y_off+crop_h, x_off:x_off+crop_w]
            cv2.resize(region, (W, H), dst=crop_buf, interpolation=cv2.INTER_LINEAR)
            base_frame = crop_buf.astype(np.float32)
        else:
            base_frame = slide_f32

        # 亮度遮罩
        build_brightness(brightness, tmp_buf, col_vec, row_vec,
                         regions_with_time, t, total_dur, slide_idx)

        # 進場淡入
        if t < ENTRY_DUR:
            fade_in = ease_out_cubic(t / ENTRY_DUR)
            np.multiply(brightness, fade_in, out=brightness)

        # 套用亮度
        np.multiply(base_frame, brightness[:, :, np.newaxis], out=frame_f32)
        np.clip(frame_f32, 0.0, 255.0, out=frame_f32)
        np.copyto(frame_u8, frame_f32, casting="unsafe")

        proc.stdin.write(frame_u8.data)

    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg video encode failed (slide {slide_idx})")

    # 合併音訊
    cmd_audio = [
        FFMPEG, "-y",
        "-i", silent_mp4, "-i", mp3_path,
        "-filter_complex",
        f"[1:a]adelay={narr_delay_ms}|{narr_delay_ms}[a_del];"
        "[a_del]apad[a_out]",
        "-map", "0:v", "-map", "[a_out]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-shortest", out_path,
    ]
    r = subprocess.run(cmd_audio, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        import shutil
        shutil.copy(silent_mp4, out_path)
    try:
        os.remove(silent_mp4)
    except Exception:
        pass
    return total_dur


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
slide_pngs  = [os.path.join(ASSETS, f"slide_{i:02d}.png") for i in range(N_SLIDES)]
audio_files = [os.path.join(ASSETS, f"narr_{i:02d}.mp3")  for i in range(N_SLIDES)]

for p in slide_pngs + audio_files:
    if not os.path.exists(p):
        raise FileNotFoundError(f"找不到: {p}")

print("=" * 60)
print("強化版投影片動畫生成器")
print("效果：縮放進場 + 標題 curtain / 文字 wipe / 圖片 fade")
print("=" * 60)

segment_files = []

for i, (png, mp3) in enumerate(zip(slide_pngs, audio_files)):
    seg = os.path.join(TMP_DIR, f"enh_seg_{i:02d}.mp4")

    if os.path.exists(seg) and os.path.getsize(seg) > 50000:
        print(f"[{i+1:02d}/{N_SLIDES}] 已存在，跳過", flush=True)
        segment_files.append(seg)
        continue

    print(f"\n[{i+1:02d}/{N_SLIDES}] slide_{i:02d}.png", end="  ", flush=True)

    img_bgr  = cv2.imread(png)
    img_bgr  = cv2.resize(img_bgr, (W, H))
    slide_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    raw_regions = detect_regions(img_bgr)
    types = [r[1] for r in raw_regions]
    print(f"偵測 {len(raw_regions)} 區域 {types}", flush=True)

    narr_dur   = get_audio_duration(mp3)
    n_reg      = len(raw_regions)
    anim_end_t = ENTRY_DUR + DIM_DUR + n_reg * REVEAL_GAP + REVEAL_DUR + FULL_HOLD
    total_est  = max(NARR_DELAY + narr_dur, anim_end_t + 0.3) + FADE_OUT
    total_frames = int(total_est * FPS)
    print(f"  渲染 {total_frames} frames（{total_est:.1f}s）…", end=" ", flush=True)

    dur     = encode_segment(slide_rgb, raw_regions, mp3, seg, i)
    size_kb = os.path.getsize(seg) // 1024
    print(f"完成  ({size_kb} KB)", flush=True)
    segment_files.append(seg)

# ── 串接所有片段 ──────────────────────────────────────────────────────────────
print(f"\n串接 {len(segment_files)} 個片段…", flush=True)
concat_list = os.path.join(TMP_DIR, "concat_enh.txt")
with open(concat_list, "w", encoding="utf-8") as f:
    for seg in segment_files:
        f.write(f"file '{seg.replace(os.sep, '/')}'\n")

cmd_concat = [
    FFMPEG, "-y",
    "-f", "concat", "-safe", "0", "-i", concat_list,
    "-c", "copy", "-movflags", "+faststart",
    OUT_MP4,
]
subprocess.run(cmd_concat, check=True, capture_output=True)

size_mb = os.path.getsize(OUT_MP4) / 1024 / 1024
print(f"\n完成！")
print(f"  輸出：{OUT_MP4}")
print(f"  大小：{size_mb:.1f} MB", flush=True)
