"""
animate_fast_v2.py — 逐行掃描動畫版（row-stagger reveal）

效果：
  • 每一行像素獨立亮起（top→bottom 或 bottom→top 交替）
    → 文字與圖片區域像被「光筆逐行掃出」
  • 掃描邊緣發光帶（40px glow），增加現代感
  • Ken Burns：偶數頁 1.0→1.04 放大，奇數頁 1.04→1.0 縮小
  • 淡入 0.3s / 淡出 0.5s（xfade 交叉淡化 0.6s）
"""
import os, sys, subprocess
import imageio_ffmpeg

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE    = r"D:\wi\260612"
ASSETS  = os.path.join(BASE, "pres_pdf_assets")
TMP_DIR = os.path.join(ASSETS, "v2_tmp")
OUT_MP4 = os.path.join(BASE, "hw6_presentation_enhanced.mp4")
FFMPEG  = imageio_ffmpeg.get_ffmpeg_exe()
FFPROBE = FFMPEG.replace("ffmpeg-win", "ffprobe-win")
if not os.path.exists(FFPROBE):
    FFPROBE = FFMPEG.replace("ffmpeg", "ffprobe")

FPS        = 24
FADE_IN    = 0.30
FADE_OUT   = 0.50
XFADE_DUR  = 0.60
W, H       = 1920, 1080
N_SLIDES   = 12
EXTRA_SEC  = 1.20
ZOOM_RANGE = 0.04

# 掃描動畫參數
SCAN_START  = 0.10   # 第一行開始亮起的時間
SCAN_SPREAD = 1.30   # 從頂到底的總延遲（秒）
ROW_DUR     = 0.35   # 每行從暗到亮的過渡時長（cubic ease-out）
GLOW_HALF   = 40.0   # 發光帶半寬（像素）
GLOW_AMP    = 0.30   # 發光亮度增加量（允許輕微過曝）
BASE_DIM    = 0.12   # 未亮起時的最低亮度

os.makedirs(TMP_DIR, exist_ok=True)


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


def build_geq(slide_idx):
    """
    建立 geq filter 的 lum / cb / cr 公式。
    偶數頁：top→bottom（由上往下）
    奇數頁：bottom→top（由下往上）
    """
    top_to_bottom = (slide_idx % 2 == 0)

    Hf = float(H)
    ss = SCAN_START
    sp = SCAN_SPREAD
    rd = ROW_DUR
    gh = GLOW_HALF
    ga = GLOW_AMP
    bd = BASE_DIM

    if top_to_bottom:
        # 行 Y 的亮起起始時間：ss + (Y/H)*sp
        row_start  = f"({ss} + Y/{Hf}*{sp})"
        # 掃描帶中心位置
        sweep_y    = f"max((T - {ss}) / {sp} * {Hf}, 0.0)"
        sweep_dist = f"abs(Y - {sweep_y})"
    else:
        # 反向：底部先亮
        row_start  = f"({ss} + ({Hf}-1-Y)/{Hf}*{sp})"
        sweep_y    = f"({Hf}-1 - max((T - {ss}) / {sp} * {Hf}, 0.0))"
        sweep_dist = f"abs(Y - {sweep_y})"

    # cubic ease-out：p = clamp((T - row_start) / ROW_DUR, 0, 1)
    #                 ease = 1 - (1-p)^3
    p_expr    = f"max(min((T - {row_start}) / {rd}, 1.0), 0.0)"
    ease_expr = f"(1 - pow(1 - ({p_expr}), 3))"
    base_expr = f"({bd} + {1.0 - bd} * {ease_expr})"

    # 發光帶（僅動畫期間，T < ss + sp + rd）
    glow_expr = f"({ga} * max(1.0 - {sweep_dist} / {gh}, 0.0))"

    # 合併：亮度 = clamp(base + glow, 0, 1.2)
    bright_expr = f"min({base_expr} + {glow_expr}, 1.2)"

    lum_formula = f"clip(lum(X,Y)*({bright_expr}), 0, 255)"
    cb_formula  = f"clip(128+(cb(X,Y)-128)*({bright_expr}), 0, 255)"
    cr_formula  = f"clip(128+(cr(X,Y)-128)*({bright_expr}), 0, 255)"

    return lum_formula, cb_formula, cr_formula


# ── 計算時長 ──────────────────────────────────────────────────────────────────
print("計算旁白時長…")
durations = []
for i in range(N_SLIDES):
    mp3 = os.path.join(ASSETS, f"narr_{i:02d}.mp3")
    dur = get_duration(mp3) + EXTRA_SEC
    durations.append(dur)
    print(f"  narr_{i:02d}: {dur:.1f}s")

# ── 逐張編碼 ──────────────────────────────────────────────────────────────────
print("\n編碼各投影片片段（逐行掃描動畫）…")
segment_files = []

for i in range(N_SLIDES):
    png = os.path.join(ASSETS, f"slide_{i:02d}.png")
    mp3 = os.path.join(ASSETS, f"narr_{i:02d}.mp3")
    seg = os.path.join(TMP_DIR, f"v2seg_{i:02d}.mp4")

    if os.path.exists(seg) and os.path.getsize(seg) > 50_000:
        print(f"  [{i+1:02d}/{N_SLIDES}] 已存在，跳過")
        segment_files.append(seg)
        continue

    dur         = durations[i]
    n_frames    = max(1, int(dur * FPS))
    fade_out_st = max(0.0, dur - FADE_OUT - 0.05)

    # Ken Burns
    if i % 2 == 0:
        zoom_expr = f"min(zoom+{ZOOM_RANGE/n_frames:.8f},1.0+{ZOOM_RANGE})"
    else:
        zoom_expr = f"max(zoom-{ZOOM_RANGE/n_frames:.8f},1.0)"

    lum_f, cb_f, cr_f = build_geq(i)

    direction = "↓" if i % 2 == 0 else "↑"
    vf = (
        f"scale={W}:{H},"
        f"zoompan="
        f"z='{zoom_expr}':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d={n_frames}:s={W}x{H}:fps={FPS},"
        f"geq=lum='{lum_f}':cb='{cb_f}':cr='{cr_f}',"
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
    print(f"  [{i+1:02d}/{N_SLIDES}] slide_{i:02d} {direction}  ({dur:.1f}s)…",
          end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
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
        f"{last_v}[{i}:v]xfade=transition=fade:"
        f"duration={xd}:offset={offsets[i-1]:.4f}{out_v}"
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
    "-map", last_v, "-map", "[aout]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "21",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "128k",
    "-movflags", "+faststart",
    tmp_out,
]
r = subprocess.run(cmd, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
if r.returncode != 0:
    print("xfade 失敗，降級 concat…")
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
print(f"  效果：逐行掃描(↓↑交替) + 發光帶 + Ken Burns + xfade")
