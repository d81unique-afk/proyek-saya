"""Pembuat video YouTube Shorts 30 detik otomatis (gratis).

Alur: ide 4 bagian (Gemini) -> koreksi (Gemini) -> suara per bagian (edge-tts)
      -> klip stok yang dicocokkan ke tiap bagian (Pexels/Pixabay)
      -> efek suara + musik latar (disintesis FFmpeg) -> rakit video vertikal (FFmpeg)
      -> kirim ke Telegram / simpan sebagai artifact.
"""
import asyncio
import datetime
import json
import os
import random
import re
import subprocess
import textwrap
import time

import requests

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
PEXELS_KEY = os.environ.get("PEXELS_API_KEY") or ""
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY") or ""
MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash"
NICHE = os.environ.get("NICHE") or "fakta unik dunia"
TOPIC = (os.environ.get("TOPIC") or "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID") or ""

OUT = "output"
HISTORY = "history.json"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DURATION = 30      # target durasi (detik)
SEGMENTS = 4       # jumlah bagian cerita

ANGLES = [
    "fakta mengejutkan yang jarang diketahui orang",
    "mitos vs fakta",
    "pertanyaan 'tahukah kamu' dengan jawaban tak terduga",
    "perbandingan angka yang bikin kaget",
    "sejarah singkat yang aneh tapi nyata",
    "fakta tentang tubuh manusia atau hewan",
    "fakta Indonesia yang jarang dibahas",
]
STYLES = [
    {"color": "yellow", "box": "black@0.55", "y": "(h-text_h)/2", "size": 72,
     "voice": "id-ID-ArdiNeural", "rate": "+5%"},
    {"color": "white", "box": "0x1a1a1a@0.65", "y": "h*0.68", "size": 68,
     "voice": "id-ID-GadisNeural", "rate": "+8%"},
    {"color": "0x7CFFB2", "box": "black@0.6", "y": "h*0.58", "size": 70,
     "voice": "id-ID-ArdiNeural", "rate": "+10%"},
    {"color": "0xFFB347", "box": "black@0.6", "y": "h*0.72", "size": 66,
     "voice": "id-ID-GadisNeural", "rate": "+3%"},
]
# Akor untuk musik latar (frekuensi Hz): A minor, C major, D minor
CHORDS = [[220.0, 261.63, 329.63], [261.63, 329.63, 392.0], [293.66, 349.23, 440.0]]


def run_ffmpeg(args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + args, check=True)


# ---------------------------------------------------------------- Gemini
def gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
    }
    last = None
    for attempt in range(4):
        r = requests.post(url, headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=90)
        if r.status_code in (429, 500, 503):
            last = r.text
            time.sleep(20 * (attempt + 1))
            continue
        if r.status_code != 200:
            print("Gemini status", r.status_code, r.text[:400])
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        return json.loads(text)
    raise RuntimeError(f"Gemini gagal: {last}")


def make_idea(angle, history):
    prompt = f"""Kamu kreator konten YouTube Shorts berbahasa Indonesia.
Niche kanal: {NICHE}.
Sudut pandang hari ini: {angle}.
Jangan ulangi topik berikut: {json.dumps(history[-40:], ensure_ascii=False)}

Buat satu ide video berdurasi 30 detik. Jawab sebagai objek JSON dengan kunci:
- judul: hook pendek, maksimal 40 karakter
- bagian: daftar TEPAT 4 objek berurutan. Bagian 1 adalah hook yang memancing penasaran dalam 3 detik pertama. Bagian 2 dan 3 adalah isi, masing-masing menjelaskan satu fakta atau poin dengan jelas. Bagian 4 adalah penutup berupa kejutan atau ajakan. Setiap objek punya kunci:
    - teks: narasi bagian itu dalam bahasa Indonesia santai, 15 sampai 20 kata, tanpa emoji dan tanpa tanda kurung. Total keempat teks sekitar 65 sampai 75 kata.
    - visual: daftar 2 frasa pencarian video stok dalam bahasa Inggris (2 sampai 4 kata). Frasa pertama paling spesifik, frasa kedua lebih umum sebagai cadangan. Harus berupa benda, hewan, tempat, atau aktivitas nyata yang disebut atau menggambarkan isi teks bagian itu, misalnya 'octopus underwater', 'human brain scan', 'old library books'. Jangan pakai konsep abstrak seperti 'fact' atau 'mystery'.
- caption: 1 sampai 2 kalimat untuk deskripsi video, diakhiri ajakan subscribe
- hashtag: daftar 4 sampai 5 hashtag tanpa tanda #"""
    return gemini(prompt)


