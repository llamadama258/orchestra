import os
import uuid
import glob
import shutil
import subprocess
import threading
import time

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


# ── Job store ─────────────────────────────────────────────────────────────────
# job_id -> {progress, status, status_text, error, result}
_jobs = {}
_jobs_lock = threading.Lock()


def _set_job(job_id, **kwargs):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def _run_job(job_id, upload_path, ext, export_dir):
    """Background thread: run conversion and update _jobs[job_id] with progress."""
    def prog(pct, text=None):
        update = {"progress": pct}
        if text:
            update["status_text"] = text
        _set_job(job_id, **update)

    try:
        prog(5, "Saving file\u2026")
        parse_path = upload_path

        if ext == "pdf":
            omr_dir = os.path.join(export_dir, "omr_out")
            os.makedirs(omr_dir, exist_ok=True)
            prog(10, "Launching OMR engine\u2026")
            parse_path = convert_pdf_to_mxl(
                upload_path, omr_dir,
                progress_cb=lambda p: prog(p, "Recognising score\u2026")
            )
            prog(72, "Score detected\u2026")
        else:
            prog(25, "Reading score\u2026")

        prog(75, "Parsing MusicXML\u2026")
        score = converter.parse(parse_path)

        prog(88, "Writing score\u2026")
        xml_path = os.path.join(export_dir, "score.xml")
        score.write("musicxml", fp=xml_path)

        prog(94, "Generating MIDI\u2026")
        midi_path = os.path.join(export_dir, f"{job_id}.mid")
        score.write("midi", fp=midi_path)

        _set_job(job_id,
                 progress=100,
                 status="done",
                 status_text="Done!",
                 result={"midi_url": f"/midi/{job_id}", "mxl_url": f"/mxl/{job_id}"})

    except RuntimeError as exc:
        _set_job(job_id, status="error", error=str(exc))
    except Exception as exc:
        _set_job(job_id, status="error", error=str(exc))


def _run_setup_job(job_id, piece_name, setup_type, parts_info, output_folder):
    """Process each part file sequentially for the setup flow.

    parts_info: list of {name: str, upload_path: str, ext: str}
    """
    total = len(parts_info)
    results = []

    for idx, part in enumerate(parts_info):
        base_pct = int(idx * 100 / total)
        end_pct  = int((idx + 1) * 100 / total)

        sub_id     = uuid.uuid4().hex[:12]
        export_dir = os.path.join(output_folder, sub_id)
        os.makedirs(export_dir, exist_ok=True)

        def prog(pct, text=None, _base=base_pct, _rng=end_pct - base_pct,
                 _i=idx, _t=total):
            scaled = _base + int(pct * _rng / 100)
            update = {"progress": scaled}
            if text:
                update["status_text"] = f"Part {_i + 1}/{_t}: {text}"
            _set_job(job_id, **update)

        try:
            parse_path = part["upload_path"]

            if part["ext"] == "pdf":
                omr_dir = os.path.join(export_dir, "omr_out")
                os.makedirs(omr_dir, exist_ok=True)
                prog(10, "Launching OMR\u2026")
                parse_path = convert_pdf_to_mxl(
                    part["upload_path"], omr_dir,
                    progress_cb=lambda p, _p=prog: _p(int(p * 0.6), "Recognising score\u2026"),
                )
                prog(72, "Score detected\u2026")
            else:
                prog(25, "Reading score\u2026")

            prog(75, "Parsing MusicXML\u2026")
            score = converter.parse(parse_path)

            prog(88, "Writing score\u2026")
            xml_path = os.path.join(export_dir, "score.xml")
            score.write("musicxml", fp=xml_path)

            prog(94, "Generating MIDI\u2026")
            midi_path = os.path.join(export_dir, f"{sub_id}.mid")
            score.write("midi", fp=midi_path)

            results.append({
                "part_name": part["name"],
                "mxl_url":  f"/mxl/{sub_id}",
                "midi_url": f"/midi/{sub_id}",
            })

        except RuntimeError as exc:
            _set_job(job_id, status="error",
                     error=f"Part \u2018{part['name']}\u2019: {exc}")
            return
        except Exception as exc:
            _set_job(job_id, status="error",
                     error=f"Part \u2018{part['name']}\u2019: {exc}")
            return

    _set_job(job_id,
             progress=100,
             status="done",
             status_text="Done!",
             result={"piece_name": piece_name, "type": setup_type, "parts": results})


