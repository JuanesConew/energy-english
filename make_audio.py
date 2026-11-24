#!/usr/bin/env python3
"""Creates an MP3 plus exact word timings for every article that has no audio yet.
Uses the free edge-tts library (Microsoft neural voices). Voice and speed can be changed
with the TTS_VOICE and TTS_RATE variables (for example TTS_RATE=-20% reads more slowly)."""
import asyncio, json, os, shutil, subprocess, sys
from pathlib import Path
import edge_tts

DATA = Path("data/articles.json")
AUDIO = Path("audio")
VOICE = os.getenv("TTS_VOICE") or "en-US-AriaNeural"
RATE = os.getenv("TTS_RATE") or "-12%"


async def synth(text, mp3):
    com = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    audio, marks, cursor = bytearray(), [], 0
    async for ch in com.stream():
        if ch["type"] == "audio":
            audio += ch["data"]
        elif ch["type"] == "WordBoundary":
            w = ch["text"]
            i = text.find(w, cursor)          # position of this spoken word in the text
            if i >= 0:
                marks.append([round(ch["offset"] / 10000), i])   # [milliseconds, character index]
                cursor = i + len(w)
    if not audio or len(marks) < 20:
        raise RuntimeError("no audio or too few word timings")
    mp3.write_bytes(bytes(audio))
    return marks


def shrink(mp3):
    """Smaller file (mono, 40 kbps). Timings do not change."""
    if not shutil.which("ffmpeg"):
        return
    tmp = mp3.with_suffix(".tmp.mp3")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-b:a", "40k", str(tmp)])
    if r.returncode == 0 and tmp.exists():
        tmp.replace(mp3)


def main():
    db = json.loads(DATA.read_text("utf-8"))
    AUDIO.mkdir(exist_ok=True)
    tried = made = 0
    for a in db["articles"]:
        mp3 = AUDIO / f"{a['id']}.mp3"
        if a.get("audio") and mp3.exists():
            continue
        tried += 1
        try:
            marks = asyncio.run(synth("\n".join(a["paras"]), mp3))
            shrink(mp3)
            (AUDIO / f"{a['id']}.json").write_text(json.dumps(marks), "utf-8")
            a["audio"] = f"audio/{a['id']}.mp3"
            made += 1
            print(f"audio OK {a['id']}: {len(marks)} words")
        except Exception as e:
            print(f"audio FAILED {a['id']}: {e}")
    keep = {a["id"] for a in db["articles"]}
    for f in AUDIO.glob("*"):                      # delete audio of articles that are no longer kept
        if f.stem not in keep:
            f.unlink()
    DATA.write_text(json.dumps(db, ensure_ascii=False, indent=1), "utf-8")
    if tried and not made:
        sys.exit("No audio could be created.")


if __name__ == "__main__":
    main()
