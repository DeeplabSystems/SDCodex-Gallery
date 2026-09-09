import os
import sys

plugin_dir = os.path.dirname(os.path.abspath(__file__))
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

from gallery_backend import gallery as gallery2_bp


def init_plugin(app, db, plugin_info=None):
    """Initialize the SDCodex Gallery plugin.

    The gallery reads media, captions, SD prompts and ComfyUI workflows
    directly from disk and image metadata, so it does not touch the database.
    ``db`` is accepted for API compatibility with the plugin manager but is
    intentionally unused.
    """
    if "gallery2" not in app.blueprints:
        app.register_blueprint(gallery2_bp, url_prefix="/gallery2")
        app.logger.info("SDCodex Gallery mounted at /gallery2")
    return app