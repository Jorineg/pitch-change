Pitch Change Web App (Local)

A local Flask web app to browse MP4 videos on your machine, extract audio, preview pitch‑shifted versions using SoX, and export a new MP4 that reuses the original video stream and replaces the audio.

### Requirements
- **macOS** (tested on macOS; works elsewhere with minor changes)
- **Python 3.9+**
  - On macOS with Homebrew:
    ```bash
    brew install python
    ```
  - Or download an installer from [python.org](https://www.python.org/downloads/).
  - Verify installation:
    ```bash
    python3 --version
    ```
- **ffmpeg** and **sox** installed and available in your PATH
  - On macOS with Homebrew:
    ```bash
    brew install ffmpeg sox
    ```
  - If you do not have Homebrew, see [brew.sh](https://brew.sh).
  - It is also possible to install ffmpeg and SoX without Homebrew, but this might be more difficult. Make sure to add the binaries to your PATH.
  - Verify installation:
    ```bash
    ffmpeg -version
    sox --version
    ```

### Setup (system‑wide installation on macOS)
1. Open the Terminal app.
2. Make sure Python is installed system‑wide (via Homebrew or the Python.org installer). On Apple Silicon, ensure `/opt/homebrew/bin` is in your PATH; on Intel Macs, ensure `/usr/local/bin` is in your PATH.
3. Navigate to the project folder. Use quotes because the folder name contains a space:
   ```bash
   cd "/path/to/pitch change"
   ```
4. Install the Python dependencies system‑wide:
   ```bash
   pip3 install -r requirements.txt
   ```
5. Start the app:
   ```bash
   python3 server.py
   ```
6. Open `http://127.0.0.1:5000` in your web browser.
7. Keep the Terminal window open while you use the app. To stop the app, press `Ctrl+C` in the Terminal.

### Notes for Usage with Presenter (WorshipTools)
- Presenter stores its media files in `~/Library/Application Support/WorshipTools Library/<library-name>`, where `<library-name>` is a long hexadecimal string.
  - Example: `/Users/c2g-team4/Library/Application Support/WorshipTools Library/4a7ff432-dd62-437d-b681-63f98f0ada16`
- Add this path in the UI.
- Media files in this library have filenames that are IDs.
- The web UI will also show these IDs.
- To find a specific file in the UI, you therefore need to know its ID used by Presenter.
- To find the ID in Presenter:
  - Go to the "Medien" tab and search for the file by its displayed name.
  - Click on the "i" to find specific information.
  - Hover over "lokaler pfad" to see the full file path including the filename. You can also click on "lokaler pfad" to open the item in the Finder.
- You can now use the ID to find the media item in this program's web UI (e.g., with Cmd+F).

### General Notes
- Audio is extracted to WAV at 48 kHz into `temp/audio/`.
- Pitch‑shifted WAVs are placed in `temp/pitch/` named with `_<+/-N>.wav`.
- Thumbnails are generated to `temp/thumbs/`.
- Exported MP4s go to your system `Downloads` folder.
- The audio HTTP endpoint supports Range requests, so the seek bar works.
- If a conversion already exists, it is reused.

### Limitations / Considerations
- Pitch shifting changes duration slightly due to SoX resampling; muxing uses `-shortest`.
- Source videos with non‑standard audio sample rates are normalized to 48 kHz in the base WAV.
- If a video lacks an audio stream, extraction will fail.
- Searching large directories can take time on first load; thumbnails are cached.

