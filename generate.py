"""Pembuat video YouTube Shorts 30 detik otomatis (gratis).

Alur: ide (Gemini) -> koreksi (Gemini) -> suara (edge-tts) -> 4 klip stok (Pexels/Pixabay)
      -> rakit video vertikal (FFmpeg) -> kirim ke Telegram / simpan sebagai artifact.
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
DURATION = 30      # detik
SEGMENTS = 4       # jumlah klip visual (30 / 4 = 7,5 detik per klip)

# Sudut pandang & gaya visual diputar otomatis supaya konten bervariasi.
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
            print("Gemini status", r.status_code, r.text[:400])  # pesan asli dari Google
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
- naskah: narasi 65 sampai 75 kata, bahasa Indonesia santai. Struktur: kalimat pertama adalah hook yang memancing penasaran dalam 3 detik pertama, lalu 3 bagian isi yang masing-masing menjelaskan satu fakta atau poin dengan jelas, lalu penutup singkat berupa kejutan atau ajakan. Tanpa emoji dan tanpa tanda kurung.
- kata_kunci: daftar tepat 4 frasa pencarian bahasa Inggris (2 sampai 3 kata), satu untuk tiap bagian naskah secara berurutan, berupa visual konkret untuk video stok vertikal yang cocok dengan isi bagian itu
- caption: 1 sampai 2 kalimat untuk deskripsi video, diakhiri ajakan subscribe
- hashtag: daftar 4 sampai 5 hashtag tanpa tanda #"""
    return gemini(prompt)


def proofread(idea):
    prompt = f"""Kamu editor bahasa Indonesia dan pemeriksa fakta.
Periksa ide video berikut: ejaan, tata bahasa, nada bicara yang enak didengar, dan keakuratan fakta.
Jika sebuah fakta meragukan, ganti dengan fakta lain yang pasti benar.
Naskah harus tetap 65 sampai 75 kata, tanpa emoji, dan kata_kunci tetap berupa daftar tepat 4 frasa bahasa Inggris.
Kembalikan JSON dengan kunci yang sama persis: judul, naskah, kata_kunci, caption, hashtag.

{json.dumps(idea, ensure_ascii=False)}"""
    fixed = gemini(prompt)
    for k in ("judul", "naskah", "kata_kunci", "caption", "hashtag"):
        fixed.setdefault(k, idea.get(k))
    return fixed


def keyword_list(idea, n):
    """Pastikan kata_kunci berupa daftar berisi tepat n frasa."""
    kw = idea.get("kata_kunci") or []
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


def audio_len(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path])
    return float(out)


# ---------------------------------------------------------------- Klip stok
def download(url, dest):
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)


def pick_clip_pexels(query, dest):
    headers = {"Authorization": PEXELS_KEY}
    videos = []
    for q in (query, query.split()[0] if query else "nature", "nature"):
        r = requests.get("https://api.pexels.com/videos/search", headers=headers, timeout=30,
                         params={"query": q, "orientation": "portrait", "size": "medium", "per_page": 15})
        r.raise_for_status()
        videos = r.json().get("videos", [])
        if videos:
            break
    if not videos:
        raise RuntimeError("Tidak ada klip dari Pexels")
    video = random.choice(videos)
    files = [f for f in video["video_files"] if f.get("file_type") == "video/mp4" and f.get("height")]
    good = sorted([f for f in files if f["height"] >= 1080], key=lambda f: f["height"])
    chosen = good[0] if good else max(files, key=lambda f: f["height"])
    download(chosen["link"], dest)


def pick_clip_pixabay(query, dest):
    hits = []
    for q in (query, query.split()[0] if query else "nature", "nature"):
        r = requests.get("https://pixabay.com/api/videos/", timeout=30,
                         params={"key": PIXABAY_KEY, "q": q, "per_page": 20, "safesearch": "true"})
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if hits:
            break
    if not hits:
        raise RuntimeError("Tidak ada klip dari Pixabay")
    videos = random.choice(hits)["videos"]
    for size in ("large", "medium", "small"):
        if videos.get(size, {}).get("url"):
            download(videos[size]["url"], dest)  # klip lanskap otomatis di-crop ke vertikal
            return
    raise RuntimeError("Klip Pixabay tidak punya URL")


