# Translator

A local web app that translates text between 100+ languages using Google Translate
(through the [`deep-translator`](https://pypi.org/project/deep-translator/) library).

## Features

- Press **Translate** (or **Ctrl+Enter**). An optional "Translate as I type" switch is off by default so you don't send needless requests
- Every language Google Translate supports, with "Detect language" as a source option
- Swap languages (and text) in one click
- Copy the translation, or listen to either side with your browser's built-in voices
- Recent translations are kept in your browser, with one click to restore them
- Light and dark mode (follows your system), works on phones

## Run it locally

You need **Python 3.9 or newer** and an **internet connection** (translation is done by Google Translate).

```bash
# 1. (recommended) create a virtual environment
python -m venv .venv

# 2. activate it
#    Windows (PowerShell):  .venv\Scripts\Activate.ps1
#    Windows (cmd):         .venv\Scripts\activate.bat
#    macOS / Linux:         source .venv/bin/activate

# 3. install the dependencies
python -m pip install -r requirements.txt

# 4. start the app
python app.py
```

Then open **http://127.0.0.1:8000** in your browser.

### Options

| Environment variable | Default     | Purpose                              |
| -------------------- | ----------- | ------------------------------------ |
| `PORT`               | `8000`      | Port to listen on                    |
| `HOST`               | `127.0.0.1` | Use `0.0.0.0` to open it to your LAN |

Example (PowerShell): `$env:PORT=5000; python app.py`

## Project structure

```
Translator/
├── app.py                 Flask server and API routes
├── translator_service.py  Language list, validation, translation, fallback, error messages
├── diagnose.py            Checks how Google Translate answers on your network
├── requirements.txt
├── templates/index.html   Page markup
└── static/
    ├── css/style.css      Design (notebook phrasebook theme)
    └── js/app.js          Typing, requests, history, copy, speech
```

## API

| Method | Route             | Description                                              |
| ------ | ----------------- | -------------------------------------------------------- |
| GET    | `/api/languages`  | `{"all": [{"code", "name"}], "popular": [...]}`           |
| POST   | `/api/translate`  | JSON `{"text", "source", "target"}`, returns `{"translation"}` |

`source` can be `"auto"`. Language codes are the ones from `/api/languages` (for example `en`, `bn`, `zh-CN`).

## What changed from the Colab version

- **`!pip install` removed from the script.** That line is Jupyter/Colab syntax and is a `SyntaxError`
  in a normal `.py` file. Dependencies now live in `requirements.txt`.
- **The command-line menu is now a web page.** `input()` prompts are replaced by the browser UI.
- **Language codes come from the library.** The old hard-coded list used `zh-cn` in lowercase, which newer
  versions of deep-translator can reject (it expects `zh-CN`). The menu now lists every language the
  library supports, with the exact codes it expects.
- **Source language is no longer fixed**, and you can pick "Detect language".
- **Real error handling.** Empty text, text over 5,000 characters, no internet, and Google rate limits each
  show a clear message instead of a stack trace.
- **README fixed.** It told you to run `python translator.py`, which never existed.
- **Fewer requests, and backups.** Requests are spaced out and retried once. If Google refuses your network the
  app tries, in order: Google's JSON endpoint, **Lingva** (open-source Google front end whose servers make the request
  for you), and **MyMemory** (independent service, texts up to 1,500 characters, free daily quota). Repeated
  translations are cached.

## Troubleshooting

- **"deep-translator is not installed for this Python"**: `pip` installed it into a different Python than the
  one running `app.py`. Run `python -m pip install -r requirements.txt` using the interpreter named in the message.
- **"Can't reach Google Translate"**: check your internet connection, VPN, or proxy.
- **"Google is refusing translation requests from your network (error 429)"**: Google is rate-limiting your
  IP address, which is common on shared connections (campus, mobile data) and with VPNs. It is not a bug in the
  app. The error shows what each service answered. What to do:
  1. Run `python diagnose.py`. It tests all four services and prints one line each.
  2. If Lingva or MyMemory says OK, translations will keep working through them.
  3. If everything fails, wait 10 to 30 minutes, switch network (a phone hotspot often works) or turn off your VPN.
  4. Behind a proxy? Set `HTTPS_PROXY` (for example `$env:HTTPS_PROXY="http://host:port"`) before `python app.py`.
  5. Keep "Translate as I type" switched off.
- **Lingva fails**: it relies on public community servers (`LINGVA_INSTANCES` in `translator_service.py`) that
  sometimes go offline. Replace them with ones that work, or self-host one.
- **MyMemory quality or limits**: it is the last resort. Translations can be rougher, and anonymous use has a daily
  free quota.
- **Port already in use**: start on another port, for example `PORT=8080 python app.py`.
- **No sound when pressing Listen**: speech uses voices installed on your device. Languages without an installed
  voice (often Bengali on Windows) are reported in a message. Add the voice in your system's language settings.
- **The fonts look different offline**: the page loads Newsreader and Instrument Sans from Google Fonts and falls
  back to system fonts offline.

## Limitations

This uses the free, unofficial Google Translate web endpoint through `deep-translator`. It is fine for personal
use, but it is not meant for heavy or commercial traffic. For that, use the official Google Cloud Translation API.
Translations are machine-generated and can contain mistakes, especially for idioms and technical text.
