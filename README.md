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

**PDF scans need two extra installs**, because reading notation off a scanned image requires an optical music recognition engine that can't ship with the repo:

1. **Java 17+** — [adoptium.net](https://adoptium.net/temurin/releases/)
2. **Audiveris** — [github.com/Audiveris/audiveris/releases](https://github.com/Audiveris/audiveris/releases)

Install both, restart the server, and PDFs will work. The landing page shows a **PDF: ready / unavailable** badge so you can tell whether it was detected.

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
