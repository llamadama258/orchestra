# Melodra

Upload sheet music and hear it played back with a follow-along cursor that tracks the score as it plays.

## Running it

Requires Python 3.10 or newer.

```bash
git clone https://github.com/llamadama258/orchestra.git
cd orchestra
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000**.

That's the whole setup — no database to configure, no environment variables required. The app creates `uploads/`, `outputs/`, and the SQLite database on first run, and you don't need an account to use it.

## Using it

1. Click **Start Your Song**
2. Pick **Solo** (one part) or **Ensemble** (multiple parts)
3. Name the piece and choose your file
4. Wait for processing, then hit **Play**

In the player you can change the instrument, adjust tempo and volume, toggle a metronome and count-in, switch between parts, and export the audio as MP3.

## File formats

| Format | Works out of the box? |
|---|---|
| `.mxl`, `.xml`, `.musicxml` | Yes |
| `.pdf` | Only with extra setup — see below |

**MusicXML needs nothing beyond the steps above.** You can export it from MuseScore, Finale, or Sibelius, or download it from sites like [OpenScore](https://musescore.org/en/openscore).

**PDF scans need two extra installs.** Reading notation off a scanned image requires an optical music recognition engine, which is a standalone Java application and can't be installed by `pip`. See [PDF Support](#pdf-support-optional) below.

## PDF Support (Optional)

Skip this section unless you want to upload scanned PDFs. MusicXML works without it.

Converting a PDF means recognizing staves, noteheads, and rhythms from an image — a process called Optical Music Recognition. Melodra delegates that to [Audiveris](https://github.com/Audiveris/audiveris), an open-source OMR engine that runs on Java.

### 1. Install Java 17 or newer

| Platform | Command |
|---|---|
| macOS | `brew install openjdk@17` |
| Ubuntu / Debian | `sudo apt install openjdk-17-jre-headless` |
| Windows | Installer from [adoptium.net](https://adoptium.net/temurin/releases/) — tick **Add to PATH** |

Verify with `java -version`.

### 2. Install Audiveris

Download the installer for your platform from the [Audiveris releases page](https://github.com/Audiveris/audiveris/releases) — `.msi` for Windows, `.dmg` for macOS, `.deb` for Ubuntu — and run it.

The default install location is fine. Melodra searches the standard paths automatically, including `C:\Program Files\Audiveris\`, `/opt/audiveris/`, your home and Downloads folders, and the project folder itself.

If you installed it somewhere unusual, point at it explicitly:

```bash
export AUDIVERIS_JAR=/path/to/audiveris.jar      # macOS / Linux
setx AUDIVERIS_BAT "C:\path\to\Audiveris.exe"   # Windows
```

### 3. Restart the server

Detection happens at request time, so restart `python app.py` after installing. The landing page shows a **PDF: ready** / **PDF: unavailable** badge, and `http://localhost:5000/check` returns the details:

```json
{"java": true, "audiveris": "C:\\Program Files\\Audiveris\\Audiveris.exe", "pdf_ready": true}
```

### Getting good results

OMR is imperfect and depends heavily on scan quality:

- **300 dpi or higher.** Low-resolution scans are the most common failure — Audiveris reports it can't find staff lines.
- **Clean, printed notation.** Handwritten scores and heavily annotated parts do poorly.
- **Expect a wait.** A few pages takes tens of seconds to a couple of minutes. Results are cached by file hash, so re-uploading the same PDF is instant.
- **Check the result.** Recognition errors carry through to playback. Where you have a choice, MusicXML is always more accurate.

## How it works

```
PDF ──── Audiveris OMR ────┐
                           ├──► music21 ──┬──► score.xml  (notation)
MusicXML ──────────────────┘              └──► part.mid   (playback)
                                                   │
                                          browser ◄┘
                                     OSMD + Tone.js → synced score & audio
```

Uploads are converted on a background thread, with up to 4 parts processed in parallel. PDFs go through Audiveris first to extract MusicXML; results are cached by file hash so re-uploading the same scan is instant. From there [music21](https://web.mit.edu/music21/) parses the score and writes two files: MusicXML for the notation and MIDI for the playback timing.

In the browser, OpenSheetMusicDisplay renders the notation while Tone.js plays the MIDI using real recorded instrument samples streamed from a soundfont CDN. A render loop maps playback time back to score position, which is what keeps the cursor locked to the music even when you change tempo mid-playback.

## Environment variables

All optional.

| Variable | Description | Default |
|---|---|---|
| `MELODRA_SECRET` | JWT signing secret | `melodra-dev-secret-change-in-prod` |
| `FLASK_DEBUG` | Debug mode (`1`/`0`) | `1` |
| `MELODRA_DB_PATH` | SQLite database path | `./melodra.db` |
| `AUDIVERIS_JAR` | Path to Audiveris JAR | Auto-detected |
| `AUDIVERIS_BAT` | Path to Audiveris exe/bat (Windows) | Auto-detected |

## Project structure

```
app.py          # Flask routes, conversion pipeline, Audiveris integration
auth.py         # Optional accounts (JWT); falls back to a guest user
db.py           # SQLite layer
static/         # CSS, JS, OpenSheetMusicDisplay
templates/      # Jinja2 templates — index.html is the player
uploads/        # Uploaded files (gitignored)
outputs/        # Generated MusicXML and MIDI (gitignored)
```

## Built with

Flask · music21 · Audiveris · OpenSheetMusicDisplay · Tone.js