def proofread(idea):
    prompt = f"""Kamu editor bahasa Indonesia dan pemeriksa fakta.
Periksa ide video berikut: ejaan, tata bahasa, nada bicara yang enak didengar, dan keakuratan fakta.
Jika sebuah fakta meragukan, ganti dengan fakta lain yang pasti benar, lalu sesuaikan frasa visual-nya.
Pastikan frasa visual tiap bagian benar-benar cocok dengan isi teks bagian itu.
Struktur harus tetap: bagian berisi tepat 4 objek (teks 15 sampai 20 kata, tanpa emoji, dan visual berisi 2 frasa bahasa Inggris).
Kembalikan JSON dengan kunci yang sama persis: judul, bagian, caption, hashtag.

{json.dumps(idea, ensure_ascii=False)}"""
    fixed = gemini(prompt)
    for k in ("judul", "bagian", "caption", "hashtag"):
        fixed.setdefault(k, idea.get(k))
    return fixed


def keyword_list(raw, n):
    kw = raw or []
    if isinstance(kw, str):
        kw = [k.strip() for k in re.split(r"[,;\n]", kw) if k.strip()]
    kw = [str(k).strip() for k in kw if str(k).strip()]
    if not kw:
        kw = ["nature"]
    i = 0
    while len(kw) < n:
        kw.append(kw[i % len(kw)])
        i += 1
    return kw[:n]


