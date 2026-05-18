"""
Tripwire — OSINT Social Media Monitor
Flask web application + REST API
"""

import re
import atexit
from flask import Flask, jsonify, render_template, request

import config
import database as db
import monitor

app = Flask(__name__)
app.secret_key = config.SECRET_KEY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def detect_platform(url: str) -> str:
    url = url.lower()
    if re.search(r"(twitter\.com|x\.com)/\w", url):
        return "twitter"
    return "generic"


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API — Targets
# ---------------------------------------------------------------------------

@app.route("/api/targets", methods=["GET"])
def api_list_targets():
    return jsonify(db.get_targets())


@app.route("/api/targets", methods=["POST"])
def api_add_targets():
    """
    Accepts a JSON body:
    {
        "urls": ["https://...", "https://..."],
        "label_prefix": "Subject A",   // optional
        "check_interval": 15            // minutes, optional
    }
    URLs can also be newline-separated in a single string field "urls_raw".
    """
    data = request.get_json(force=True, silent=True) or {}

    # Support both a list and a raw newline-separated string
    raw = data.get("urls_raw", "")
    urls = data.get("urls", [])
    if raw:
        urls += [u.strip() for u in raw.splitlines() if u.strip()]

    urls = [u for u in urls if u.startswith("http")]
    if not urls:
        return jsonify({"error": "No valid URLs provided"}), 400

    label_prefix = data.get("label_prefix", "").strip()
    check_interval = int(data.get("check_interval", config.DEFAULT_CHECK_INTERVAL))
    check_interval = max(5, min(check_interval, 1440))  # clamp 5 min – 24 h

    added = []
    for i, url in enumerate(urls):
        platform = detect_platform(url)
        # Auto-label: use prefix or derive from URL
        if label_prefix:
            label = label_prefix if len(urls) == 1 else f"{label_prefix} #{i+1}"
        else:
            label = url.rstrip("/").split("/")[-1] or url

        target_id = db.add_target(url, label, platform, check_interval)
        target = db.get_target(target_id)
        monitor.schedule_target(target)
        added.append(target)

    return jsonify({"added": added}), 201


@app.route("/api/targets/<int:target_id>", methods=["DELETE"])
def api_delete_target(target_id):
    if not db.get_target(target_id):
        return jsonify({"error": "Not found"}), 404
    monitor.unschedule_target(target_id)
    db.delete_target(target_id)
    return jsonify({"deleted": target_id})


@app.route("/api/targets/<int:target_id>/toggle", methods=["POST"])
def api_toggle_target(target_id):
    target = db.get_target(target_id)
    if not target:
        return jsonify({"error": "Not found"}), 404
    new_active = not bool(target["active"])
    db.toggle_target(target_id, new_active)
    if new_active:
        monitor.schedule_target(db.get_target(target_id))
    else:
        monitor.unschedule_target(target_id)
    return jsonify({"active": new_active})


@app.route("/api/targets/<int:target_id>/check", methods=["POST"])
def api_check_now(target_id):
    target = db.get_target(target_id)
    if not target:
        return jsonify({"error": "Not found"}), 404
    # Run synchronously in the request thread (keep it simple for manual triggers)
    monitor.check_now(target_id)
    return jsonify(db.get_target(target_id))


# ---------------------------------------------------------------------------
# API — Alerts
# ---------------------------------------------------------------------------

@app.route("/api/alerts", methods=["GET"])
def api_list_alerts():
    limit = int(request.args.get("limit", 50))
    return jsonify(db.get_alerts(limit=limit))


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------

def create_app():
    db.init_db()
    monitor.start()
    atexit.register(monitor.stop)
    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5500, debug=False)
