"""Pembuat video YouTube Shorts 30 detik otomatis (gratis).

Alur: ide 4 bagian (Gemini) -> koreksi (Gemini) -> suara per bagian (edge-tts)
      -> klip stok yang dicocokkan ke tiap bagian (Pexels/Pixabay)
      -> efek suara + musik latar (disintesis FFmpeg) -> rakit video vertikal (FFmpeg)
      -> kirim ke Telegram / simpan sebagai artifact.
"""
import asyncio
import base64
import datetime
import glob
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
FONT_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
# Font Google (OFL) yang diunduh otomatis; kalau gagal dipakai DejaVu Bold.
# Mau pakai font sendiri? Unggah file .ttf/.otf ke folder fonts/ di repo.
FONT_URLS = {
    "Poppins-ExtraBold": ["https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-ExtraBold.ttf"],
    "LilitaOne-Regular": ["https://raw.githubusercontent.com/google/fonts/main/ofl/lilitaone/LilitaOne-Regular.ttf"],
}
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
    {"color": "yellow", "y": "(h-text_h)/2", "size": 76, "font": "Poppins-ExtraBold",
     "voice": "id-ID-ArdiNeural", "rate": "+5%"},
    {"color": "white", "y": "h*0.68", "size": 88, "font": "LilitaOne-Regular",
     "voice": "id-ID-GadisNeural", "rate": "+8%"},
    {"color": "0x7CFFB2", "y": "h*0.58", "size": 74, "font": "Poppins-ExtraBold",
     "voice": "id-ID-ArdiNeural", "rate": "+10%"},
    {"color": "0xFFD966", "y": "h*0.72", "size": 86, "font": "LilitaOne-Regular",
     "voice": "id-ID-GadisNeural", "rate": "+3%"},
]
# Akor untuk musik latar (frekuensi Hz): A minor, C major, D minor
CHORDS = [[220.0, 261.63, 329.63], [261.63, 329.63, 392.0], [293.66, 349.23, 440.0]]


def run_ffmpeg(args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + args, check=True)


# ---------------------------------------------------------------- Gemini
FALLBACK_MODELS = [m.strip() for m in (os.environ.get("GEMINI_FALLBACK_MODELS") or
                   "gemini-3.5-flash,gemini-2.5-flash,gemini-2.5-flash-lite").split(",") if m.strip()]


def gemini(prompt):
    """Coba model utama, lalu model cadangan (kuota gratis dihitung per model per hari)."""
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
    }
    models = []
    for m in [MODEL] + FALLBACK_MODELS:
        if m not in models:
            models.append(m)
    last = None
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(3):
            r = requests.post(url, headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=90)
            if r.status_code in (429, 500, 503):
                last = r.text
                # Kuota harian habis atau tunggu terlalu lama -> langsung ganti model
                if r.status_code == 429 and ("PerDay" in r.text or "per day" in r.text):
                    print(f"Kuota harian {model} habis, coba model lain")
                    break
                time.sleep(15 * (attempt + 1))
                continue
            if r.status_code in (400, 404):
                last = r.text
                print(f"Model {model} tidak bisa dipakai ({r.status_code}), coba model lain")
                break
            if r.status_code != 200:
                print("Gemini status", r.status_code, r.text[:400])
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
            print("Model dipakai:", model)
            return json.loads(text)
    raise RuntimeError(f"Gemini gagal di semua model: {last}")


