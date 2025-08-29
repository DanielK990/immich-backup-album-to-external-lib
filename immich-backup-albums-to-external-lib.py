from flask import Flask, request, render_template, jsonify
import os
import requests
import shutil
import json
import sys
from uuid import uuid4
from threading import Thread, Lock

app = Flask(__name__)

# In-memory job tracking
progress_lock = Lock()
copy_progress = {}  # job_id -> {done, total, errors: [], complete: bool}


@app.template_filter("nl2br")
def nl2br_filter(s):
    return (s or "").replace("\n", "<br>")


def copy_assets_job(
    job_id,
    assets,
    album_name,
    album_id,
    start_date,
    delete_assets,
    delete_album,
    copy_path,
    create_subdir_for_year,
):
    # Initialize progress
    with progress_lock:
        copy_progress[job_id] = {
            "done": 0,
            "total": len(assets),
            "errors": [],
            "complete": False,
        }

    success = True
    album_path = ""

    # Ensure album directory exists
    if create_subdir_for_year:
        year = start_date.split("-")[0] if start_date else "Unknown"
        album_path = os.path.join(copy_path, year, album_name)
    else:
        album_path = os.path.join(copy_path, album_name)

    try:
        os.makedirs(album_path, exist_ok=True)
    except Exception as e:
        with progress_lock:
            copy_progress[job_id]["errors"].append(
                f"Failed to create directories {album_path}: {e}"
            )
            success = False

    if success:
        # Copy assets one by one and update progress
        for asset in assets:
            file_name = asset.get("originalFileName")
            dest_path = os.path.join(album_path, file_name)
            source_path = asset.get("originalPath")
            try:
                with progress_lock:
                    copy_progress[job_id]["current"] = dest_path

                print("Copy from " + source_path + " to " + dest_path)
                shutil.copy2(source_path, dest_path)
            except Exception as e:
                with progress_lock:
                    copy_progress[job_id]["errors"].append(
                        f"Failed to copy {file_name}: {e}"
                    )
                    success = False
            finally:
                with progress_lock:
                    copy_progress[job_id]["done"] += 1

    if success and delete_assets:
        try:
            headers = {"x-api-key": API_KEY, "Content-Type": "application/json"}
            assets_url = IMMICH_SERVER + ":" + IMMICH_PORT + "/api/assets"
            body = json.dumps({"ids": [asset.get("id") for asset in assets]})
            response = requests.delete(assets_url, headers=headers, data=body)
            response.raise_for_status()
        except Exception as e:
            copy_progress[job_id]["errors"].append(f"Error deleting assets: {str(e)}")
            success = False

    if success and delete_album:
        try:
            headers = {"x-api-key": API_KEY}
            albums_url = IMMICH_SERVER + ":" + IMMICH_PORT + "/api/albums/" + album_id
            response = requests.delete(albums_url, headers=headers)
            response.raise_for_status()
        except Exception as e:
            copy_progress[job_id]["errors"].append(f"Error deleting album: {str(e)}")
            success = False

    with progress_lock:
        copy_progress[job_id]["complete"] = True


@app.route("/", methods=["GET"])
def index():
    error = None
    albums = []
    selected_path = EXTERNAL_LIB_PATHS.split(",")[0]
    try:
        headers = {"x-api-key": API_KEY}
        albums_url = IMMICH_SERVER + ":" + IMMICH_PORT + "/api/albums"
        response = requests.get(albums_url, headers=headers)
        response.raise_for_status()
        album_data = response.json()
        albums = [(album.get("albumName"), album.get("id")) for album in album_data]
        albums = sorted(albums, key=lambda album: album[0], reverse=True)
    except Exception as e:
        error = f"Error fetching albums: {str(e)}"

    return render_template(
        "immich-backup-albums-to-external-lib.html",
        albums=albums,
        paths=EXTERNAL_LIB_PATHS.split(","),
        selected_path=selected_path,
        create_subdir_for_year=True,
        error=error,
    )


