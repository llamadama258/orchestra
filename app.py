import os
import uuid
import glob
import shutil
import subprocess

from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename
from music21 import converter

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["OUTPUT_FOLDER"] = os.path.join(os.path.dirname(__file__), "outputs")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

ALLOWED_EXTENSIONS = {"mxl", "xml", "musicxml", "pdf"}

# ── Audiveris discovery ────────────────────────────────────────────────────────
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_HOME    = os.path.expanduser("~")

# Candidate exe/bat launchers (binary distribution — preferred on Windows)
_AUDIVERIS_EXE_PATHS = [
    # MSI installer default (5.10.0 on this machine)
    r"C:\Program Files\Audiveris\Audiveris.exe",
    r"C:\Program Files (x86)\Audiveris\Audiveris.exe",
    # .bat variants (older releases)
    r"C:\Program Files\Audiveris\bin\Audiveris.bat",
    r"C:\Program Files (x86)\Audiveris\bin\Audiveris.bat",
    # Manual extraction next to app.py
    os.path.join(_APP_DIR, "Audiveris", "Audiveris.exe"),
    os.path.join(_APP_DIR, "audiveris", "Audiveris.exe"),
    os.path.join(_APP_DIR, "Audiveris", "bin", "Audiveris.bat"),
    r"C:\Audiveris\bin\Audiveris.bat",
    os.path.join(_HOME, "Audiveris", "bin", "Audiveris.bat"),
    os.path.join(_HOME, "Downloads", "Audiveris", "bin", "Audiveris.bat"),
]
_AUDIVERIS_BAT_GLOBS = [
    r"C:\Program Files\Audiveris*\Audiveris.exe",
    r"C:\Program Files (x86)\Audiveris*\Audiveris.exe",
    r"C:\Program Files\Audiveris*\bin\Audiveris.bat",
    os.path.join(_APP_DIR, "Audiveris*", "bin", "Audiveris.bat"),
    r"C:\Audiveris*\bin\Audiveris.bat",
    os.path.join(_HOME, "Downloads", "Audiveris*", "bin", "Audiveris.bat"),
    os.path.join(_HOME, "Audiveris*", "bin", "Audiveris.bat"),
]

# Candidate fat JARs (fallback)
_AUDIVERIS_JAR_PATHS = [
    # MSI installer puts it here in 5.10.0
    r"C:\Program Files\Audiveris\app\audiveris.jar",
    r"C:\Program Files (x86)\Audiveris\app\audiveris.jar",
    os.path.join(_APP_DIR, "Audiveris.jar"),
    os.path.join(_APP_DIR, "audiveris.jar"),
    os.path.join(_APP_DIR, "audiveris", "lib", "Audiveris.jar"),
    os.path.join(_APP_DIR, "Audiveris", "lib", "Audiveris.jar"),
    r"C:\Audiveris\lib\Audiveris.jar",
    r"C:\Program Files\Audiveris\lib\Audiveris.jar",
    os.path.join(_HOME, "Audiveris", "lib", "Audiveris.jar"),
    os.path.join(_HOME, "Downloads", "Audiveris", "lib", "Audiveris.jar"),
]
_AUDIVERIS_JAR_GLOBS = [
    os.path.join(_APP_DIR, "Audiveris*", "lib", "Audiveris.jar"),
    r"C:\Audiveris*\lib\Audiveris.jar",
    os.path.join(_HOME, "Downloads", "Audiveris*", "lib", "Audiveris.jar"),
    os.path.join(_HOME, "Audiveris*", "lib", "Audiveris.jar"),
]


def _first_glob(patterns):
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def find_audiveris():
    """Return (mode, path) where mode is 'exe'/'bat'/'jar', or (None, None)."""
    # Explicit env vars override everything
    env_bat = os.environ.get("AUDIVERIS_BAT", "").strip()
    if env_bat and os.path.isfile(env_bat):
        return ("bat", env_bat)
    env_jar = os.environ.get("AUDIVERIS_JAR", "").strip()
    if env_jar and os.path.isfile(env_jar):
        return ("jar", env_jar)

    # Prefer exe/bat launcher (binary distribution)
    for p in _AUDIVERIS_EXE_PATHS:
        if os.path.isfile(p):
            mode = "bat" if p.endswith(".bat") else "exe"
            return (mode, p)
    found = _first_glob(_AUDIVERIS_BAT_GLOBS)
    if found:
        mode = "bat" if found.endswith(".bat") else "exe"
        return (mode, found)

    # Fallback: fat JAR
    for p in _AUDIVERIS_JAR_PATHS:
        if os.path.isfile(p):
            return ("jar", p)
    found = _first_glob(_AUDIVERIS_JAR_GLOBS)
    if found:
        return ("jar", found)

    return (None, None)


def java_available():
    return shutil.which("java") is not None


_SETUP_MSG = (
    "Audiveris OMR engine not found.\n\n"
    "Setup (one-time):\n"
    "  1. Go to  https://github.com/Audiveris/audiveris/releases\n"
    "  2. Download the BINARY zip — look for 'Audiveris-5.x.x.zip'\n"
    "     (NOT the 'Source code' links at the bottom of the page)\n"
    "  3. Extract the zip to  C:\\Audiveris\\\n"
    "     You should see  C:\\Audiveris\\bin\\Audiveris.bat  inside.\n"
    "  4. Restart this server — it will find Audiveris automatically."
)