def make_idea(angle, history):
    prompt = f"""Kamu kreator konten YouTube Shorts berbahasa Indonesia.
Niche kanal: {NICHE}.
Sudut pandang hari ini: {angle}.
Jangan ulangi topik berikut: {json.dumps(history[-40:], ensure_ascii=False)}

Buat satu ide video berdurasi 30 detik. Jawab sebagai objek JSON dengan kunci:
- judul: hook pendek, maksimal 40 karakter, ditulis dengan huruf kapital biasa seperti kalimat (kapital hanya di awal kalimat dan nama diri, bukan huruf besar semua)
- hook: teks layar 3 sampai 8 kata yang muncul di 3 detik pertama untuk membuat penonton berhenti scroll. Bentuknya pertanyaan yang mengusik atau pernyataan yang mengejutkan dan belum menjawab apa pun (celah rasa penasaran), misalnya 'Kenapa gurita punya 3 jantung?' atau 'Ini yang terjadi 1 detik sebelum tidur'. Tanpa emoji, tanpa huruf besar semua.
- bagian: daftar TEPAT 4 objek berurutan. Bagian 1 adalah hook: kalimat pertamanya harus langsung memancing penasaran (pertanyaan yang tajam, klaim mengejutkan, atau angka yang aneh), JANGAN dimulai dengan sapaan atau pengantar seperti 'Halo' atau 'Tahukah kamu' yang datar, dan JANGAN membocorkan jawabannya; jawabannya baru diberikan di bagian 2 sampai 4. Bagian 4 sebaiknya menutup lingkaran dengan jawaban atau kejutan terakhir yang nyambung dengan hook. Bagian 2 dan 3 adalah isi, masing-masing menjelaskan satu fakta atau poin dengan jelas. Bagian 4 adalah penutup berupa kejutan, kesimpulan, atau pertanyaan untuk direnungkan. DILARANG mengajak penonton untuk like, komentar, subscribe, follow, atau share, baik di narasi maupun caption. Setiap objek punya kunci:
    - teks: narasi bagian itu dalam bahasa Indonesia santai, 15 sampai 20 kata, tanpa emoji dan tanpa tanda kurung, dengan huruf kapital sesuai ejaan sehari-hari (kapital di awal kalimat dan nama diri saja, jangan menulis kata dengan huruf besar semua untuk penekanan). Total keempat teks sekitar 65 sampai 75 kata.
    - shot: daftar TEPAT 2 objek (storyboard bagian itu, urut sesuai narasi), masing-masing punya kunci visual: daftar 2 frasa pencarian video stok dalam bahasa Inggris (2 sampai 4 kata). Frasa pertama paling spesifik, frasa kedua lebih umum sebagai cadangan. Shot pertama menggambarkan awal kalimat, shot kedua menggambarkan akhir narasi bagian itu, jadi gambar berganti mengikuti isi cerita. Harus berupa benda, hewan, tempat, atau aktivitas nyata yang disebut atau menggambarkan isi teks, misalnya 'octopus underwater', 'human brain scan', 'old library books'. Jangan pakai konsep abstrak seperti 'fact' atau 'mystery'. DILARANG memakai visual korek api, lighter, api unggun, lilin, percikan api, atau benda acak yang tidak ada hubungannya dengan isi teks, kecuali topiknya memang tentang itu.
    - gambar (hanya untuk bagian 1): satu kalimat bahasa Inggris yang mendeskripsikan ilustrasi sinematik vertikal yang dramatis dan memancing penasaran untuk 3 detik pertama (subjek utama topik di tengah, close-up, cahaya kuat). Tanpa teks, tanpa logo, tanpa wajah orang nyata.
- caption: 1 sampai 2 kalimat untuk deskripsi video, tanpa ajakan like, komentar, atau subscribe
- hashtag: daftar 4 sampai 5 hashtag tanpa tanda #"""
    return gemini(prompt)


