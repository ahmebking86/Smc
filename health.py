"""health.py — Minimal Flask server for Railway health checks."""
import threading
import logging
from flask import Flask, jsonify
from config import PORT

logger = logging.getLogger(__name__)
app = Flask(__name__)

_bot_status: dict = {"status": "starting"}


def set_status(info: dict) -> None:
    _bot_status.update(info)


@app.route("/")
@app.route("/health")
def health():
    return jsonify({"ok": True, **_bot_status})


def start_health_server() -> None:
    """Run Flask in a background daemon thread."""
    def _run():
        import os
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, name="health-server", daemon=True)
    t.start()
    logger.info("Health server started on port %d", PORT)
