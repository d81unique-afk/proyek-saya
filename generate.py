"""Pembuat video YouTube Shorts 10 detik otomatis (gratis).

Alur: ide (Gemini) -> koreksi (Gemini) -> suara (edge-tts) -> klip stok (Pexels)
      -> rakit video (FFmpeg) -> kirim ke Telegram / simpan sebagai artifact.
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
DURATION = 10

# Sudut pandang & gaya visual diputar otomatis tiap hari supaya konten bervariasi.
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

Buat satu ide video 10 detik. Jawab sebagai objek JSON dengan kunci:
- judul: hook pendek, maksimal 40 karakter
- naskah: narasi 18 sampai 24 kata, bahasa Indonesia santai, kalimat pertama langsung menarik perhatian, tanpa emoji
- kata_kunci: 2 sampai 3 kata bahasa Inggris untuk mencari video stok vertikal yang cocok dengan isi naskah (visual konkret)
- caption: 1 sampai 2 kalimat untuk deskripsi video, diakhiri ajakan subscribe
- hashtag: daftar 4 sampai 5 hashtag tanpa tanda #"""
    return gemini(prompt)


def proofread(idea):
    prompt = f"""Kamu editor bahasa Indonesia dan pemeriksa fakta.
Periksa ide video berikut: ejaan, tata bahasa, nada bicara yang enak didengar, dan keakuratan fakta.
Jika sebuah fakta meragukan, ganti dengan fakta lain yang pasti benar.
Naskah harus tetap 18 sampai 24 kata dan tanpa emoji.
Kembalikan JSON dengan kunci yang sama persis: judul, naskah, kata_kunci, caption, hashtag.

{json.dumps(idea, ensure_ascii=False)}"""
    fixed = gemini(prompt)
    for k in ("judul", "naskah", "kata_kunci", "caption", "hashtag"):
        fixed.setdefault(k, idea.get(k))
    return fixed


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


def build_video(clip, audio, script, title, style, out_path):
    dur = audio_len(audio)
    tempo = min(max(1.0, dur / 9.2), 1.6)  # percepat sedikit bila narasi terlalu panjang
    speech = dur / tempo

    filters = [
        "scale=1080:1920:force_original_aspect_ratio=increase",
        "crop=1080:1920", "setsar=1", "fps=30",
        "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.25:t=fill",
    ]

    title_path = f"{OUT}/title.txt"
    with open(title_path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.fill(title.upper(), 20))
    filters.append(
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
        filters.append(
            f"drawtext=fontfile={FONT}:textfile={path}:fontsize={style['size']}:"
            f"fontcolor={style['color']}:box=1:boxcolor={style['box']}:boxborderw=24:"
            f"x=(w-text_w)/2:y={style['y']}:enable='between(t,{t:.2f},{t + d:.2f})'")
        t += d

    graph = f"[0:v]{','.join(filters)}[v];[1:a]atempo={tempo:.3f},apad[a]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", clip, "-i", audio,
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

    tags = idea.get("hashtag") or []
    if isinstance(tags, str):
        tags = tags.split()
    tags = " ".join("#" + str(h).lstrip("#").replace(" ", "") for h in tags)
    caption = f"{idea['judul']}\n\n{idea['caption']}\n\n{tags} #shorts"

    make_voice(idea["naskah"], style, f"{OUT}/voice.mp3")
    pick_clip(idea["kata_kunci"], f"{OUT}/clip.mp4")
    final = f"{OUT}/video.mp4"
    build_video(f"{OUT}/clip.mp4", f"{OUT}/voice.mp3", idea["naskah"], idea["judul"], style, final)

    with open(f"{OUT}/caption.txt", "w", encoding="utf-8") as fh:
        fh.write(caption)
    send_telegram(final, caption)

    history.append(idea["judul"])
    with open(HISTORY, "w", encoding="utf-8") as fh:
        json.dump(history[-60:], fh, ensure_ascii=False, indent=1)
    print("Selesai:", final)


if __name__ == "__main__":
    main()
