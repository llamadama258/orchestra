# Melodra

Upload sheet music PDFs or MusicXML files, get interactive playable scores with MIDI playback.

## Quick Start (Docker)

Prerequisites: [Docker Desktop](https://www.docker.com/products/docker-desktop/)

```bash
git clone https://github.com/llamadama258/orchestra.git
cd orchestra
docker compose up --build
```

Open http://localhost:5000

## Local Development Setup

### Prerequisites

- Python 3.10+
- Java 11+ (only needed for PDF processing)
- Audiveris OMR engine (only needed for PDF processing)

> MusicXML files (.mxl, .xml, .musicxml) work without Java or Audiveris.

### 1. Install Python dependencies

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Install Java (for PDF support)

- **macOS:** `brew install openjdk@17`
- **Ubuntu/Debian:** `sudo apt install openjdk-17-jre-headless`
- **Windows:** Download from https://adoptium.net/temurin/releases/

### 3. Install Audiveris (for PDF support)

Download the installer for your OS from [Audiveris releases](https://github.com/Audiveris/audiveris/releases).

Or place the JAR in the project root and set:
```bash
export AUDIVERIS_JAR=./audiveris.jar
```

### 4. Run

```bash
python app.py
```

Open http://localhost:5000

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `MELODRA_SECRET` | JWT signing secret | `melodra-dev-secret-change-in-prod` |
| `AUDIVERIS_JAR` | Path to Audiveris JAR | Auto-detected |
| `AUDIVERIS_BAT` | Path to Audiveris exe/bat (Windows) | Auto-detected |
| `FLASK_DEBUG` | Enable debug mode (`1`/`0`) | `1` |
| `MELODRA_DB_PATH` | SQLite database path | `./melodra.db` |

## Project Structure

```
app.py          # Flask app, routes, Audiveris integration
auth.py         # JWT authentication, login/signup
db.py           # SQLite database layer
static/         # CSS, JS, OpenSheetMusicDisplay
templates/      # Jinja2 HTML templates
uploads/        # User-uploaded files (gitignored)
outputs/        # Processed scores and MIDI (gitignored)
```
