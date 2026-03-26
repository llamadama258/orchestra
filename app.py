import os
import json
import platform
import uuid
import glob
import hashlib
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, request, jsonify, render_template, send_file, g, redirect
from werkzeug.utils import secure_filename
from music21 import converter, environment

from db import (init_db, create_project, get_project,
                get_user_projects_enriched, record_play, get_recent_plays,
                toggle_favorite, delete_project, rename_project)
from auth import auth_bp, login_required, get_current_user_from_cookie

# ── music21 optimization: disable unnecessary analysis ────────────────────────
_m21env = environment.Environment()
_m21env['autoDownload'] = 'deny'          # don't fetch remote resources
_m21env['warnings'] = 0                   # suppress warnings overhead

app = Flask(__name__)
app.secret_key = os.environ.get("MELODRA_SECRET", "melodra-dev-secret-change-in-prod")
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["OUTPUT_FOLDER"] = os.path.join(os.path.dirname(__file__), "outputs")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

app.register_blueprint(auth_bp)


@app.context_processor
def inject_user():
    return {"current_user": get_current_user_from_cookie()}

ALLOWED_EXTENSIONS = {"mxl", "xml", "musicxml", "pdf"}

# ── Audiveris discovery ────────────────────────────────────────────────────────
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_HOME    = os.path.expanduser("~")
_IS_WINDOWS = platform.system() == "Windows"


def _first_glob(patterns):
    for pat in patterns:
        matches = glob.glob(pat)
        if matches:
            return matches[0]
    return None


def _get_audiveris_paths():
    """Return (exe_paths, exe_globs, jar_paths, jar_globs) for the current OS."""
    exe_paths = [
        os.path.join(_APP_DIR, "Audiveris", "Audiveris.exe"),
        os.path.join(_APP_DIR, "audiveris", "Audiveris.exe"),
        os.path.join(_APP_DIR, "Audiveris", "bin", "Audiveris.bat"),
    ]
    exe_globs = [
        os.path.join(_APP_DIR, "Audiveris*", "bin", "Audiveris.bat"),
    ]
    jar_paths = [
        os.path.join(_APP_DIR, "audiveris.jar"),
        os.path.join(_APP_DIR, "Audiveris.jar"),
        os.path.join(_APP_DIR, "audiveris", "lib", "Audiveris.jar"),
        os.path.join(_APP_DIR, "Audiveris", "lib", "Audiveris.jar"),
    ]
    jar_globs = [
        os.path.join(_APP_DIR, "Audiveris*", "lib", "Audiveris.jar"),
        os.path.join(_APP_DIR, "audiveris*", "lib", "*.jar"),
    ]

    if _IS_WINDOWS:
        exe_paths += [
            r"C:\Program Files\Audiveris\Audiveris.exe",
            r"C:\Program Files (x86)\Audiveris\Audiveris.exe",
            r"C:\Program Files\Audiveris\bin\Audiveris.bat",
            r"C:\Program Files (x86)\Audiveris\bin\Audiveris.bat",
            r"C:\Audiveris\bin\Audiveris.bat",
            os.path.join(_HOME, "Audiveris", "bin", "Audiveris.bat"),
            os.path.join(_HOME, "Downloads", "Audiveris", "bin", "Audiveris.bat"),
        ]
        exe_globs += [
            r"C:\Program Files\Audiveris*\Audiveris.exe",
            r"C:\Program Files (x86)\Audiveris*\Audiveris.exe",
            r"C:\Program Files\Audiveris*\bin\Audiveris.bat",
            r"C:\Audiveris*\bin\Audiveris.bat",
            os.path.join(_HOME, "Downloads", "Audiveris*", "bin", "Audiveris.bat"),
            os.path.join(_HOME, "Audiveris*", "bin", "Audiveris.bat"),
        ]
        jar_paths += [
            r"C:\Program Files\Audiveris\app\audiveris.jar",
            r"C:\Program Files (x86)\Audiveris\app\audiveris.jar",
            r"C:\Audiveris\lib\Audiveris.jar",
            r"C:\Program Files\Audiveris\lib\Audiveris.jar",
            os.path.join(_HOME, "Audiveris", "lib", "Audiveris.jar"),
            os.path.join(_HOME, "Downloads", "Audiveris", "lib", "Audiveris.jar"),
        ]
        jar_globs += [
            r"C:\Audiveris*\lib\Audiveris.jar",
            os.path.join(_HOME, "Downloads", "Audiveris*", "lib", "Audiveris.jar"),
            os.path.join(_HOME, "Audiveris*", "lib", "Audiveris.jar"),
        ]
    else:
        # Linux / macOS
        exe_paths += [
            "/opt/audiveris/bin/Audiveris",
            "/usr/local/bin/audiveris",
        ]
        jar_paths += [
            "/opt/audiveris/lib/audiveris.jar",
            "/usr/share/audiveris/lib/audiveris.jar",
            os.path.join(_HOME, "audiveris", "lib", "audiveris.jar"),
            os.path.join(_HOME, ".local", "share", "audiveris", "lib", "audiveris.jar"),
        ]
        jar_globs += [
            "/opt/audiveris*/lib/*.jar",
            os.path.join(_HOME, "audiveris*", "lib", "*.jar"),
        ]

    return exe_paths, exe_globs, jar_paths, jar_globs


