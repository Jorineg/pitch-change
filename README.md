Pitch Change Web App (Local)

A local Flask web app to browse MP4 videos on your machine, extract audio, preview pitch-shifted versions using SoX, and export a new MP4 that reuses the original video stream and replaces audio.

Requirements:
- macOS (works elsewhere with minor changes)
- ffmpeg and sox installed and in PATH
- Python 3.9+

Setup (system Python)

```
pip3 install --user -r requirements.txt
python3 server.py
```

Open `http://127.0.0.1:5000` in your browser.

Notes
- Audio is extracted to WAV at 48kHz into `temp/audio/`.
- Pitch-shifted WAVs are placed in `temp/pitch/` named with `_<+/-N>.wav`.
- Thumbnails are generated to `temp/thumbs/`.
- Exported MP4s go to your system `Downloads` folder.
- The audio HTTP endpoint supports Range requests, so the seekbar works.
- If a conversion exists already, it is reused.

Limitations / Considerations
- Pitch shifting changes duration slightly due to SoX resampling; muxing uses `-shortest`.
- Source videos with non-standard audio sample rates are normalized to 48kHz in the base WAV.
- If a video lacks an audio stream, extraction will fail.
- Searching large directories can take time on first load; thumbnails are cached.


