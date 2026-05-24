"""tools/clip_voice_sample.py — Download & trim a YouTube clip for Chatterbox voice cloning.

Usage:
    python tools/clip_voice_sample.py
    python tools/clip_voice_sample.py "https://youtube.com/watch?v=..."
"""

import os
import subprocess
import sys
import tempfile

OUTPUT_PATH = os.path.join(os.environ.get("APPDATA", ""), "JARVIS", "voice_sample.wav")


def get_ffmpeg() -> str:
    """Return ffmpeg executable path — system PATH or bundled via imageio-ffmpeg."""
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True)
    if result.returncode == 0:
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        print(f"  Using bundled ffmpeg: {path}")
        return path
    except Exception:
        pass
    raise RuntimeError("ffmpeg not found. Run:  winget install ffmpeg  OR  pip install imageio[ffmpeg]")


def check_deps() -> bool:
    ok = True
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        print("  yt-dlp not installed. Run:  pip install yt-dlp")
        ok = False
    try:
        get_ffmpeg()
    except RuntimeError as e:
        print(f"  {e}")
        ok = False
    return ok


def download_audio(url: str, out_path: str) -> bool:
    print(f"\n[1/3] Downloading audio from YouTube…")
    try:
        import yt_dlp
        ffmpeg_exe = get_ffmpeg()
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": out_path,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            }],
            "ffmpeg_location": os.path.dirname(ffmpeg_exe) if ffmpeg_exe != "ffmpeg" else None,
            "quiet": False,
            "no_warnings": False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "unknown")
            duration = info.get("duration", 0)
            print(f"\n  Title    : {title}")
            print(f"  Duration : {int(duration // 60)}m {int(duration % 60)}s  ({duration}s total)")
            return True
    except Exception as e:
        print(f"  Download failed: {e}")
        return False


def get_time(prompt: str) -> str:
    """Ask user for a timestamp. Accepts 12, 1:23, 0:01:23 formats."""
    while True:
        val = input(prompt).strip()
        if not val:
            return "0"
        # Accept bare seconds or mm:ss or hh:mm:ss
        parts = val.replace(".", ":").split(":")
        if all(p.isdigit() for p in parts):
            return val
        print("  Enter seconds (e.g. 12) or mm:ss (e.g. 1:23)")


def trim_clip(raw_wav: str, start: str, duration: str, output: str) -> bool:
    print(f"\n[3/3] Trimming  start={start}s  duration={duration}s  →  {output}")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    cmd = [
        get_ffmpeg(), "-y",
        "-i", raw_wav,
        "-ss", start,
        "-t", duration,
        "-ar", "24000",   # Chatterbox native sample rate
        "-ac", "1",       # mono
        "-sample_fmt", "s16",
        output,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ffmpeg error:\n{result.stderr}")
        return False
    size_kb = os.path.getsize(output) // 1024
    print(f"  Saved {size_kb} KB  →  {output}")
    return True


def play_preview(path: str) -> None:
    """Play the clip via sounddevice so the user can hear it."""
    try:
        import sounddevice as sd
        import soundfile as sf
        data, sr = sf.read(path)
        print(f"\n  Playing preview ({len(data)/sr:.1f}s) …  Ctrl+C to skip")
        sd.play(data, sr)
        sd.wait()
    except Exception as e:
        print(f"  (Preview unavailable: {e})")


def main() -> None:
    print("=" * 60)
    print("  JARVIS Voice Sample Extractor")
    print("=" * 60)

    if not check_deps():
        sys.exit(1)

    url = sys.argv[1] if len(sys.argv) > 1 else input("\nPaste YouTube URL: ").strip()
    if not url:
        print("No URL provided.")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        raw_base = os.path.join(tmp, "raw_audio")
        raw_wav  = raw_base + ".wav"

        if not download_audio(url, raw_base):
            sys.exit(1)

        # yt-dlp may append .wav automatically
        if not os.path.exists(raw_wav):
            candidates = [f for f in os.listdir(tmp) if f.endswith(".wav")]
            if candidates:
                raw_wav = os.path.join(tmp, candidates[0])
            else:
                print("  Could not find downloaded WAV file.")
                sys.exit(1)

        print("\n[2/3] Choose your clip window.")
        print("  Open the video in your browser to find a clean speech segment.")
        print("  Aim for 8–15 seconds with NO music or background noise.\n")

        start    = get_time("  Start time (seconds or mm:ss): ")
        duration = get_time("  Duration   (seconds, 8–15 recommended): ")

        if not trim_clip(raw_wav, start, duration, OUTPUT_PATH):
            sys.exit(1)

    play_preview(OUTPUT_PATH)

    print("\n  Done! Voice sample saved to:")
    print(f"  {OUTPUT_PATH}")
    print("\n  JARVIS will use this for Chatterbox voice cloning on next startup.")
    print("  Restart JARVIS to activate the new voice.\n")


if __name__ == "__main__":
    main()