def proofread(idea):
    prompt = f"""Kamu editor bahasa Indonesia dan pemeriksa fakta.
Periksa ide video berikut: ejaan, tata bahasa, nada bicara yang enak didengar, dan keakuratan fakta.
Jika sebuah fakta meragukan, ganti dengan fakta lain yang pasti benar, lalu sesuaikan frasa visual-nya.
Pastikan frasa visual tiap bagian benar-benar cocok dengan isi teks bagian itu.
Hapus semua ajakan like, komentar, subscribe, follow, atau share dari teks dan caption.
Perbaiki huruf kapital agar sesuai ejaan sehari-hari: kapital di awal kalimat dan nama diri saja, singkatan umum (misalnya DNA, NASA) tetap kapital, dan jangan ada kata berhuruf besar semua untuk penekanan.
Struktur harus tetap: bagian berisi tepat 4 objek (teks 15 sampai 20 kata, tanpa emoji, dan shot berisi tepat 2 objek yang masing-masing punya visual berupa 2 frasa bahasa Inggris; bagian 1 juga punya kunci gambar berupa satu kalimat bahasa Inggris untuk ilustrasi hook).
Pastikan kunci hook (teks layar 3 sampai 8 kata, bentuk pertanyaan atau klaim yang memancing penasaran tanpa membocorkan jawaban) tetap ada dan diperbaiki jika lemah. Kalimat pertama bagian 1 harus langsung memancing penasaran, bukan sapaan datar. Ganti visual yang berupa korek api, lighter, lilin, api, atau benda acak yang tidak berhubungan dengan isi teks.
Kembalikan JSON dengan kunci yang sama persis: judul, hook, bagian, caption, hashtag.

{json.dumps(idea, ensure_ascii=False)}"""
    try:
        fixed = gemini(prompt)
    except Exception as e:  # koreksi gagal (mis. kuota): pakai ide apa adanya
        print("Koreksi dilewati:", str(e)[:200])
        return idea
    for k in ("judul", "hook", "bagian", "caption", "hashtag"):
        fixed.setdefault(k, idea.get(k))
    return fixed


CTA_RE = re.compile(
    r"subscribe|berlangganan|langganan|\blike\b|\bkomen\b|komentar|\bfollow\b|\bikuti\b|"
    r"bagikan|\bshare\b|jangan lupa|lonceng", re.I)


def strip_cta(text):
    """Pengaman: buang kalimat yang mengajak like/komen/subscribe/share."""
    sentences = re.split(r"(?<=[.!?])\s+", str(text).strip())
    return " ".join(s for s in sentences if not CTA_RE.search(s)).strip()


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
        teks = natural_case(strip_cta(b.get("teks", "")))
        shots = []
        for sh in b.get("shot") or []:
            v = sh.get("visual") if isinstance(sh, dict) else sh
            if isinstance(v, str):
                v = [v]
            v = [str(x).strip() for x in (v or []) if str(x).strip()]
            if v:
                shots.append(v)
        if not shots:
            vis = b.get("visual") or b.get("kata_kunci") or []
            if isinstance(vis, str):
                vis = [vis]
            vis = [str(v).strip() for v in vis if str(v).strip()] or ["nature"]
            shots = [vis]
        if teks:
            parts.append({"teks": teks, "visual": shots[0], "shots": shots[:2],
                          "gambar": str(b.get("gambar") or "").strip()})
    if len(parts) >= 2:
        return parts[:6]

    # Cadangan: format lama (naskah + kata_kunci)
    words = natural_case(strip_cta(idea.get("naskah", ""))).split()
    if not words:
        raise RuntimeError("Gemini tidak memberi naskah")
    kws = keyword_list(idea.get("kata_kunci"), SEGMENTS)
    size = max(1, -(-len(words) // SEGMENTS))
    chunks = [" ".join(words[i:i + size]) for i in range(0, len(words), size)]
    return [{"teks": c, "visual": [kws[i % len(kws)]], "shots": [[kws[i % len(kws)]]], "gambar": ""}
            for i, c in enumerate(chunks)]


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


# Klip dengan kata ini sering tidak nyambung (mis. korek api); dibuang kecuali memang dicari.
BAD_WORDS = {"lighter", "matchstick", "matches", "match", "matchbox", "flame", "fire",
             "candle", "spark", "sparkler", "cigarette", "smoke", "burning", "bonfire"}


def score(c, qwords, need):
    twords = set(re.findall(r"[a-z]+", c["text"].lower()))
    s = 3 * len(qwords & twords)
    if (twords & BAD_WORDS) - qwords:
        s -= 100          # hindari klip korek api / api yang tidak diminta
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
    scored = [(score(c, qwords, need), c) for c in cands]
    good = [sc for sc in scored if sc[0] > -50]
    best = max(good or scored, key=lambda x: x[0])[1]
    print(f"  pilih {best['id']} ({best['w']}x{best['h']}) '{best['text'][:50]}' untuk {queries}")
    download(best["link"], dest)
    used.add(best["id"])


IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL") or "gemini-2.5-flash-image"


def gen_image(prompt, dest):
    """Buat ilustrasi hook vertikal 9:16 dengan Gemini. Gagal -> error (pemanggil pakai klip stok)."""
    full = (f"{prompt}. Vertical 9:16 cinematic illustration, dramatic lighting, high contrast, "
            "sharp focus, subject centered, empty space at top and center. "
            "No text, no letters, no watermark, no logo, no real person's face.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{IMAGE_MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": full}]}],
            "generationConfig": {"responseModalities": ["IMAGE"],
                                 "imageConfig": {"aspectRatio": "9:16"}}}
    for attempt in range(3):
        r = requests.post(url, headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=120)
        if r.status_code in (429, 500, 503):
            time.sleep(15 * (attempt + 1))
            continue
        r.raise_for_status()
        for cand in r.json().get("candidates", []):
            for part in (cand.get("content") or {}).get("parts", []):
                data = (part.get("inlineData") or part.get("inline_data") or {}).get("data")
                if data:
                    with open(dest, "wb") as fh:
                        fh.write(base64.b64decode(data))
                    return
        raise RuntimeError("Gemini tidak mengembalikan gambar")
    raise RuntimeError("Gemini gambar gagal (kuota/server)")