def get_parts(idea):
    """Ubah hasil Gemini menjadi daftar bagian {teks, visual}. Ada cadangan untuk format lama."""
    parts = []
    for b in idea.get("bagian") or []:
        if not isinstance(b, dict):
            continue
        teks = str(b.get("teks", "")).strip()
        vis = b.get("visual") or b.get("kata_kunci") or []
        if isinstance(vis, str):
            vis = [vis]
        vis = [str(v).strip() for v in vis if str(v).strip()] or ["nature"]
        if teks:
            parts.append({"teks": teks, "visual": vis})
    if len(parts) >= 2:
        return parts[:6]

    # Cadangan: format lama (naskah + kata_kunci)
    words = str(idea.get("naskah", "")).split()
    if not words:
        raise RuntimeError("Gemini tidak memberi naskah")
    kws = keyword_list(idea.get("kata_kunci"), SEGMENTS)
    size = max(1, -(-len(words) // SEGMENTS))
    chunks = [" ".join(words[i:i + size]) for i in range(0, len(words), size)]
    return [{"teks": c, "visual": [kws[i % len(kws)]]} for i, c in enumerate(chunks)]


# ---------------------------------------------------------------- Suara
def make_voice(text, style, path):
    try:
        import edge_tts

        async def run():
            await edge_tts.Communicate(text, style["voice"], rate=style["rate"]).save(path)

        asyncio.run(run())
        if os.path.getsize(path) < 1000:
            raise RuntimeError("audio kosong")
    except Exception as e:  # cadangan: gTTS
        print("edge-tts gagal, pakai gTTS:", e)
        from gtts import gTTS

        gTTS(text, lang="id").save(path)


def media_len(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path])
    return float(out)


def plan_timing(lens):
    """Hitung tempo suara dan durasi tiap bagian visual."""
    total = sum(lens)
    tempo = min(max(total / (DURATION - 1.4), 0.85), 1.4)
    speech = [length / tempo for length in lens]
    lead, tail = 0.4, 1.0
    vd = speech[:]
    vd[0] += lead
    vd[-1] += tail
    return tempo, speech, vd, lead


# ---------------------------------------------------------------- Efek suara & musik
def make_sfx(chord):
    """Buat efek suara (whoosh, impact, ding) dan musik latar dengan FFmpeg.
    Kalau ada file sfx/whoosh.mp3, sfx/impact.mp3, sfx/ding.mp3, sfx/music.mp3 di repo,
    file itu dipakai sebagai pengganti."""
    specs = {
        "whoosh": ["-f", "lavfi", "-i", "anoisesrc=d=0.8:c=pink:r=44100:a=0.7",
                   "-af", "highpass=f=300,lowpass=f=5000,afade=t=in:st=0:d=0.4,afade=t=out:st=0.4:d=0.4"],
        "impact": ["-f", "lavfi", "-i",
                   "aevalsrc=exprs='sin(2*PI*110*t)*exp(-4*t)+0.5*sin(2*PI*220*t)*exp(-6*t)':d=0.9:s=44100"],
        "ding": ["-f", "lavfi", "-i",
                 "aevalsrc=exprs='0.6*sin(2*PI*1175*t)*exp(-3*t)+0.4*sin(2*PI*1760*t)*exp(-4*t)':d=1.2:s=44100"],
    }
    tones = "+".join(f"0.2*sin(2*PI*{f}*t)" for f in chord)
    specs["music"] = ["-f", "lavfi", "-i",
                      f"aevalsrc=exprs='(0.7+0.3*sin(2*PI*0.15*t))*({tones})':d=40:s=44100",
                      "-af", "lowpass=f=1400"]
    out = {}
    for name, args in specs.items():
        custom = next((f"sfx/{name}{ext}" for ext in (".mp3", ".wav", ".ogg")
                       if os.path.exists(f"sfx/{name}{ext}")), None)
        if custom:
            out[name] = custom
            continue
        path = f"{OUT}/{name}.wav"
        try:
            run_ffmpeg(args + [path])
            out[name] = path
        except Exception as e:
            print(f"Gagal membuat {name}:", e)
    return out


FMT = "aformat=sample_rates=44100:channel_layouts=stereo"


def build_audio(voices, tempo, lead, vd, sfx, out_path):
    """Gabungkan suara narasi + efek + musik latar menjadi satu file audio."""
    total = sum(vd)
    n = len(voices)
    inputs, parts = [], []
    for i, v in enumerate(voices):
        inputs += ["-i", v]
        parts.append(f"[{i}:a]{FMT}[a{i}]")
    cat = "".join(f"[a{i}]" for i in range(n))
    parts.append(f"{cat}concat=n={n}:v=0:a=1,atempo={tempo:.3f},"
                 f"adelay={int(lead * 1000)}:all=1,apad=whole_dur={total:.2f}[vo]")
    labels = ["[vo]"]

    extras = []
    if sfx.get("impact"):
        extras.append((sfx["impact"], 0.45, 0.0))
    cum = 0.0
    for i in range(1, n):
        cum += vd[i - 1]
        if sfx.get("whoosh"):
            extras.append((sfx["whoosh"], 0.9, max(cum - 0.4, 0.0)))
    if sfx.get("ding"):
        extras.append((sfx["ding"], 0.3, max(total - 1.15, 0.0)))

    idx = n
    for path, gain, delay in extras:
        inputs += ["-i", path]
        parts.append(f"[{idx}:a]{FMT},volume={gain},adelay={int(delay * 1000)}:all=1,"
                     f"apad=whole_dur={total:.2f}[e{idx}]")
        labels.append(f"[e{idx}]")
        idx += 1
    if sfx.get("music"):
        inputs += ["-i", sfx["music"]]
        parts.append(f"[{idx}:a]{FMT},volume=0.15,afade=t=in:st=0:d=1.5,"
                     f"afade=t=out:st={total - 2:.2f}:d=2,apad=whole_dur={total:.2f}[mu]")
        labels.append("[mu]")

    k = len(labels)
    parts.append(f"{''.join(labels)}amix=inputs={k}:duration=longest:dropout_transition=0,"
                 f"volume={k},alimiter=limit=0.95[a]")
    run_ffmpeg(inputs + ["-filter_complex", ";".join(parts), "-map", "[a]",
                         "-t", f"{total:.2f}", "-c:a", "aac", "-b:a", "160k", out_path])


# ---------------------------------------------------------------- Klip stok
def download(url, dest):
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)


def search_pexels(query):
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": PEXELS_KEY},
                     timeout=30, params={"query": query, "orientation": "portrait", "per_page": 15})
    r.raise_for_status()
    out = []
    for v in r.json().get("videos", []):
        files = [f for f in v.get("video_files", [])
                 if f.get("file_type") == "video/mp4" and f.get("height")]
        if not files:
            continue
        big = sorted([f for f in files if f["height"] >= 1920], key=lambda f: f["height"])
        mid = sorted([f for f in files if 1280 <= f["height"] < 1920], key=lambda f: f["height"])
        f = (big or mid or [max(files, key=lambda x: x["height"])])[0]
        slug = v.get("url", "").rstrip("/").split("/")[-1]
        text = re.sub(r"-\d+$", "", slug).replace("-", " ")
        out.append({"id": f"px{v['id']}", "link": f["link"], "w": f.get("width") or 0,
                    "h": f["height"], "dur": v.get("duration", 0), "text": text})
    return out


