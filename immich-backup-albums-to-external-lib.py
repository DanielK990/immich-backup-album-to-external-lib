import logging
from flask import Flask, request, render_template, jsonify
import os
import requests
import shutil
import json
import sys
from uuid import uuid4
from threading import Thread, Lock


# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = Flask(__name__)

# In-memory job tracking
progress_lock = Lock()
copy_progress = {}  # job_id -> {done, total, errors: [], complete: bool}


@app.template_filter("nl2br")
def nl2br_filter(s):
    return (s or "").replace("\n", "<br>")


def get_first_api_key():
    api_keys = API_KEYS.split(",")
    return api_keys[0].split(":", 1)[1] if api_keys else None


def get_api_key_by_user_id(user_id):
    for key in API_KEYS.split(","):
        if key.startswith(f"{user_id}:"):
            return key.split(":", 1)[1]
    return None


def copy_assets_job(
    job_id,
    assets,
    album_name,
    album_id,
    album_owner_id,
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

    logging.info("Start copying assets to external library")

    # Ensure album directory exists
    if create_subdir_for_year:
        year = start_date.split("-")[0] if start_date else "Unknown"
        album_path = os.path.join(copy_path, year, album_name)
    else:
        album_path = os.path.join(copy_path, album_name)

    try:
        logging.info(f"Create directory {album_path}")
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

                logging.info(f"Copy from {source_path} to {dest_path}")
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

            asset_ids_by_owner_id = {}
            for asset in assets:
                if asset["ownerId"] not in asset_ids_by_owner_id:
                    asset_ids_by_owner_id[asset["ownerId"]] = set()
                asset_ids_by_owner_id[asset["ownerId"]].add(asset["id"])

            for owner_id in asset_ids_by_owner_id.keys():
                headers = {
                    "x-api-key": get_api_key_by_user_id(owner_id),
                    "Content-Type": "application/json",
                }
                assets_url = IMMICH_SERVER + ":" + IMMICH_PORT + "/api/assets"
                body = json.dumps({"ids": list(asset_ids_by_owner_id[owner_id])})
                response = requests.delete(assets_url, headers=headers, data=body)
                response.raise_for_status()
        except Exception as e:
            copy_progress[job_id]["errors"].append(f"Error deleting assets: {str(e)}")
            success = False

    if success and delete_album:
        try:
            headers = {"x-api-key": get_api_key_by_user_id(album_owner_id)}
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
        headers = {"x-api-key": get_first_api_key()}
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
    delete_assets = request.form.get("delete_assets") == "on"
    delete_album = request.form.get("delete_album") == "on"
    create_subdir_for_year = request.form.get("create_subdir_for_year") == "on"

    logging.info("Validate input")
    # Validate album and get assets
    try:
        headers = {"x-api-key": get_first_api_key()}
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
        album_owner_id = album.get("ownerId")
        assets = album.get("assets", [])
        start_date = album.get("startDate")

        for asset in assets:
            for path in EXTERNAL_LIB_PATHS.split(","):
                if asset.get("originalPath").startswith(path):
                    error = f"Album '{album_name}' contains images which are already in the external library."
                    return render_template(
                        "immich-backup-albums-to-external-lib.html", error=error
                    )

        logging.info("Start background job")
        # Start background job
        job_id = str(uuid4())
        thread = Thread(
            target=copy_assets_job,
            args=(
                job_id,
                assets,
                album_name,
                album_id,
                album_owner_id,
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

    API_KEYS = os.getenv("API_KEYS", "")
    EXTERNAL_LIB_PATHS = os.getenv("EXTERNAL_LIB_PATHS", "")
    IMMICH_SERVER = os.getenv("IMMICH_SERVER", "")
    IMMICH_PORT = os.getenv("IMMICH_PORT", "")
    WEBUI_IP = os.getenv("WEBUI_IP", "")
    WEBUI_PORT = os.getenv("WEBUI_PORT", "")

    error = False

    if not API_KEYS:
        logging.error(
            "API keys are missing. Please set the API_KEYS environment variable."
        )
        error = True

    if not EXTERNAL_LIB_PATHS:
        logging.error(
            "External library paths are missing. Please set the EXTERNAL_LIB_PATHS environment variable."
        )
        error = True

    if not IMMICH_SERVER:
        logging.error(
            "immich server is missing. Please set the IMMICH_SERVER environment variable."
        )
        error = True

    if not IMMICH_PORT:
        logging.error(
            "immich port is missing. Please set the IMMICH_PORT environment variable."
        )
        error = True

    if not WEBUI_IP:
        logging.error(
            "webui ip is missing. Please set the WEBUI_IP environment variable."
        )
        error = True

    if not WEBUI_PORT:
        logging.error(
            "webui port is missing. Please set the WEBUI_PORT environment variable."
        )
        error = True

    if error:
        exit(1)

    app.run(host=WEBUI_IP, port=WEBUI_PORT)