def plan_shots(parts, vd):
    """Storyboard: pecah tiap bagian jadi beberapa shot. Shot pertama video = gambar AI (hook)."""
    shots = []
    for i, p in enumerate(parts):
        qs = (p.get("shots") or [p["visual"]])[:2]
        d = vd[i]
        if i == 0 and p.get("gambar"):
            hook_d = min(3.0, d * 0.45)
            shots.append({"seg": i, "kind": "image", "dur": hook_d,
                          "prompt": p["gambar"], "queries": qs[0]})
            d -= hook_d
        for q in qs:
            shots.append({"seg": i, "kind": "clip", "dur": d / len(qs), "queries": q})
    return shots


def pick_shots(shots):
    results, used = [], set()
    for k, sh in enumerate(shots):
        try:
            if sh["kind"] == "image":
                dest = f"{OUT}/shot_{k}.png"
                try:
                    gen_image(sh["prompt"], dest)
                    print(f"  shot {k}: gambar AI untuk hook")
                    results.append(dest)
                    continue
                except Exception as e:
                    print(f"Gambar hook gagal, pakai klip stok: {e}")
            dest = f"{OUT}/clip_{k}.mp4"
            pick_clip_for(sh["queries"], sh["dur"], dest, used)
            results.append(dest)
        except Exception as e:
            print(f"Shot {k} gagal: {e}")
            results.append(None)
    ok = [c for c in results if c]
    if not ok:
        raise RuntimeError("Semua shot gagal dibuat")
    return [c or ok[0] for c in results]


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
def natural_case(text):
    """Pengaman: kalau teks berhuruf besar semua, ubah ke huruf kapital biasa (awal kalimat saja)."""
    text = str(text).strip()
    letters = [c for c in text if c.isalpha()]
    if len(letters) > 3 and text.isupper():
        text = text.lower()
        text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text