def convert_pdf_to_mxl(pdf_path, output_dir, progress_cb=None):
    """Run Audiveris OMR on pdf_path; return path of the produced .mxl file.
    Optionally calls progress_cb(pct) with values 12–68 as output lines come in.
    """
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
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace"
        )
    except FileNotFoundError:
        raise RuntimeError("Could not launch Audiveris — make sure Java is on your PATH.")

    lines = []
    deadline = time.monotonic() + 300
    try:
        for line in proc.stdout:
            lines.append(line)
            if time.monotonic() > deadline:
                proc.kill()
                raise RuntimeError("Audiveris timed out after 5 minutes. Try a shorter PDF.")
            if progress_cb:
                # asymptotic: each new line moves progress from 12% towards 68%
                n = len(lines)
                frac = 1.0 - 1.0 / (1.0 + n / 80.0)
                pct = int(12 + frac * 56)
                progress_cb(min(pct, 68))
        proc.wait()
    except RuntimeError:
        raise
    except Exception:
        proc.kill()
        raise

    return_code = proc.returncode
    combined_output = "".join(lines)
    pdf_stem = os.path.splitext(os.path.basename(pdf_path))[0]

    # Search output_dir and also the directory next to the PDF (Audiveris default fallback)
    search_dirs = [output_dir, os.path.dirname(pdf_path)]
    found_music = None
    for search_root in search_dirs:
        for file_ext in ("*.mxl", "*.xml"):
            matches = glob.glob(os.path.join(search_root, "**", file_ext), recursive=True)
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


@app.route("/setup")
def setup():
    return render_template("setup.html")


@app.route("/setup/upload", methods=["POST"])
def setup_upload():
    setup_type = request.form.get("type", "solo")
    piece_name = (request.form.get("piece_name", "Untitled").strip() or "Untitled")

    if setup_type not in ("solo", "ensemble"):
        return jsonify({"error": "Invalid type."}), 400

    parts_info = []

    if setup_type == "solo":
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"error": "No file uploaded."}), 400
        if not allowed_file(f.filename):
            return jsonify({"error": "Unsupported file type."}), 400
        ext         = f.filename.rsplit(".", 1)[1].lower()
        pid         = uuid.uuid4().hex[:12]
        safe        = secure_filename(f.filename)
        upload_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{pid}_{safe}")
        f.save(upload_path)
        parts_info.append({"name": piece_name, "upload_path": upload_path, "ext": ext})
    else:
        i = 0
        while True:
            f = request.files.get(f"part_file_{i}")
            if f is None:
                break
            if not f.filename:
                i += 1
                continue
            if not allowed_file(f.filename):
                return jsonify({"error": f"Part {i + 1}: unsupported file type."}), 400
            name        = (request.form.get(f"part_name_{i}", f"Part {i + 1}").strip()
                           or f"Part {i + 1}")
            ext         = f.filename.rsplit(".", 1)[1].lower()
            pid         = uuid.uuid4().hex[:12]
            safe        = secure_filename(f.filename)
            upload_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{pid}_{safe}")
            f.save(upload_path)
            parts_info.append({"name": name, "upload_path": upload_path, "ext": ext})
            i += 1

    if not parts_info:
        return jsonify({"error": "No valid files uploaded."}), 400

    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "progress": 0,
            "status": "processing",
            "status_text": "Starting\u2026",
            "error": None,
            "result": None,
        }

    t = threading.Thread(
        target=_run_setup_job,
        args=(job_id, piece_name, setup_type, parts_info, app.config["OUTPUT_FOLDER"]),
        daemon=True,
    )
    t.start()
    return jsonify({"job_id": job_id})


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

    ext         = file.filename.rsplit(".", 1)[1].lower()
    job_id      = uuid.uuid4().hex[:12]
    safe        = secure_filename(file.filename)
    upload_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{job_id}_{safe}")
    file.save(upload_path)

    export_dir = os.path.join(app.config["OUTPUT_FOLDER"], job_id)
    os.makedirs(export_dir, exist_ok=True)

    with _jobs_lock:
        _jobs[job_id] = {
            "progress": 0,
            "status": "processing",
            "status_text": "Starting\u2026",
            "error": None,
            "result": None,
        }

    t = threading.Thread(target=_run_job, args=(job_id, upload_path, ext, export_dir), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def job_status(job_id):
    if not job_id.isalnum():
        return jsonify({"error": "Invalid job ID"}), 400
    with _jobs_lock:
        job = dict(_jobs.get(job_id, {}))
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


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