def find_audiveris():
    """Return (mode, path) where mode is 'exe'/'bat'/'jar', or (None, None)."""
    # Explicit env vars override everything
    env_bat = os.environ.get("AUDIVERIS_BAT", "").strip()
    if env_bat and os.path.isfile(env_bat):
        return ("bat", env_bat)
    env_jar = os.environ.get("AUDIVERIS_JAR", "").strip()
    if env_jar and os.path.isfile(env_jar):
        return ("jar", env_jar)

    # Check PATH (works on all platforms)
    on_path = shutil.which("audiveris") or shutil.which("Audiveris")
    if on_path:
        return ("exe", on_path)

    exe_paths, exe_globs, jar_paths, jar_globs = _get_audiveris_paths()

    # Prefer exe/bat launcher (binary distribution)
    for p in exe_paths:
        if os.path.isfile(p):
            mode = "bat" if p.endswith(".bat") else "exe"
            return (mode, p)
    found = _first_glob(exe_globs)
    if found:
        mode = "bat" if found.endswith(".bat") else "exe"
        return (mode, found)

    # Fallback: fat JAR
    for p in jar_paths:
        if os.path.isfile(p):
            return ("jar", p)
    found = _first_glob(jar_globs)
    if found:
        return ("jar", found)

    return (None, None)


def java_available():
    return shutil.which("java") is not None


if _IS_WINDOWS:
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
else:
    _SETUP_MSG = (
        "Audiveris OMR engine not found.\n\n"
        "Setup (one-time):\n"
        "  1. Go to  https://github.com/Audiveris/audiveris/releases\n"
        "  2. Download the JAR file — look for 'Audiveris-5.x.x.jar'\n"
        "  3. Place it in this project folder as  audiveris.jar\n"
        "     Or set:  export AUDIVERIS_JAR=/path/to/audiveris.jar\n"
        "  4. Make sure Java 11+ is installed:  java -version\n"
        "  5. Restart this server — it will find Audiveris automatically.\n\n"
        "Or use Docker:  docker compose up --build"
    )


# ── Job store ─────────────────────────────────────────────────────────────────
# job_id -> {progress, status, status_text, error, result}
_jobs = {}
_jobs_lock = threading.Lock()

# ── PDF hash cache ────────────────────────────────────────────────────────────
# sha256 -> path to already-converted .mxl/.xml file
_pdf_cache = {}
_pdf_cache_lock = threading.Lock()