@app.route("/submit", methods=["POST"])
def submit():
    error = None

    album_id = request.form.get("album_id")
    copy_path = request.form.get("path")
    delete_assets = request.form.get("delete_assets") == "True"
    delete_album = request.form.get("delete_album") == "True"
    create_subdir_for_year = request.form.get("create_subdir_for_year") == "on"

    # Validate album and get assets
    try:
        headers = {"x-api-key": API_KEY}
        albums_url = IMMICH_SERVER + ":" + IMMICH_PORT + "/api/albums/" + album_id
        validate_response = requests.get(albums_url, headers=headers)
        validate_result = validate_response.json()
        if not validate_result:
            error = f"Album with id '{album_id}' does not exist."
            return render_template(
                "immich-backup-albums-to-external-lib.html", error=error
            )
        album = validate_result
        album_name = album.get("albumName")
        assets = album.get("assets", [])
        start_date = album.get("startDate")

        for asset in assets:
            for path in EXTERNAL_LIB_PATHS.split(","):
                if asset.get("originalPath").startswith(path):
                    error = f"Album '{album_name}' contains images which are already in the external library."
                    return render_template(
                        "immich-backup-albums-to-external-lib.html", error=error
                    )

        # Start background job
        job_id = str(uuid4())
        thread = Thread(
            target=copy_assets_job,
            args=(
                job_id,
                assets,
                album_name,
                album_id,
                start_date,
                delete_assets,
                delete_album,
                copy_path,
                create_subdir_for_year,
            ),
            daemon=True,
        )
        thread.start()

    except Exception as e:
        error = f"Error starting copy job: {str(e)}"
        return render_template(
            "immich-backup-albums-to-external-lib.html",
            error=error,
        )

    # Render progress screen (job_id triggers the progress UI)
    return render_template("immich-backup-albums-to-external-lib.html", job_id=job_id)


@app.route("/progress/<job_id>", methods=["GET"])
def progress(job_id):
    with progress_lock:
        data = copy_progress.get(job_id)
    if not data:
        return jsonify({"errors": ["unknown job"]}), 404

    percent = 0
    if data.get("total", 0):
        percent = int((data.get("done", 0) * 100) / max(1, data.get("total", 0)))

    payload = {
        "current": data.get("current", ""),
        "percent": percent,
        "errors": data.get("errors", []),
        "complete": data.get("complete", False),
    }
    return jsonify(payload)


if __name__ == "__main__":

    API_KEY = os.getenv("API_KEY", "")
    EXTERNAL_LIB_PATHS = os.getenv("EXTERNAL_LIB_PATHS", "")
    IMMICH_SERVER = os.getenv("IMMICH_SERVER", "")
    IMMICH_PORT = os.getenv("IMMICH_PORT", "")
    WEBUI_IP = os.getenv("WEBUI_IP", "")
    WEBUI_PORT = os.getenv("WEBUI_PORT", "")

    error = False

    if not API_KEY:
        print(
            "API key is missing. Please set the API_KEY environment variable.",
            file=sys.stderr,
        )
        error = True

    if not EXTERNAL_LIB_PATHS:
        print(
            "External library paths are missing. Please set the EXTERNAL_LIB_PATHS environment variable.",
            file=sys.stderr,
        )
        error = True

    if not IMMICH_SERVER:
        print(
            "immich server is missing. Please set the IMMICH_SERVER environment variable.",
            file=sys.stderr,
        )
        error = True

    if not IMMICH_PORT:
        print(
            "immich port is missing. Please set the IMMICH_PORT environment variable.",
            file=sys.stderr,
        )
        error = True

    if not WEBUI_IP:
        print(
            "webui ip is missing. Please set the WEBUI_IP environment variable.",
            file=sys.stderr,
        )
        error = True

    if not WEBUI_PORT:
        print(
            "webui port is missing. Please set the WEBUI_PORT environment variable.",
            file=sys.stderr,
        )
        error = True

    if error:
        exit(1)

    app.run(host=WEBUI_IP, port=WEBUI_PORT)
