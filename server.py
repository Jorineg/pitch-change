import base64
import json
import logging
import mimetypes
import os
import pathlib
import shlex
import shutil
import subprocess
import threading
import wave
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, url_for


# ----- Configuration -----
APP_ROOT = pathlib.Path(__file__).parent.resolve()
TEMP_DIR = APP_ROOT / "temp"
THUMBS_DIR = TEMP_DIR / "thumbs"
AUDIO_DIR = TEMP_DIR / "audio"
PITCH_DIR = TEMP_DIR / "pitch"
CONFIG_FILE = APP_ROOT / "config.json"

DOWNLOADS_DIR = pathlib.Path(os.path.expanduser("~/Downloads"))


def ensure_dirs() -> None:
    for p in [TEMP_DIR, THUMBS_DIR, AUDIO_DIR, PITCH_DIR, DOWNLOADS_DIR]:
        p.mkdir(parents=True, exist_ok=True)


ensure_dirs()


app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ----- Utilities -----


def b64url_encode_path(path_str: str) -> str:
    raw = path_str.encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii")
    return b64.rstrip("=")


def b64url_decode_path(b64_id: str) -> str:
    padding = "=" * (-len(b64_id) % 4)
    raw = base64.urlsafe_b64decode(b64_id + padding)
    return raw.decode("utf-8")


def run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
    logger.info("Running: %s", " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate()
    return proc.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def ffprobe_duration_seconds(path: pathlib.Path) -> Optional[float]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    code, out, _ = run_cmd(cmd)
    if code != 0:
        return None
    try:
        return float(out.strip())
    except Exception:
        return None


def human_readable_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "Unknown"
    seconds = int(round(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def generate_thumbnail(video_path: pathlib.Path, thumb_path: pathlib.Path) -> None:
    if thumb_path.exists():
        return
    # Try to capture a frame at 10% duration, fallback to 1s if unknown
    # dur = ffprobe_duration_seconds(video_path) or 10.0
    # ts = max(1.0, dur * 0.1)
    ts = 1.0
    # Generate thumbnail
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(ts),
        "-i",
        str(video_path),
        "-vframes",
        "1",
        "-q:v",
        "2",
        str(thumb_path),
    ]
    code, _, err = run_cmd(cmd)
    if code != 0:
        logger.warning("Thumbnail generation failed for %s: %s", video_path, err)


def wav_duration_seconds(path: pathlib.Path) -> Optional[float]:
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate:
                return frames / float(rate)
            return None
    except Exception:
        return None


def extract_audio_wav(video_path: pathlib.Path, audio_out: pathlib.Path) -> None:
    if audio_out.exists():
        return
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "48000",
        str(audio_out),
    ]
    code, _, err = run_cmd(cmd)
    if code != 0:
        raise RuntimeError(f"ffmpeg failed to extract audio: {err}")


def sox_pitch_shift_wav(input_wav: pathlib.Path, output_wav: pathlib.Path, semitones: int) -> None:
    if output_wav.exists():
        return
    cents = int(semitones) * 100
    cmd = [
        "sox",
        "-G",  # guard against clipping
        str(input_wav),
        str(output_wav),
        "pitch",
        str(cents),
    ]
    code, _, err = run_cmd(cmd)
    if code != 0:
        raise RuntimeError(f"SoX failed to pitch shift: {err}")


def mux_video_with_audio(video_path: pathlib.Path, audio_wav: pathlib.Path, output_path: pathlib.Path) -> None:
    # Copy video stream, encode audio to AAC, don't re-encode video
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_wav),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-shortest",
        str(output_path),
    ]
    code, _, err = run_cmd(cmd)
    if code != 0:
        raise RuntimeError(f"ffmpeg failed to mux: {err}")


def audio_base_path_for_id(file_id: str) -> pathlib.Path:
    return AUDIO_DIR / f"{file_id}.wav"


def pitched_audio_path_for_id(file_id: str, semitones: int) -> pathlib.Path:
    sign = "+" if semitones >= 0 else "-"
    return PITCH_DIR / f"{file_id}_{sign}{abs(int(semitones))}.wav"


def thumb_path_for_id(file_id: str) -> pathlib.Path:
    return THUMBS_DIR / f"{file_id}.jpg"


def get_video_path_from_id_or_404(file_id: str) -> pathlib.Path:
    try:
        path_str = b64url_decode_path(file_id)
    except Exception:
        return abort_json(400, "Invalid video id")
    p = pathlib.Path(path_str)
    if not p.exists():
        return abort_json(404, "Video not found")
    return p


def abort_json(status: int, message: str) -> Response:
    resp = jsonify({"error": message})
    resp.status_code = status
    return resp