def search_pixabay(query):
    r = requests.get("https://pixabay.com/api/videos/", timeout=30,
                     params={"key": PIXABAY_KEY, "q": query[:100], "per_page": 20,
                             "safesearch": "true", "video_type": "film"})
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        vids = h.get("videos", {})
        for size in ("large", "medium", "small"):
            v = vids.get(size) or {}
            if v.get("url"):
                out.append({"id": f"pb{h['id']}", "link": v["url"], "w": v.get("width") or 0,
                            "h": v.get("height") or 0, "dur": h.get("duration", 0),
                            "text": h.get("tags", "")})
                break
    return out


def search_candidates(query):
    found = []
    if PEXELS_KEY:
        try:
            found += search_pexels(query)
        except Exception as e:
            print("Pexels gagal:", e)
    if PIXABAY_KEY:
        try:
            found += search_pixabay(query)
        except Exception as e:
            print("Pixabay gagal:", e)
    return found


def score(c, qwords, need):
    twords = set(re.findall(r"[a-z]+", c["text"].lower()))
    s = 3 * len(qwords & twords)
    if c["h"] > c["w"]:
        s += 1            # klip vertikal asli lebih tajam
    if c["dur"] >= need:
        s += 0.5
    return s + random.random() * 0.3


def pick_clip_for(queries, need, dest, used):
    if not (PEXELS_KEY or PIXABAY_KEY):
        raise RuntimeError("Isi PIXABAY_API_KEY (atau PEXELS_API_KEY) di GitHub Secrets")
    qwords = set()
    for q in queries:
        qwords |= {w for w in re.findall(r"[a-z]+", q.lower()) if len(w) > 2}
    cands, seen = [], set()
    for q in queries[:2]:
        for c in search_candidates(q):
            if c["id"] not in seen and c["id"] not in used:
                seen.add(c["id"])
                cands.append(c)
    if not cands:
        cands = [c for c in search_candidates("nature landscape") if c["id"] not in used]
    if not cands:
        raise RuntimeError("Tidak ada klip ditemukan")
    best = max(cands, key=lambda c: score(c, qwords, need))
    print(f"  pilih {best['id']} ({best['w']}x{best['h']}) '{best['text'][:50]}' untuk {queries}")
    download(best["link"], dest)
    used.add(best["id"])


def pick_clips(parts, vd):
    results, used = [], set()
    for i, p in enumerate(parts):
        dest = f"{OUT}/clip_{i}.mp4"
        try:
            pick_clip_for(p["visual"], vd[i], dest, used)
            results.append(dest)
        except Exception as e:
            print(f"Klip {i} gagal: {e}")
            results.append(None)
    ok = [c for c in results if c]
    if not ok:
        raise RuntimeError("Semua klip gagal diunduh")
    return [c or ok[0] for c in results]


def is_landscape(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=s=x:p=0", path]).decode().strip()
    w, h = (int(x) for x in out.split("x")[:2])
    return w > h * 1.05


# ---------------------------------------------------------------- Video
def chunk_words(text):
    chunks, cur = [], []
    for w in text.split():
        trial = " ".join(cur + [w])
        if cur and (len(trial) > 16 or len(cur) >= 3):
            chunks.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def make_subtitles(texts, speech, lead):
    subs, t0 = [], lead
    for text, s in zip(texts, speech):
        chunks = chunk_words(text)
        total = sum(len(c) for c in chunks) or 1
        t, span = t0 + 0.05, max(s - 0.1, 0.5)
        for c in chunks:
            d = span * len(c) / total
            subs.append((c, t, t + d))
            t += d
        t0 += s
    return subs


def seg_filter(i, d, landscape):
    fade = f"fade=t=in:st=0:d=0.25,fade=t=out:st={d - 0.25:.2f}:d=0.25"
    grade = "eq=contrast=1.06:saturation=1.25"
    if landscape:
        # Klip lanskap: tampil utuh di tengah, latar blur memenuhi layar vertikal
        return (f"[{i}:v]split=2[bg{i}][fg{i}];"
                f"[bg{i}]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"boxblur=30:3,eq=brightness=-0.08[b{i}];"
                f"[fg{i}]scale=1080:-2,{grade}[f{i}];"
                f"[b{i}][f{i}]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,{fade},"
                f"setpts=PTS-STARTPTS[v{i}]")
    # Klip vertikal: isi penuh layar dengan geseran pelan
    return (f"[{i}:v]scale=1188:2112:force_original_aspect_ratio=increase,"
            f"crop=1080:1920:x='(iw-1080)/2-30+60*t/{d:.2f}':y='(ih-1920)/2',"
            f"{grade},setsar=1,fps=30,{fade},setpts=PTS-STARTPTS[v{i}]")


def build_video(clips, landscape, vd, audio, subs, title, style, out_path):
    n = len(clips)
    total = sum(vd)
    parts = [seg_filter(i, vd[i], landscape[i]) for i in range(n)]
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[bg]")

    text_filters = ["vignette", "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.18:t=fill"]
    title_path = f"{OUT}/title.txt"
    with open(title_path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.fill(title.upper(), 20))
    text_filters.append(
        f"drawtext=fontfile={FONT}:textfile={title_path}:fontsize=58:fontcolor=white:"
        "borderw=4:bordercolor=black:line_spacing=10:x=(w-text_w)/2:y=170")
    for k, (text, t1, t2) in enumerate(subs):
        path = f"{OUT}/sub_{k}.txt"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.upper())
        text_filters.append(
            f"drawtext=fontfile={FONT}:textfile={path}:fontsize={style['size']}:"
            f"fontcolor={style['color']}:box=1:boxcolor={style['box']}:boxborderw=24:"
            f"x=(w-text_w)/2:y={style['y']}:enable='between(t,{t1:.2f},{t2:.2f})'")
    parts.append(f"[bg]{','.join(text_filters)}[v]")

    cmd = []
    for c, d in zip(clips, vd):
        cmd += ["-stream_loop", "-1", "-t", f"{d:.2f}", "-i", c]
    cmd += ["-i", audio, "-filter_complex", ";".join(parts),
            "-map", "[v]", "-map", f"{n}:a", "-t", f"{total:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_path]
    run_ffmpeg(cmd)


# ---------------------------------------------------------------- Kirim
def send_telegram(video_path, caption):
    if not (TG_TOKEN and TG_CHAT):
        print("Telegram tidak diatur, lewati.")
        return
    with open(video_path, "rb") as fh:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo", timeout=180,
            data={"chat_id": TG_CHAT, "caption": caption[:1000], "supports_streaming": "true"},
            files={"video": fh})
    print("Telegram:", r.status_code, r.text[:200])