def get_font(name):
    """Urutan: font di folder fonts/ (milikmu) -> unduh font Google -> DejaVu Bold."""
    mine = sorted(glob.glob("fonts/*.ttf") + glob.glob("fonts/*.otf"))
    if mine:
        return os.path.abspath(mine[0])
    dest = os.path.abspath(f"{OUT}/fonts/{name}.ttf")
    if os.path.exists(dest) and os.path.getsize(dest) > 20000:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for url in FONT_URLS.get(name, []):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            data = r.content
            if len(data) > 20000 and data[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
                with open(dest, "wb") as fh:
                    fh.write(data)
                return dest
            print("Font", name, "tidak valid dari", url)
        except Exception as e:
            print("Unduh font gagal:", e)
    print(f"Pakai font cadangan untuk {name}")
    return FONT_FALLBACK


def chunk_words(text):
    chunks, cur = [], []
    for w in text.split():
        trial = " ".join(cur + [w])
        if cur and (len(trial) > 18 or len(cur) >= 3):
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
    fade = f"fade=t=in:st=0:d=0.15,fade=t=out:st={d - 0.15:.2f}:d=0.15"
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


def build_video(clips, landscape, vd, audio, subs, title, hook, style, out_path):
    n = len(clips)
    total = sum(vd)
    parts = [seg_filter(i, vd[i], landscape[i]) for i in range(n)]
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[bg]")

    text_filters = ["vignette", "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.18:t=fill"]
    font = get_font(style["font"])
    title_path = f"{OUT}/title.txt"
    with open(title_path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.fill(natural_case(title), 22))
    # Hook besar di detik awal: berhenti scroll + bikin penasaran, lalu judul kecil menggantikan
    hook_end = 3.0
    hook_path = f"{OUT}/hook.txt"
    with open(hook_path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.fill(natural_case(hook or title), 16))
    text_filters.append(
        f"drawtext=fontfile={font}:textfile={hook_path}:fontsize=100:fontcolor=yellow:"
        "borderw=9:bordercolor=black:box=1:boxcolor=black@0.45:boxborderw=34:"
        "line_spacing=14:x=(w-text_w)/2:y=h*0.20:"
        f"alpha='min(1,t/0.2)':enable='lt(t,{hook_end})'")
    text_filters.append(
        f"drawtext=fontfile={font}:textfile={title_path}:fontsize=66:fontcolor=white:"
        "borderw=6:bordercolor=black:shadowx=3:shadowy=4:shadowcolor=black@0.6:"
        f"line_spacing=10:x=(w-text_w)/2:y=170:enable='gte(t,{hook_end})'")
    for k, (text, t1, t2) in enumerate(subs):
        path = f"{OUT}/sub_{k}.txt"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        text_filters.append(
            f"drawtext=fontfile={font}:textfile={path}:fontsize={style['size']}:"
            f"fontcolor={style['color']}:borderw=8:bordercolor=black@0.95:"
            f"shadowx=3:shadowy=5:shadowcolor=black@0.6:"
            f"x=(w-text_w)/2:y={style['y']}:enable='between(t,{t1:.2f},{t2:.2f})'")
    parts.append(f"[bg]{','.join(text_filters)}[v]")

    cmd = []
    for c, d in zip(clips, vd):
        if c.endswith(".png"):
            cmd += ["-loop", "1", "-framerate", "30", "-t", f"{d:.2f}", "-i", c]
        else:
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
    idea["judul"] = natural_case(idea.get("judul", ""))
    print("Ide:", json.dumps(idea, ensure_ascii=False))
    parts = get_parts(idea)
    texts = [p["teks"] for p in parts]
    print("Jumlah bagian:", len(parts), "| jumlah kata:", sum(len(t.split()) for t in texts))

    tags = idea.get("hashtag") or []
    if isinstance(tags, str):
        tags = tags.split()
    tags = " ".join("#" + str(h).lstrip("#").replace(" ", "") for h in tags)
    caption = f"{idea['judul']}\n\n{strip_cta(idea.get('caption', ''))}\n\n{tags} #shorts"

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
    shots = plan_shots(parts, vd)
    with open(f"{OUT}/storyboard.json", "w", encoding="utf-8") as fh:
        json.dump({"hook": idea.get("hook"), "shots": shots}, fh, ensure_ascii=False, indent=1)
    print("Storyboard:", [(x["seg"], x["kind"], round(x["dur"], 1), x["queries"]) for x in shots])
    clips = pick_shots(shots)
    sd = [x["dur"] for x in shots]
    landscape = [False if c.endswith(".png") else is_landscape(c) for c in clips]

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
    hook = natural_case(strip_cta(idea.get("hook") or "")) or idea["judul"]
    build_video(clips, landscape, sd, audio, subs, idea["judul"], hook, style, final)

    with open(f"{OUT}/caption.txt", "w", encoding="utf-8") as fh:
        fh.write(caption)
    send_telegram(final, caption)

    history.append(idea["judul"])
    with open(HISTORY, "w", encoding="utf-8") as fh:
        json.dump(history[-60:], fh, ensure_ascii=False, indent=1)
    print("Selesai:", final)


if __name__ == "__main__":
    main()