def iter_mp4_files(root: pathlib.Path) -> List[pathlib.Path]:
    results: List[pathlib.Path] = []
    if not root.exists():
        return results
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(".mp4"):
                results.append(pathlib.Path(dirpath) / name)
    return results


# In-memory state for search paths, persisted to CONFIG_FILE
SEARCH_PATHS: List[str] = []


def load_search_paths() -> None:
    global SEARCH_PATHS
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                paths = data.get("paths", [])
                # Normalize to absolute, unique order preserved
                seen = set()
                normalized: List[str] = []
                for p in paths:
                    try:
                        rp = str(pathlib.Path(p).resolve())
                    except Exception:
                        rp = str(p)
                    if rp not in seen:
                        normalized.append(rp)
                        seen.add(rp)
                SEARCH_PATHS = normalized
    except Exception as e:
        logger.warning("Failed to load config: %s", e)


def save_search_paths() -> None:
    try:
        data = {"paths": SEARCH_PATHS}
        tmp = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, CONFIG_FILE)
    except Exception as e:
        logger.warning("Failed to save config: %s", e)


# Load persisted paths at startup
load_search_paths()


# ----- Range file serving for audio seeking -----


def send_file_range(path: pathlib.Path, mimetype: Optional[str] = None) -> Response:
    file_size = path.stat().st_size
    range_header = request.headers.get("Range")
    if not range_header:
        resp = send_file(str(path), mimetype=mimetype, conditional=True)
        try:
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["Accept-Ranges"] = "bytes"
        except Exception:
            pass
        return resp

    # Parse Range: bytes=start-end
    try:
        units, _, range_spec = range_header.partition("=")
        if units != "bytes":
            raise ValueError("Only bytes supported")
        start_s, _, end_s = range_spec.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
        if end >= file_size:
            end = file_size - 1
        length = end - start + 1
    except Exception:
        # Malformed range; fall back to full file with no-store headers
        resp = send_file(str(path), mimetype=mimetype, conditional=True)
        try:
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["Accept-Ranges"] = "bytes"
        except Exception:
            pass
        return resp

    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(length)

    rv = Response(data, 206, mimetype=mimetype, direct_passthrough=True)
    rv.headers.add("Content-Range", f"bytes {start}-{end}/{file_size}")
    rv.headers.add("Accept-Ranges", "bytes")
    rv.headers.add("Content-Length", str(length))
    rv.headers.add("Cache-Control", "no-store")
    return rv


# ----- Routes -----


@app.get("/")
def index() -> Response:
    return render_template("index.html")


@app.get("/detail/<file_id>")
def detail_page(file_id: str) -> Response:
    return render_template("detail.html", file_id=file_id)


@app.get("/api/paths")
def list_paths() -> Response:
    return jsonify({"paths": SEARCH_PATHS})


@app.post("/api/paths")
def add_path() -> Response:
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    if not path:
        return abort_json(400, "Missing 'path'")
    p = str(pathlib.Path(path).resolve())
    if p not in SEARCH_PATHS:
        SEARCH_PATHS.append(p)
        save_search_paths()
    return jsonify({"ok": True, "paths": SEARCH_PATHS})


@app.delete("/api/paths")
def remove_path() -> Response:
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    if not path:
        return abort_json(400, "Missing 'path'")
    p = str(pathlib.Path(path).resolve())
    try:
        SEARCH_PATHS.remove(p)
        save_search_paths()
    except ValueError:
        pass
    return jsonify({"ok": True, "paths": SEARCH_PATHS})


@app.get("/api/videos")
def list_videos() -> Response:
    videos = []
    for root in SEARCH_PATHS:
        for path in iter_mp4_files(pathlib.Path(root)):
            file_id = b64url_encode_path(str(path))
            thumb_path = thumb_path_for_id(file_id)
            try:
                generate_thumbnail(path, thumb_path)
            except Exception as e:
                logger.warning("Thumbnail generation error: %s", e)
            videos.append(
                {
                    "id": file_id,
                    "filename": path.name,
                    "thumbnail": url_for("get_thumb", file_id=file_id),
                }
            )
    # Deduplicate by id in case overlapping paths
    seen = set()
    unique = []
    for v in videos:
        if v["id"] not in seen:
            unique.append(v)
            seen.add(v["id"])
    return jsonify({"videos": unique})


@app.get("/thumbs/<file_id>.jpg")
def get_thumb(file_id: str) -> Response:
    p = thumb_path_for_id(file_id)
    if not p.exists():
        # Attempt regeneration if possible
        try:
            video_path = pathlib.Path(b64url_decode_path(file_id))
            generate_thumbnail(video_path, p)
        except Exception:
            pass
    if not p.exists():
        return abort_json(404, "Thumbnail not found")
    return send_file(str(p), mimetype="image/jpeg")


