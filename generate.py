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
