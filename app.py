"""Local web app for translating text with Google Translate (via deep-translator).

Run:  python app.py      then open  http://127.0.0.1:8000
"""
import os

from flask import Flask, jsonify, render_template, request

import translator_service as service

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024


@app.get("/")
def index():
    languages = service.get_languages()
    popular_codes = {l["code"] for l in languages["popular"]}
    others = [l for l in languages["all"] if l["code"] not in popular_codes]
    return render_template(
        "index.html",
        popular=languages["popular"],
        others=others,
        count=len(languages["all"]),
        max_chars=service.MAX_CHARS,
    )


@app.get("/api/languages")
def languages():
    return jsonify(service.get_languages())


@app.post("/api/translate")
def translate():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Send a JSON body with text, source and target."), 400

    try:
        translation = service.translate(
            data.get("text"), data.get("source", "auto"), data.get("target", "")
        )
    except service.TranslationError as exc:
        return jsonify(error=str(exc), details=list(exc.details)), exc.status_code
    except Exception:  # noqa: BLE001
        app.logger.exception("Unexpected error while translating")
        return jsonify(error="Something went wrong while translating."), 500

    return jsonify(translation=translation)


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error="That request is too large."), 413


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"\n  Translator running at http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False, threaded=True)