@app.post("/api/extract-audio")
def api_extract_audio() -> Response:
    data = request.get_json(silent=True) or {}
    file_id = data.get("id")
    if not file_id:
        return abort_json(400, "Missing 'id'")
    video_path = get_video_path_from_id_or_404(file_id)
    if isinstance(video_path, Response):
        return video_path
    audio_out = audio_base_path_for_id(file_id)
    try:
        extract_audio_wav(video_path, audio_out)
    except Exception as e:
        return abort_json(500, str(e))

    # Prefer native WAV duration if ffprobe is unavailable
    duration = wav_duration_seconds(audio_out) or ffprobe_duration_seconds(audio_out)
    return jsonify(
        {
            "ok": True,
            "audio_url": url_for("serve_audio", id=file_id, pitch=0),
            "duration_seconds": duration,
            "duration": human_readable_duration(duration),
            "filename": video_path.name,
        }
    )


@app.get("/api/audio-meta/<file_id>")
def api_audio_meta(file_id: str) -> Response:
    audio_path = audio_base_path_for_id(file_id)
    if not audio_path.exists():
        return abort_json(404, "Audio not extracted")
    duration = wav_duration_seconds(audio_path) or ffprobe_duration_seconds(audio_path)
    return jsonify(
        {
            "duration_seconds": duration,
            "duration": human_readable_duration(duration),
        }
    )


@app.get("/api/video-info/<file_id>")
def api_video_info(file_id: str) -> Response:
    video_path = get_video_path_from_id_or_404(file_id)
    if isinstance(video_path, Response):
        return video_path
    return jsonify({
        "filename": video_path.name,
        "path": str(video_path),
        "thumbnail": url_for("get_thumb", file_id=file_id),
    })


@app.get("/audio")
def serve_audio() -> Response:
    file_id = request.args.get("id")
    pitch = int(request.args.get("pitch", "0"))
    if not file_id:
        return abort_json(400, "Missing 'id'")
    if pitch == 0:
        p = audio_base_path_for_id(file_id)
    else:
        p = pitched_audio_path_for_id(file_id, pitch)
    if not p.exists():
        return abort_json(404, "Audio not found")
    return send_file_range(p, mimetypes.guess_type(str(p))[0] or "audio/wav")


@app.post("/api/pitch")
def api_pitch() -> Response:
    data = request.get_json(silent=True) or {}
    file_id = data.get("id")
    semitones = int(data.get("semitones", 0))
    if file_id is None:
        return abort_json(400, "Missing 'id'")
    if semitones < -8 or semitones > 8:
        return abort_json(400, "'semitones' must be between -8 and 8")
    base_audio = audio_base_path_for_id(file_id)
    if not base_audio.exists():
        return abort_json(400, "Base audio not extracted yet")
    out = pitched_audio_path_for_id(file_id, semitones)
    try:
        sox_pitch_shift_wav(base_audio, out, semitones)
    except Exception as e:
        return abort_json(500, str(e))
    duration = (
        wav_duration_seconds(out)
        or wav_duration_seconds(base_audio)
        or ffprobe_duration_seconds(out)
        or ffprobe_duration_seconds(base_audio)
    )
    return jsonify(
        {
            "ok": True,
            "audio_url": url_for("serve_audio", id=file_id, pitch=semitones),
            "duration_seconds": duration,
            "duration": human_readable_duration(duration),
        }
    )


@app.post("/api/store-video")
def api_store_video() -> Response:
    data = request.get_json(silent=True) or {}
    file_id = data.get("id")
    semitones = int(data.get("semitones", 0))
    if file_id is None:
        return abort_json(400, "Missing 'id'")
    video_path = get_video_path_from_id_or_404(file_id)
    if isinstance(video_path, Response):
        return video_path
    # Determine which audio to use
    audio_path = (
        pitched_audio_path_for_id(file_id, semitones)
        if semitones != 0
        else audio_base_path_for_id(file_id)
    )
    if not audio_path.exists():
        return abort_json(400, "Requested audio not available. Generate it first.")
    base = video_path.stem
    sign = "+" if semitones >= 0 else "-"
    out_name = f"{base}_{sign}{abs(int(semitones))}.mp4"
    out_path = DOWNLOADS_DIR / out_name
    try:
        mux_video_with_audio(video_path, audio_path, out_path)
    except Exception as e:
        return abort_json(500, str(e))
    return jsonify({"ok": True, "output_path": str(out_path)})


# ----- Static assets -----


@app.get("/healthz")
def healthz() -> Response:
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)