def _hash_file(path, chunk_size=65536):
    """Fast SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


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
        score = converter.parse(parse_path, forceSource=True, quantizePost=False)

        prog(85, "Writing score\u2026")
        xml_path = os.path.join(export_dir, "score.xml")
        # If source is already MusicXML, just copy it instead of re-serializing
        if ext != "pdf" and parse_path.lower().endswith((".xml", ".mxl", ".musicxml")):
            shutil.copy2(parse_path, xml_path)
        else:
            score.write("musicxml", fp=xml_path)

        prog(94, "Generating MIDI\u2026")
        midi_path = os.path.join(export_dir, f"{job_id}.mid")
        score.write("midi", fp=midi_path)

        # Save project to database
        with _jobs_lock:
            job_meta = _jobs.get(job_id, {})
            user_id = job_meta.get("user_id")
            title = job_meta.get("title", "Untitled")
        if user_id:
            create_project(user_id, title, "solo", job_id)

        _set_job(job_id,
                 progress=100,
                 status="done",
                 status_text="Done!",
                 result={"midi_url": f"/midi/{job_id}", "mxl_url": f"/mxl/{job_id}"})

    except RuntimeError as exc:
        _set_job(job_id, status="error", error=str(exc))
    except Exception as exc:
        _set_job(job_id, status="error", error=str(exc))


def _process_single_part(part, export_dir, sub_id):
    """Process one part (PDF or MusicXML) and return its result dict.
    Runs independently so multiple parts can be processed in parallel.
    """
    parse_path = part["upload_path"]

    if part["ext"] == "pdf":
        omr_dir = os.path.join(export_dir, "omr_out")
        os.makedirs(omr_dir, exist_ok=True)
        parse_path = convert_pdf_to_mxl(part["upload_path"], omr_dir)

    score = converter.parse(parse_path)

    xml_path = os.path.join(export_dir, "score.xml")
    score.write("musicxml", fp=xml_path)

    midi_path = os.path.join(export_dir, f"{sub_id}.mid")
    score.write("midi", fp=midi_path)

    return {
        "part_name": part["name"],
        "mxl_url":  f"/mxl/{sub_id}",
        "midi_url": f"/midi/{sub_id}",
    }


def _run_setup_job(job_id, piece_name, setup_type, parts_info, output_folder):
    """Process parts in parallel for the setup flow.

    parts_info: list of {name: str, upload_path: str, ext: str}
    """
    total = len(parts_info)

    # Prepare export dirs for each part
    part_tasks = []
    for part in parts_info:
        sub_id     = uuid.uuid4().hex[:12]
        export_dir = os.path.join(output_folder, sub_id)
        os.makedirs(export_dir, exist_ok=True)
        part_tasks.append((part, export_dir, sub_id))

    results = [None] * total
    _set_job(job_id, progress=5, status_text=f"Processing {total} part(s)\u2026")

    # Process parts in parallel (up to 4 at a time)
    max_workers = min(total, 4)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {}
        for idx, (part, export_dir, sub_id) in enumerate(part_tasks):
            fut = pool.submit(_process_single_part, part, export_dir, sub_id)
            future_to_idx[fut] = idx

        done_count = 0
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                results[idx] = fut.result()
                done_count += 1
                pct = int(done_count * 95 / total)
                _set_job(job_id, progress=pct,
                         status_text=f"Finished part {done_count}/{total}\u2026")
            except (RuntimeError, Exception) as exc:
                _set_job(job_id, status="error",
                         error=f"Part \u2018{parts_info[idx]['name']}\u2019: {exc}")
                return

    # Save project to database
    with _jobs_lock:
        user_id = _jobs.get(job_id, {}).get("user_id")
    if user_id:
        create_project(user_id, piece_name, setup_type, job_id, json.dumps(results))

    _set_job(job_id,
             progress=100,
             status="done",
             status_text="Done!",
             result={"piece_name": piece_name, "type": setup_type, "parts": results})


def convert_pdf_to_mxl(pdf_path, output_dir, progress_cb=None):
    """Run Audiveris OMR on pdf_path; return path of the produced .mxl file.
    Optionally calls progress_cb(pct) with values 12–68 as output lines come in.
    Uses SHA-256 cache to skip re-processing identical PDFs.
    """
    # ── Check cache first ─────────────────────────────────────────────────
    pdf_hash = _hash_file(pdf_path)
    with _pdf_cache_lock:
        cached = _pdf_cache.get(pdf_hash)
    if cached and os.path.isfile(cached):
        if progress_cb:
            progress_cb(68)
        # Copy cached result into this job's output dir
        dest = os.path.join(output_dir, os.path.basename(cached))
        shutil.copy2(cached, dest)
        return dest

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
            "java", "-Xmx2g", "-jar", runner,
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
        # Cache the result for future uploads of the same PDF
        with _pdf_cache_lock:
            _pdf_cache[pdf_hash] = found_music
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

    if return_code != 0:
        tail = combined_output[-800:].strip()
        raise RuntimeError(
            f"Audiveris could not process this PDF (exit code {return_code}).\n\n"
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
def home():
    return render_template("home.html")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/blog")
def blog():
    return render_template("blog.html")


@app.route("/blog/<slug>")
def blog_post(slug):
    return render_template("blog_post.html", slug=slug)


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/studio")
def studio():
    user = get_current_user_from_cookie()
    if user:
        return redirect("/dashboard")
    return render_template("studio.html")


@app.route("/dashboard")
@login_required
def dashboard():
    projects = get_user_projects_enriched(g.user["id"])
    recent_plays = get_recent_plays(g.user["id"], limit=10)
    return render_template("dashboard.html", user=g.user, projects=projects, recent_plays=recent_plays)


@app.route("/api/play/<int:project_id>", methods=["POST"])
@login_required
def api_play(project_id):
    proj = get_project(project_id, g.user["id"])
    if not proj:
        return jsonify({"error": "Not found"}), 404
    record_play(g.user["id"], project_id)
    return jsonify({"ok": True})


@app.route("/api/favorite/<int:project_id>", methods=["POST"])
@login_required
def api_favorite(project_id):
    result = toggle_favorite(project_id, g.user["id"])
    if result is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True, "is_favorite": result})


@app.route("/api/rename/<int:project_id>", methods=["POST"])
@login_required
def api_rename(project_id):
    data = request.get_json(silent=True) or {}
    new_title = (data.get("title") or "").strip()
    if not new_title:
        return jsonify({"error": "Title required"}), 400
    if not rename_project(project_id, g.user["id"], new_title):
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/project/<int:project_id>", methods=["DELETE"])
@login_required
def api_delete(project_id):
    if not delete_project(project_id, g.user["id"]):
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@app.route("/app")
@login_required
def index():
    project_id = request.args.get("project")
    project_data = None
    if project_id:
        row = get_project(project_id, g.user["id"])
        if row:
            project_data = dict(row)
    return render_template("index.html", project=project_data)


@app.route("/setup")
@login_required
def setup():
    return render_template("setup.html", user=g.user)


@app.route("/setup/upload", methods=["POST"])
@login_required
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
            "user_id": g.user["id"],
            "title": piece_name,
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
@login_required
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
            "user_id": g.user["id"],
            "title": file.filename.rsplit(".", 1)[0],
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
    init_db()
    app.run(host="0.0.0.0", debug=os.environ.get("FLASK_DEBUG", "1") == "1", port=5000)
