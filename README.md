# SDCodex-Gallery

An installable SDCodex plugin that provides a **disk-backed media gallery**:
it scans folders directly from disk, reads image **captions**, **SD prompts**
and **ComfyUI workflows** from the images' own metadata, and lets you download
any ComfyUI workflow as JSON. It replaces the older gallery bundled with
SDCodex-ComfyCaption and needs **no database and no import step** — it reads
live from the filesystem every time you open a folder.

## Features

- **Directory-backed browsing** — folders browsed as albums, exactly like the
  FolderFrame gallery it is built on. Thumbnails and full-size images are
  streamed straight from disk.
- **Caption reading** — reads the `.txt` sidecar shipped beside each image and
  falls back to the image's embedded caption (PNG text chunks / EXIF
  `ImageDescription`).
- **SD prompt reading** — extracts the positive / negative prompt and
  generation settings using `sd-prompt-reader` (same reader as the legacy
  ComfyCaption gallery), so existing images stay fully compatible.
- **ComfyUI workflow download** — detects the `workflow`/`prompt` stored in the
  image (PNG text chunks or EXIF `UserComment`) and downloads it as a
  `<name>_workflow.json` file.
- **Fits the SD Codex header** — the gallery is rendered inside the core app
  layout (`base.html`), keeping the SD Codex navbar/theme, with the gallery's
  controls docked into a header row just below it. No standalone page.
- **Details panel** — opening an image auto-opens the **Info** panel showing
  its caption, SD prompt, negative prompt and generation settings, with a
  **Download Workflow** action when a ComfyUI workflow is embedded.

Everything is read live from disk and image metadata — there are no saves, no
gallery tables, and no "save to gallery" flow.

## Installation (SDCodex Settings → Plugins)

Add this repository (`DeeplabSystems/SDCodex-Gallery`). After installation the
gallery appears in the sidebar as **Gallery** at `/gallery2`.

### Required volume (Docker)

- `GALLERY_ROOT`: the folder the gallery browses on disk
  (defaults to `./downloads` on the host / `/data/downloads` in the container).

## Development layout

```
SDCodex-Gallery/
├── plugin.json          # plugin manifest (id: "gallery")
├── plugin.py            # init_plugin: mounts the /gallery2 blueprint
├── gallery_backend.py   # Flask backend: listings, media, metadata APIs
├── requirements.txt     # sd-prompt-reader, piexif, Pillow
├── index.html           # FolderFrame gallery UI (v2 entry point)
├── app.js               # gallery + SDCodex metadata panel logic
├── settings.js          # config / URL + listing parsing
├── resilience.js        # concurrency & resilience helpers
├── styles.css           # functional stylesheet (drop in upstream to restyle)
├── heic2any.min.js      # HEIC decoder
└── generate_thumbnails.py
```

## Backend API

The Flask blueprint is mounted at `/gallery2`:

| Route | Purpose |
| --- | --- |
| `GET /` | Serves the gallery UI |
| `GET /folderframe.config.json` | Points the gallery at the `browse/` source |
| `GET /browse/…` | HTML autoindex of a folder, or a media file |
| `GET /api/meta?folder=&file=` | Caption + SD prompt + workflow presence |
| `GET /api/caption?folder=&file=` | Caption text |
| `GET /api/prompt?folder=&file=` | SD positive / negative / settings |
| `GET /api/workflow?folder=&file=` | Downloads the embedded ComfyUI workflow |

`folder` is relative to `GALLERY_ROOT`; `file` is the image filename. Path
traversal is blocked at the backend.

## Database?

**No.** The gallery reads directly from disk and image metadata, so the
database is no longer required for the gallery. The `init_plugin` accepts the
`db` argument only for plugin-manager compatibility and never uses it. (The
legacy ComfyCaption plugin that previously persisted "saved galleries" will
have its save-to-gallery path removed separately.)