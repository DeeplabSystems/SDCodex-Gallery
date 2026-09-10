"""SDCodex Gallery — disk-backed browsing + metadata reader (caption / SD prompt / ComfyUI workflow).

This is the Flask backend for the FolderFrame-based gallery. It serves the
static gallery UI and acts as a JSON-drop-in for the directory listing that
FolderFrame normally gets from a static web server's autoindex:

  * directory listings are returned as plain HTML (an <a href> list) so the
    existing gallery JavaScript parses them with no changes;
  * image files are streamed back so the gallery can display them directly
    from disk;
  * caption / SD prompt / ComfyUI workflow are read straight from the .txt
    sidecar and from the image's own metadata — no database, no import step.

Reading the caption, SD prompt and workflow mirrors the behaviour of the
legacy ComfyCaption gallery so existing media stays fully compatible.
"""

import json
import mimetypes
import os
from html import escape
from urllib.parse import unquote

from flask import Blueprint, Response, jsonify, render_template, request, send_file, send_from_directory
from PIL import Image

try:
    from sd_prompt_reader.image_data_reader import ImageDataReader
except Exception:  # sd-prompt-reader is optional; SD prompt just reads as absent
    ImageDataReader = None

try:
    import piexif
    import piexif.helper
except Exception:  # piexif optional (EXIF workflow extraction just degrades)
    piexif = None
    piexif.helper = None

gallery = Blueprint("gallery2", __name__)

# Point the blueprint at this plugin's templates/ directory so the gallery can
# extend the core ``base.html`` (keeping the SD Codex header) like the other
# plugins. Sibling modules may also render it via the shared Jinja loader.
gallery.template_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

# --------------------------------------------------------------------------- #
#  Path / root resolution
# --------------------------------------------------------------------------- #
def _plugin_dir():
    return os.path.dirname(os.path.abspath(__file__))


def resolve_browse_root():
    """Pick the first existing directory from GALLERY_ROOT then common fallbacks."""
    candidates = [
        os.environ.get("GALLERY_ROOT"),
        os.environ.get("GALLERY"),
        "/data/downloads",
        "/data",
        os.path.join(_plugin_dir(), "downloads"),
    ]
    for cand in candidates:
        if cand and os.path.isdir(cand):
            return os.path.abspath(cand)
    return os.path.abspath(_plugin_dir())


def is_safe_path(base, target):
    """Basic path-traversal protection (same rule as the legacy gallery)."""
    resolved_base = os.path.abspath(base)
    resolved_target = os.path.abspath(target)
    return resolved_target.startswith(resolved_base + os.sep) or resolved_target == resolved_base


def resolve_media(folder, file_name):
    """Return an absolute path inside the browse root, or None if unsafe/missing."""
    root = resolve_browse_root()
    folder_clean = (folder or "").strip("/\\") if folder else ""
    parts = [_p for _p in (folder_clean + "/").split("/") if _p]
    if any(part in (".", "..") for part in parts):
        return None
    if not file_name or "/" in file_name or "\\" in file_name or file_name in (".", ".."):
        return None
    target = os.path.join(root, *parts, file_name)
    if not is_safe_path(root, target) or not os.path.isfile(target):
        return None
    return target


# --------------------------------------------------------------------------- #
#  Metadata readers (ported / simplified from the legacy ComfyCaption gallery)
# --------------------------------------------------------------------------- #
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif",
    ".avif", ".heic", ".heif", ".jfif",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".m4a", ".mpg", ".mpeg", ".avi"}


def extract_comfy_workflow(image_path):
    """Robustly extract the ComfyUI workflow (or prompt) from PNG/WebP/JPEG metadata."""
    def extract_json_if_valid(val):
        if not isinstance(val, str):
            val = str(val)
        val = val.strip()
        if val.startswith("{") or val.startswith("["):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, (dict, list)):
                    return val
            except Exception:
                pass
        return None

    try:
        with Image.open(image_path) as img:
            img.load()
            # PNG text chunks
            if "workflow" in img.info:
                val = extract_json_if_valid(img.info["workflow"])
                if val:
                    return val
            if "prompt" in img.info:
                val = extract_json_if_valid(img.info["prompt"])
                if val:
                    return val
            # Exif UserComment for WEBP / JPEG
            if "exif" in img.info:
                try:
                    exif_dict = piexif.load(img.info["exif"])
                    if "Exif" in exif_dict and piexif.ExifIFD.UserComment in exif_dict["Exif"]:
                        user_comment = exif_dict["Exif"][piexif.ExifIFD.UserComment]
                        try:
                            comment_str = piexif.helper.UserComment.load(user_comment)
                        except Exception:
                            if isinstance(user_comment, bytes):
                                comment_str = user_comment.decode("utf-8", errors="ignore")
                                if comment_str.startswith("ASCII\0\0\0"):
                                    comment_str = comment_str[8:]
                                elif comment_str.startswith("UNICODE\0"):
                                    comment_str = comment_str[8:]
                            else:
                                comment_str = str(user_comment)
                        val = extract_json_if_valid(comment_str)
                        if val:
                            return val
                except Exception:
                    pass
    except Exception:
        pass
    return None