def pick_clip(query, dest):
    if PEXELS_KEY:
        return pick_clip_pexels(query, dest)
    if PIXABAY_KEY:
        return pick_clip_pixabay(query, dest)
    raise RuntimeError("Isi PIXABAY_API_KEY (atau PEXELS_API_KEY) di GitHub Secrets")


def pick_clips(keywords):
    """Unduh satu klip per kata kunci. Kalau ada yang gagal, pakai klip lain yang berhasil."""
    results = []
    for i, q in enumerate(keywords):
        dest = f"{OUT}/clip_{i}.mp4"
        try:
            pick_clip(q, dest)
            results.append(dest)
        except Exception as e:
            print(f"Klip {i} ('{q}') gagal: {e}")
            results.append(None)
    ok = [p for p in results if p]
    if not ok:
        raise RuntimeError("Semua klip gagal diunduh")
    return [p or ok[0] for p in results]


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


def build_video(clips, audio, script, title, style, out_path):
    n = len(clips)
    seg = DURATION / n
    dur = audio_len(audio)
    # Sesuaikan tempo suara supaya narasi pas mengisi sekitar 30 detik
    tempo = min(max(dur / (DURATION - 1.5), 0.9), 1.4)
    speech = dur / tempo

    # 1) Tiap klip: crop vertikal 9:16, geser pelan (efek bergerak), fade masuk/keluar
    parts = []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale=1296:2304:force_original_aspect_ratio=increase,"
            f"crop=1080:1920:x='(iw-1080)/2-40+80*t/{seg:.2f}':y='(ih-1920)/2',"
            f"setsar=1,fps=30,"
            f"fade=t=in:st=0:d=0.25,fade=t=out:st={seg - 0.25:.2f}:d=0.25,"
            f"setpts=PTS-STARTPTS[v{i}]")
    concat_in = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"{concat_in}concat=n={n}:v=1:a=0[bg]")

    # 2) Gelapkan sedikit, judul di atas, subtitle per frasa
    text_filters = ["drawbox=x=0:y=0:w=iw:h=ih:color=black@0.25:t=fill"]

    title_path = f"{OUT}/title.txt"
    with open(title_path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.fill(title.upper(), 20))
    text_filters.append(
        f"drawtext=fontfile={FONT}:textfile={title_path}:fontsize=58:fontcolor=white:"
        "borderw=4:bordercolor=black:line_spacing=10:x=(w-text_w)/2:y=170")

    chunks = chunk_words(script)
    total = sum(len(c) for c in chunks)
    t, span = 0.3, max(speech - 0.2, 3.0)
    for i, c in enumerate(chunks):
        d = span * len(c) / total
        path = f"{OUT}/sub_{i}.txt"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(c.upper())
        text_filters.append(
            f"drawtext=fontfile={FONT}:textfile={path}:fontsize={style['size']}:"
            f"fontcolor={style['color']}:box=1:boxcolor={style['box']}:boxborderw=24:"
            f"x=(w-text_w)/2:y={style['y']}:enable='between(t,{t:.2f},{t + d:.2f})'")
        t += d

    parts.append(f"[bg]{','.join(text_filters)}[v]")
    parts.append(f"[{n}:a]atempo={tempo:.3f},apad[a]")
    graph = ";".join(parts)

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for c in clips:
        cmd += ["-stream_loop", "-1", "-t", f"{seg:.2f}", "-i", c]
    cmd += ["-i", audio,
            "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", str(DURATION),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True)


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
    print("Jumlah kata naskah:", len(idea["naskah"].split()))

    tags = idea.get("hashtag") or []
    if isinstance(tags, str):
        tags = tags.split()
    tags = " ".join("#" + str(h).lstrip("#").replace(" ", "") for h in tags)
    caption = f"{idea['judul']}\n\n{idea['caption']}\n\n{tags} #shorts"

    make_voice(idea["naskah"], style, f"{OUT}/voice.mp3")
    clips = pick_clips(keyword_list(idea, SEGMENTS))
    final = f"{OUT}/video.mp4"
    build_video(clips, f"{OUT}/voice.mp3", idea["naskah"], idea["judul"], style, final)

    with open(f"{OUT}/caption.txt", "w", encoding="utf-8") as fh:
        fh.write(caption)
    send_telegram(final, caption)

    history.append(idea["judul"])
    with open(HISTORY, "w", encoding="utf-8") as fh:
        json.dump(history[-60:], fh, ensure_ascii=False, indent=1)
    print("Selesai:", final)


if __name__ == "__main__":
    main()