def convert_pdf_to_mxl(pdf_path, output_dir):
    """Run Audiveris OMR on pdf_path; return path of the produced .mxl file."""
    if not java_available():
        raise RuntimeError(
            "Java is required for PDF conversion but was not found.\n"
            "  1. Download Java 11+ from  https://adoptium.net/temurin/releases/\n"
            "  2. Run the installer — tick 'Add to PATH'.\n"
            "  3. Restart this server, then try again."
        )

    mode, runner = find_audiveris()
    if runner is None:
        raise RuntimeError(_SETUP_MSG)

    if mode in ("bat", "exe"):
        cmd = [
            runner,
            "-batch", "-export",
            "-output", output_dir,
            "--", pdf_path,
        ]
    else:
        cmd = [
            "java", "-Xmx1g", "-jar", runner,
            "-batch", "-export",
            "-output", output_dir,
            "--", pdf_path,
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Audiveris timed out after 5 minutes. Try a shorter PDF.")
    except FileNotFoundError:
        raise RuntimeError("Could not launch Audiveris — make sure Java is on your PATH.")

    # Audiveris sometimes exits with code 1 even after successfully writing output
    # (export warnings get treated as errors in some builds).
    # So: always check for output first, only raise if nothing was produced.
    pdf_stem = os.path.splitext(os.path.basename(pdf_path))[0]
    combined_output = (result.stderr or "") + (result.stdout or "")

    # Search output_dir and also the directory next to the PDF (Audiveris default fallback)
    search_dirs = [output_dir, os.path.dirname(pdf_path)]
    found_music = None
    for search_root in search_dirs:
        for ext in ("*.mxl", "*.xml"):
            matches = glob.glob(os.path.join(search_root, "**", ext), recursive=True)
            # Prefer the file whose name matches the PDF stem
            for m in matches:
                if pdf_stem.lower() in os.path.basename(m).lower():
                    found_music = m
                    break
            if not found_music and matches:
                found_music = matches[0]
        if found_music:
            break

    if found_music:
        return found_music

    # Nothing produced — give a human-friendly diagnosis
    if "No system found" in combined_output or "flagged as invalid" in combined_output:
        raise RuntimeError(
            "Audiveris could not detect any music staff lines in your PDF.\n\n"
            "This usually means:\n"
            "  • The PDF is a scan at too low a resolution (need 300 dpi or higher)\n"
            "  • The score uses a non-standard layout Audiveris doesn't recognise\n"
            "  • The page has too much surrounding content (margins, decorations)\n\n"
            "Try this:\n"
            "  1. Open the PDF and check it actually shows printed sheet music\n"
            "  2. If it's a scan, re-scan at 300 dpi or higher\n"
            "  3. Export directly from MuseScore as MusicXML (.mxl) for best results"
        )

    if result.returncode != 0:
        tail = combined_output[-800:].strip()
        raise RuntimeError(
            f"Audiveris could not process this PDF (exit code {result.returncode}).\n\n"
            f"{tail}\n\n"
            "Tip: For best results, export directly as MusicXML (.mxl) from MuseScore."
        )

    raise RuntimeError(
        "Audiveris finished but produced no output file.\n"
        "The PDF may not contain music notation Audiveris can recognise."
    )


# ── Helpers ───────────────────────────────────────────────────────────────────
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/check")
def check_deps():
    """Let the frontend display a live readiness badge."""
    java        = java_available()
    mode, runner = find_audiveris()
    return jsonify({
        "java":      java,
        "audiveris": runner,
        "pdf_ready": java and runner is not None,
    })


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part in request."}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    if not allowed_file(file.filename):
        return jsonify({
            "error": (
                "Unsupported file type. "
                "Please upload a PDF or MusicXML file (.pdf, .mxl, .xml, .musicxml)."
            )
        }), 400

    ext        = file.filename.rsplit(".", 1)[1].lower()
    job_id     = uuid.uuid4().hex[:12]
    safe       = secure_filename(file.filename)
    upload_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{job_id}_{safe}")
    file.save(upload_path)

    try:
        export_dir = os.path.join(app.config["OUTPUT_FOLDER"], job_id)
        os.makedirs(export_dir, exist_ok=True)

        parse_path = upload_path

        if ext == "pdf":
            omr_dir = os.path.join(export_dir, "omr_out")
            os.makedirs(omr_dir, exist_ok=True)
            parse_path = convert_pdf_to_mxl(upload_path, omr_dir)

        score = converter.parse(parse_path)

        xml_path  = os.path.join(export_dir, "score.xml")
        score.write("musicxml", fp=xml_path)

        midi_path = os.path.join(export_dir, f"{job_id}.mid")
        score.write("midi", fp=midi_path)

        return jsonify({"midi_url": f"/midi/{job_id}", "mxl_url": f"/mxl/{job_id}"})

    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/mxl/<job_id>")
def serve_mxl(job_id):
    if not job_id.isalnum():
        return jsonify({"error": "Invalid job ID"}), 400
    xml_path = os.path.join(app.config["OUTPUT_FOLDER"], job_id, "score.xml")
    if not os.path.isfile(xml_path):
        return jsonify({"error": "Score file not found"}), 404
    return send_file(xml_path, mimetype="text/xml")


@app.route("/midi/<job_id>")
def serve_midi(job_id):
    if not job_id.isalnum():
        return jsonify({"error": "Invalid job ID"}), 400
    midi_path = os.path.join(app.config["OUTPUT_FOLDER"], job_id, f"{job_id}.mid")
    if not os.path.isfile(midi_path):
        return jsonify({"error": "MIDI file not found"}), 404
    return send_file(midi_path, mimetype="audio/midi")


if __name__ == "__main__":
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["OUTPUT_FOLDER"], exist_ok=True)
    app.run(debug=True, port=5000)