def read_sidecar_caption(image_path):
    """Read the .txt sidecar caption beside an image, if present."""
    base, _ = os.path.splitext(image_path)
    txt_path = base + ".txt"
    if os.path.isfile(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8", errors="ignore") as fh:
                return fh.read()
        except Exception:
            pass
    return ""


def read_sd_prompt(image_path):
    """Read the SD positive/negative/settings using sd-prompt-reader."""
    if not ImageDataReader:
        return {"sdPrompt": "", "sdNegative": "", "sdSetting": ""}
    try:
        reader = ImageDataReader(image_path)
        return {
            "sdPrompt": str(reader.positive).strip() if getattr(reader, "positive", None) else "",
            "sdNegative": str(reader.negative).strip() if getattr(reader, "negative", None) else "",
            "sdSetting": str(reader.setting).strip() if getattr(reader, "setting", None) else "",
        }
    except Exception:
        return {"sdPrompt": "", "sdNegative": "", "sdSetting": ""}


def read_embedded_caption(image_path):
    """Fallback caption from PNG text chunks / EXIF Description."""
    try:
        with Image.open(image_path) as img:
            for key in ("Description", "parameters", "Comment", "title"):
                val = img.info.get(key)
                if val:
                    val = str(val).strip()
                    if val and not val.startswith("{"):
                        return val
            if "exif" in img.info:
                try:
                    exif = piexif.load(img.info["exif"])
                    if "0th" in exif and piexif.ImageIFD.ImageDescription in exif["0th"]:
                        val = exif["0th"][piexif.ImageIFD.ImageDescription]
                        if isinstance(val, bytes):
                            val = val.decode("utf-8", errors="ignore")
                        val = str(val).strip()
                        if val:
                            return val
                except Exception:
                    pass
    except Exception:
        pass
    return ""


def collect_image_metadata(image_path):
    """Return everything the viewer panel needs for one image."""
    if not image_path or not os.path.isfile(image_path):
        return {"ok": False, "error": "File not found"}
    caption = read_sidecar_caption(image_path)
    sd = read_sd_prompt(image_path)
    workflow = extract_comfy_workflow(image_path)
    return {
        "ok": True,
        "fileName": os.path.basename(image_path),
        "caption": caption,
        "hasCaption": bool(caption),
        "sdPrompt": sd["sdPrompt"],
        "sdNegative": sd["sdNegative"],
        "sdSetting": sd["sdSetting"],
        "hasWorkflow": workflow is not None,
    }


# --------------------------------------------------------------------------- #
#  Directory-listing HTML (FolderFrame-compatible)
# --------------------------------------------------------------------------- #
def _listing_html(root, rel_dir):
    """Return an HTML autoindex that the gallery's <a href> parser understands."""
    names = sorted(os.listdir(rel_dir))
    rows = []
    for name in names:
        full = os.path.join(rel_dir, name)
        if name.startswith(".") or name in ("folderframe.ignore", ".frameignore"):
            continue
        href_name = name
        try:
            href_name = escape(name)
        except Exception:
            href_name = name
        if os.path.isdir(full):
            rows.append(f'<a href="{href_name}/">{href_name}/</a><br>\n')
        else:
            rows.append(f'<a href="{href_name}">{href_name}</a><br>\n')
    return "<!DOCTYPE html><html><head><title>Index of %s</title></head><body>" \
           "<h1>Index of %s</h1><hr>" % (escape(os.path.relpath(rel_dir, root) or "/"),
                                         escape(os.path.relpath(rel_dir, root) or "/")) + \
           "".join(rows) + "<hr></body></html>"


# --------------------------------------------------------------------------- #
#  Routes
# --------------------------------------------------------------------------- #
@gallery.route("/")
def index():
    # Rendered through the core layout so the SD Codex header/navbar is kept,
    # matching how the other plugin pages look inside the app.
    return render_template("gallery.html")


@gallery.route("/folderframe.config.json")
def config():
    return jsonify({
        "sources": [
            {
                "id": "media",
                "label": "Media",
                "path": "browse/",
                "discoveryMode": "directory",
            }
        ],
        "defaults": {
            "source": "media",
            "album": "",
            "view": "folders",
            "sort": "filename",
            "interval": 5,
            "imageMode": "fit",
            "shuffle": False,
            "autoRefresh": True,
            "tvMode": False,
            "autoplay": False,
            "controls": True,
            "showFilenames": True,
            "showDownloadButton": True,
            "showCopyButton": True,
        },
        "index": {"refreshInterval": 120},
        "embed": {"refreshInterval": 300, "rememberPreferences": False},
    })


@gallery.route("/browse/")
@gallery.route("/browse/<path:relpath>")
def browse(relpath=""):
    root = resolve_browse_root()
    relpath = unquote(relpath)
    target = os.path.join(root, relpath) if relpath else root
    target = os.path.abspath(target)
    if not is_safe_path(root, target):
        return "Forbidden", 403
    if os.path.isdir(target):
        # Enforce a trailing slash so relative hrefs resolve / the parser works.
        if not relpath.endswith("/") and relpath:
            return Response("", status=308, headers={"Location": os.path.join(
                request.script_root, "browse", relpath) + "/"})
        return Response(_listing_html(root, target), mimetype="text/html")
    if os.path.isfile(target):
        guessed, _ = mimetypes.guess_type(target)
        resp = send_file(target, mimetype=guessed or "application/octet-stream")
        resp.headers["Content-Disposition"] = "inline"
        return resp
    return "Not found", 404


@gallery.route("/api/roots")
def list_roots():
    root = resolve_browse_root()
    return jsonify({"root": root, "exists": os.path.isdir(root)})


@gallery.route("/api/meta")
def image_meta():
    folder = request.args.get("folder", "")
    file_name = request.args.get("file", "")
    image_path = resolve_media(folder, file_name)
    if not image_path:
        return jsonify({"ok": False, "error": "Invalid or missing file"}), 404
    return jsonify(collect_image_metadata(image_path))


@gallery.route("/api/caption")
def caption():
    folder = request.args.get("folder", "")
    file_name = request.args.get("file", "")
    image_path = resolve_media(folder, file_name)
    if not image_path:
        return jsonify({"error": "Invalid or missing file"}), 404
    caption = read_sidecar_caption(image_path)
    return jsonify({"file": file_name, "caption": caption, "hasCaption": bool(caption)})


@gallery.route("/api/prompt")
def prompt():
    folder = request.args.get("folder", "")
    file_name = request.args.get("file", "")
    image_path = resolve_media(folder, file_name)
    if not image_path:
        return jsonify({"error": "Invalid or missing file"}), 404
    sd = read_sd_prompt(image_path)
    return jsonify({"file": file_name, **sd})


@gallery.route("/api/workflow")
def workflow():
    folder = request.args.get("folder", "")
    file_name = request.args.get("file", "")
    image_path = resolve_media(folder, file_name)
    if not image_path:
        return jsonify({"error": "Invalid or missing file"}), 400
    workflow_data = extract_comfy_workflow(image_path)
    if not workflow_data:
        return jsonify({"error": "No ComfyUI workflow found in image metadata"}), 404
    try:
        formatted_data = json.dumps(json.loads(workflow_data), indent=2)
    except json.JSONDecodeError:
        formatted_data = workflow_data
    return Response(
        formatted_data,
        mimetype="application/json",
        headers={
            "Content-Disposition": f"attachment; filename={os.path.splitext(os.path.basename(image_path))[0]}_workflow.json"
        },
    )


# --------------------------------------------------------------------------- #
#  Static gallery assets
# --------------------------------------------------------------------------- #
_ASSETS = {
    "app.js": ("app.js", "application/javascript"),
    "settings.js": ("settings.js", "application/javascript"),
    "resilience.js": ("resilience.js", "application/javascript"),
    "heic2any.min.js": ("heic2any.min.js", "application/javascript"),
    "generate_thumbnails.py": ("generate_thumbnails.py", "text/x-python"),
    "embed.html": ("embed.html", "text/html"),
    "styles.css": ("styles.css", "text/css"),
}


@gallery.route("/<path:filename>")
def static_asset(filename):
    entry = _ASSETS.get(filename)
    if not entry:
        # Let favicon-style / docs paths 404 gracefully rather than error.
        return "Not found", 404
    fname, ctype = entry
    return send_from_directory(_plugin_dir(), fname, mimetype=ctype, max_age=3600)