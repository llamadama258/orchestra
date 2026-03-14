import os
import uuid
import shutil

from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename
from music21 import converter

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["OUTPUT_FOLDER"] = os.path.join(os.path.dirname(__file__), "outputs")
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB limit

ALLOWED_EXTENSIONS = {"mxl", "xml", "musicxml"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    file = request.files["file"]
    if file.filename == "" or file.filename is None:
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Only MusicXML files are accepted (.mxl, .xml, .musicxml)"}), 400

    # Save uploaded MusicXML with a unique name
    job_id = uuid.uuid4().hex[:12]
    safe_name = secure_filename(file.filename)
    upload_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{job_id}_{safe_name}")
    file.save(upload_path)

    try:
        # MusicXML → MIDI via music21
        export_dir = os.path.join(app.config["OUTPUT_FOLDER"], job_id)
        os.makedirs(export_dir, exist_ok=True)

        score = converter.parse(upload_path)

        # Write uncompressed MusicXML for score display (OSMD handles plain XML best)
        xml_path = os.path.join(export_dir, "score.xml")
        score.write("musicxml", fp=xml_path)

        midi_path = os.path.join(export_dir, f"{job_id}.mid")
        score.write("midi", fp=midi_path)

        return jsonify({"midi_url": f"/midi/{job_id}", "mxl_url": f"/mxl/{job_id}"})

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
    # Sanitize job_id to prevent path traversal
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
