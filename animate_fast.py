"""
animate_fast.py — 純 ffmpeg 快速動畫版
效果：
  • 進場 wipe：左→右掃描亮起（0 ~ 1.0s），邊緣 80px 漸層
  • Ken Burns：偶數頁縮小→放大(1.0→1.04)，奇數頁放大→縮小(1.04→1.0)
  • 淡入 0.3s / 淡出 0.5s
  • 投影片間 xfade 交叉淡化 0.6s
"""
import os, subprocess
import imageio_ffmpeg

BASE    = r"D:\wi\260612"
ASSETS  = os.path.join(BASE, "pres_pdf_assets")
TMP_DIR = os.path.join(ASSETS, "fast_tmp")
OUT_MP4 = os.path.join(BASE, "hw6_presentation_enhanced.mp4")
FFMPEG  = imageio_ffmpeg.get_ffmpeg_exe()
FFPROBE = FFMPEG.replace("ffmpeg-win", "ffprobe-win")
if not os.path.exists(FFPROBE):
    FFPROBE = FFMPEG.replace("ffmpeg", "ffprobe")


def get_duration(mp3_path):
    cmd = [FFPROBE, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", mp3_path]
    try:
        return float(subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip())
    except Exception:
        r = subprocess.run([FFMPEG, "-i", mp3_path, "-f", "null", "-"],
                           capture_output=True, text=True)
        for line in r.stderr.split("\n"):
            if "Duration" in line:
                h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
                return int(h)*3600 + int(m)*60 + float(s)
        return 30.0

FPS        = 24
FADE_IN    = 0.30
FADE_OUT   = 0.50
XFADE_DUR  = 0.60
WIPE_DUR   = 1.00   # wipe 持續時長（秒）
EDGE_PX    = 80     # wipe 邊緣柔化像素
ZOOM_RANGE = 0.04
W, H       = 1920, 1080
N_SLIDES   = 12
EXTRA_SEC  = 1.20   # 旁白結束後多留的秒數

os.makedirs(TMP_DIR, exist_ok=True)

# ── 計算各段時長 ───────────────────────────────────────────────────────────────
print("計算旁白時長…")
durations = []
for i in range(N_SLIDES):
    mp3 = os.path.join(ASSETS, f"narr_{i:02d}.mp3")
    dur = get_duration(mp3) + EXTRA_SEC
    durations.append(dur)
    print(f"  narr_{i:02d}: {dur:.1f}s")

# ── 逐張編碼 ──────────────────────────────────────────────────────────────────
print("\n編碼各投影片片段…")
segment_files = []

for i in range(N_SLIDES):
    png = os.path.join(ASSETS, f"slide_{i:02d}.png")
    mp3 = os.path.join(ASSETS, f"narr_{i:02d}.mp3")
    seg = os.path.join(TMP_DIR, f"fseg_{i:02d}.mp4")

    if os.path.exists(seg) and os.path.getsize(seg) > 50_000:
        print(f"  [{i+1:02d}/{N_SLIDES}] 已存在，跳過")
        segment_files.append(seg)
        continue

    dur          = durations[i]
    n_frames     = max(1, int(dur * FPS))
    fade_out_st  = max(0.0, dur - FADE_OUT - 0.05)

    # Ken Burns：偶數頁放大，奇數頁縮小
    if i % 2 == 0:
        zoom_expr = f"min(zoom+{ZOOM_RANGE/n_frames:.8f},1.0+{ZOOM_RANGE})"
    else:
        zoom_expr = f"max(zoom-{ZOOM_RANGE/n_frames:.8f},1.0)"

    # Wipe reveal（左→右）：geq lum 公式
    #   bright = 0.15 + 0.85 * clamp((sweep_x + EDGE_PX - X) / (2*EDGE_PX), 0, 1)
    #   sweep_x = clamp(T / WIPE_DUR, 0, 1) * W
    wipe_expr = (
        f"0.15+0.85*max(min("
        f"(max(min(T/{WIPE_DUR},1.0),0.0)*{W}+{EDGE_PX}-X)/({2*EDGE_PX}.0"
        f"),1.0),0.0)"
    )
    geq_lum  = f"clip(lum(X,Y)*({wipe_expr}),0,255)"
    geq_cb   = f"clip(128+(cb(X,Y)-128)*({wipe_expr}),0,255)"
    geq_cr   = f"clip(128+(cr(X,Y)-128)*({wipe_expr}),0,255)"

    vf = (
        f"scale={W}:{H},"
        f"zoompan="
        f"z='{zoom_expr}':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d={n_frames}:s={W}x{H}:fps={FPS},"
        f"geq=lum='{geq_lum}':cb='{geq_cb}':cr='{geq_cr}',"
        f"fade=t=in:st=0:d={FADE_IN},"
        f"fade=t=out:st={fade_out_st:.3f}:d={FADE_OUT}"
    )

    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-framerate", str(FPS), "-i", png,
        "-i", mp3,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(dur),
        seg,
    ]
    print(f"  [{i+1:02d}/{N_SLIDES}] slide_{i:02d} ({dur:.1f}s)…", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"FAILED\n{r.stderr[-800:]}")
        raise RuntimeError(f"ffmpeg 片段 {i} 失敗")
    print(f"完成 ({os.path.getsize(seg)//1024} KB)", flush=True)
    segment_files.append(seg)

# ── xfade 串接 ────────────────────────────────────────────────────────────────
print(f"\nxfade 串接 {len(segment_files)} 片段…")
n_segs = len(segment_files)
xd     = XFADE_DUR

cumulative = 0.0
offsets    = []
for i in range(n_segs - 1):
    cumulative += durations[i]
    offsets.append(max(0.0, cumulative - xd * (i + 1)))

fc_parts = []
last_v   = "[0:v]"
for i in range(1, n_segs):
    out_v = f"[v{i:02d}]"
    fc_parts.append(
        f"{last_v}[{i}:v]xfade=transition=fade:duration={xd}:offset={offsets[i-1]:.4f}{out_v}"
    )
    last_v = out_v

a_concat = "".join(f"[{i}:a]" for i in range(n_segs))
fc_parts.append(f"{a_concat}concat=n={n_segs}:v=0:a=1[aout]")
filter_complex = "; ".join(fc_parts)

input_args = []
for seg in segment_files:
    input_args += ["-i", seg]

tmp_out = OUT_MP4.replace(".mp4", "_tmp.mp4")
cmd = [
    FFMPEG, "-y",
    *input_args,
    "-filter_complex", filter_complex,
    "-map", last_v,
    "-map", "[aout]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "21",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "128k",
    "-movflags", "+faststart",
    tmp_out,
]
r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
if r.returncode != 0:
    print(f"xfade 失敗，降級 concat…")
    concat_list = os.path.join(TMP_DIR, "concat.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for seg in segment_files:
            f.write(f"file '{seg.replace(os.sep, '/')}'\n")
    subprocess.run(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c", "copy", "-movflags", "+faststart", tmp_out],
        check=True
    )

os.replace(tmp_out, OUT_MP4)
size_mb   = os.path.getsize(OUT_MP4) / 1024 / 1024
total_sec = sum(durations)
print(f"\n完成！")
print(f"  輸出：{OUT_MP4}")
print(f"  大小：{size_mb:.1f} MB   時長：{total_sec/60:.1f} 分鐘")