def main():
    os.makedirs(OUT, exist_ok=True)
    today = datetime.date.today()
    rnd = random.Random(today.toordinal())
    angle = f"topik khusus dari pemilik kanal: {TOPIC}" if TOPIC else rnd.choice(ANGLES)
    style = rnd.choice(STYLES)

    history = []
    if os.path.exists(HISTORY):
        with open(HISTORY, encoding="utf-8") as fh:
            history = json.load(fh)

    idea = proofread(make_idea(angle, history))
    print("Ide:", json.dumps(idea, ensure_ascii=False))
    parts = get_parts(idea)
    texts = [p["teks"] for p in parts]
    print("Jumlah bagian:", len(parts), "| jumlah kata:", sum(len(t.split()) for t in texts))

    tags = idea.get("hashtag") or []
    if isinstance(tags, str):
        tags = tags.split()
    tags = " ".join("#" + str(h).lstrip("#").replace(" ", "") for h in tags)
    caption = f"{idea['judul']}\n\n{idea.get('caption', '')}\n\n{tags} #shorts"

    # 1) Suara per bagian, lalu hitung waktu tiap bagian
    voices = []
    for i, t in enumerate(texts):
        path = f"{OUT}/voice_{i}.mp3"
        make_voice(t, style, path)
        voices.append(path)
    lens = [media_len(v) for v in voices]
    tempo, speech, vd, lead = plan_timing(lens)
    print("Durasi video:", round(sum(vd), 1), "detik | tempo suara:", round(tempo, 2))

    # 2) Klip yang dicocokkan ke tiap bagian
    clips = pick_clips(parts, vd)
    landscape = [is_landscape(c) for c in clips]

    # 3) Efek suara + musik latar
    sfx = make_sfx(random.choice(CHORDS))
    audio = f"{OUT}/audio_mix.m4a"
    try:
        build_audio(voices, tempo, lead, vd, sfx, audio)
    except Exception as e:
        print("Mix efek suara gagal, pakai suara narasi saja:", e)
        build_audio(voices, tempo, lead, vd, {}, audio)

    # 4) Rakit video
    final = f"{OUT}/video.mp4"
    subs = make_subtitles(texts, speech, lead)
    build_video(clips, landscape, vd, audio, subs, idea["judul"], style, final)

    with open(f"{OUT}/caption.txt", "w", encoding="utf-8") as fh:
        fh.write(caption)
    send_telegram(final, caption)

    history.append(idea["judul"])
    with open(HISTORY, "w", encoding="utf-8") as fh:
        json.dump(history[-60:], fh, ensure_ascii=False, indent=1)
    print("Selesai:", final)


if __name__ == "__main__":
    main()
