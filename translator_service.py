"""Translation helpers built on deep-translator (Google Translate).

The language list comes straight from the library, so every code is valid for it.
Results are cached in memory so repeated requests (swapping back and forth,
restoring history) do not hit Google again.
"""
from __future__ import annotations

import html
import logging
import re
import sys
import threading
import time
from functools import lru_cache
from urllib.parse import quote

MAX_CHARS = 5000  # Google Translate limit enforced by deep-translator
MIN_GAP_SECONDS = 0.4  # minimum pause between requests sent to Google
TOTAL_BUDGET_SECONDS = 40  # stop trying more providers after this long

GTX_URL = "https://translate.googleapis.com/translate_a/single"
GTX_CHUNK_LIMIT = 3500  # max URL-encoded characters per request

# Lingva is an open-source front end for Google Translate. Its servers talk to Google,
# so it works even when Google refuses your own IP address. Public instances come and go.
LINGVA_INSTANCES = (
    "https://lingva.ml",
    "https://lingva.garudalinux.org",
    "https://translate.plausibility.cloud",
)
LINGVA_CHUNK_LIMIT = 1200  # URL-encoded characters (the text travels inside the URL path)
LINGVA_CODES = {"zh-CN": "zh", "zh-TW": "zh_HANT"}

MYMEMORY_URL = "https://api.mymemory.translated.net/get"
MYMEMORY_CHUNK_BYTES = 450  # the service accepts at most 500 bytes per request
MYMEMORY_MAX_CHARS = 1500   # longer texts would need dozens of requests
MYMEMORY_CODES = {"iw": "he", "jw": "jv"}
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Shown first in the language menus.
POPULAR_CODES = ["en", "bn", "hi", "fr", "de", "es", "it", "pt", "nl", "ru", "zh-CN"]

# Used only if the library's own list cannot be read.
_FALLBACK = {
    "English": "en", "Bengali": "bn", "Hindi": "hi", "French": "fr", "German": "de",
    "Spanish": "es", "Italian": "it", "Portuguese": "pt", "Dutch": "nl",
    "Russian": "ru", "Chinese (Simplified)": "zh-CN",
}

log = logging.getLogger(__name__)


class TranslationError(Exception):
    """An error whose message is safe to show to the user."""

    status_code = 400
    detail = ""    # short technical reason from one provider, e.g. "HTTP 429"
    details = ()   # one line per provider when every provider failed


class RateLimited(TranslationError):
    status_code = 429


class ServiceUnavailable(TranslationError):
    status_code = 503


# --------------------------------------------------------------------------
# Library access
# --------------------------------------------------------------------------
def _library():
    try:
        from deep_translator import GoogleTranslator
    except ModuleNotFoundError as exc:
        raise ServiceUnavailable(
            f"deep-translator is not installed for this Python ({sys.version.split()[0]} at "
            f"{sys.executable}). Run: python -m pip install -r requirements.txt"
        ) from exc
    return GoogleTranslator


def _display_name(name: str) -> str:
    return name.title()


@lru_cache(maxsize=1)
def _load_languages() -> tuple:
    mapping = _library()(source="auto", target="en").get_supported_languages(as_dict=True)
    return tuple((_display_name(name), code) for name, code in mapping.items())


def get_languages() -> dict:
    """Return {"all": [...], "popular": [...]} where items are {"code", "name"}."""
    try:
        pairs = _load_languages()
    except Exception:  # noqa: BLE001 - fall back so the page still renders
        pairs = tuple(_FALLBACK.items())

    all_langs = sorted(({"code": c, "name": n} for n, c in pairs), key=lambda l: l["name"].lower())
    by_code = {l["code"].lower(): l for l in all_langs}
    popular = [by_code[c.lower()] for c in POPULAR_CODES if c.lower() in by_code]
    return {"all": all_langs, "popular": popular}


# --------------------------------------------------------------------------
# Translation
# --------------------------------------------------------------------------
RATE_LIMIT_MESSAGE = (
    "Google is refusing translation requests from your network right now (error 429), and the backup "
    "services could not help either. This usually clears after a few minutes. If it keeps happening, "
    "turn off any VPN or try another network."
)
UNAVAILABLE_MESSAGE = (
    "The translation services can't be reached or did not answer properly. "
    "Check your internet connection and try again."
)

LABELS = {
    "deep": "Google (web page)",
    "gtx": "Google (JSON)",
    "lingva": "Lingva",
    "mymemory": "MyMemory",
}

_throttle_lock = threading.Lock()
_last_request = 0.0
_preferred = "deep"  # provider that worked last; tried first next time


def _err(cls, detail: str, message: str = "") -> TranslationError:
    error = cls(message or detail)
    error.detail = detail
    return error


def _throttle() -> None:
    """Space out requests so a burst never looks like abuse."""
    global _last_request
    with _throttle_lock:
        wait = MIN_GAP_SECONDS - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _friendly(exc: Exception) -> TranslationError:
    """Map deep-translator / network exceptions to our own errors."""
    names = {cls.__name__ for cls in type(exc).__mro__}

    if "TooManyRequests" in names:
        return _err(RateLimited, "HTTP 429")
    if names & {"ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout", "RequestException"}:
        return _err(ServiceUnavailable, "no connection")
    if names & {"RequestError", "ServerException", "ElementNotFoundInGetRequest"}:
        return _err(ServiceUnavailable, "bad response")
    if "NotValidLength" in names:
        return _err(TranslationError, "text too long", f"This text is too long. The limit is {MAX_CHARS:,} characters.")
    if names & {"NotValidPayload", "TranslationNotFound"}:
        return _err(TranslationError, "no translation found", "No translation was found for this text. Try rephrasing it.")
    if "LanguageNotSupportedException" in names:
        return _err(TranslationError, "language not supported", "That language isn't supported.")

    log.exception("Unexpected translation failure")
    return _err(TranslationError, exc.__class__.__name__, f"Translation failed unexpectedly ({exc.__class__.__name__}).")


# -- HTTP helpers used by the requests-based providers ---------------------
def _get(url: str, *, params=None, timeout: int = 10):
    import requests

    _throttle()
    try:
        return requests.get(url, params=params, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise _err(ServiceUnavailable, f"no connection ({exc.__class__.__name__})") from exc


def _check_status(response) -> None:
    if response.status_code == 429:
        raise _err(RateLimited, "HTTP 429")
    if response.status_code != 200:
        raise _err(ServiceUnavailable, f"HTTP {response.status_code}")


def _split_text(text: str, limit: int, size) -> list:
    """Split text into pieces whose size (as measured by `size`) stays under `limit`."""
    pieces = re.split(r"(?<=[.!?\u0964\n])", text)  # keep sentence ends and newlines
    chunks, current = [], ""
    for piece in pieces:
        while size(piece) > limit:  # one huge sentence: cut it by characters
            cut = max(1, len(piece) * limit // size(piece) - 1)
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece[:cut])
            piece = piece[cut:]
        if current and size(current + piece) > limit:
            chunks.append(current)
            current = ""
        current += piece
    if current:
        chunks.append(current)
    return [c for c in chunks if c]


def _translate_chunks(text: str, limit: int, size, fetch) -> str:
    """Translate long text piece by piece, keeping the original spacing and line breaks."""
    output = []
    for chunk in _split_text(text, limit, size):
        body = chunk.strip()
        if not body:
            output.append(chunk)
            continue
        lead = chunk[: len(chunk) - len(chunk.lstrip())]
        tail = chunk[len(chunk.rstrip()):]
        output.append(lead + fetch(body) + tail)
    result = "".join(output)
    if not result.strip():
        raise _err(TranslationError, "empty result", "No translation was returned for this text. Try rephrasing it.")
    return result


# -- Provider 1: deep-translator (Google's web page) -------------------------
def _via_deep_translator(text: str, source: str, target: str) -> str:
    translator = _library()(source=source, target=target)
    for attempt in (1, 2):
        _throttle()
        try:
            result = translator.translate(text)
        except Exception as exc:  # noqa: BLE001
            error = _friendly(exc)
            if isinstance(error, RateLimited) and attempt == 1:
                time.sleep(1.5)  # one polite retry before giving up on this provider
                continue
            raise error from exc
        if not result:
            raise _err(TranslationError, "empty result", "No translation was returned for this text. Try rephrasing it.")
        return result
    raise _err(RateLimited, "HTTP 429")  # pragma: no cover


# -- Provider 2: Google's JSON endpoint (a different URL from provider 1) ----
def _via_gtx(text: str, source: str, target: str) -> str:
    def fetch(body: str) -> str:
        response = _get(
            GTX_URL, params={"client": "gtx", "sl": source, "tl": target, "dt": "t", "q": body}
        )
        _check_status(response)
        try:
            return "".join(part[0] for part in response.json()[0] if part and part[0])
        except (ValueError, IndexError, TypeError, KeyError) as exc:
            raise _err(ServiceUnavailable, "unreadable reply") from exc

    return _translate_chunks(text, GTX_CHUNK_LIMIT, lambda s: len(quote(s)), fetch)


# -- Provider 3: Lingva (its servers query Google, not your computer) --------
_lingva_instances = list(LINGVA_INSTANCES)


def _via_lingva(text: str, source: str, target: str) -> str:
    src = LINGVA_CODES.get(source, source)
    tgt = LINGVA_CODES.get(target, target)

    def fetch(body: str) -> str:
        last = None
        for base in list(_lingva_instances):
            url = f"{base}/api/v1/{quote(src, safe='')}/{quote(tgt, safe='')}/{quote(body, safe='')}"
            try:
                response = _get(url, timeout=8)
                _check_status(response)
                try:
                    data = response.json()
                    translated = data.get("translation")
                except (ValueError, AttributeError) as exc:
                    raise _err(ServiceUnavailable, "unreadable reply") from exc
                if not translated:
                    raise _err(ServiceUnavailable, str(data.get("error") or "empty reply")[:80])
            except (RateLimited, ServiceUnavailable) as exc:
                last = exc
                continue
            _lingva_instances.remove(base)       # remember the instance that works
            _lingva_instances.insert(0, base)
            return translated
        raise last or _err(ServiceUnavailable, "no Lingva instance answered")

    return _translate_chunks(text, LINGVA_CHUNK_LIMIT, lambda s: len(quote(s, safe="")), fetch)


# -- Provider 4: MyMemory (independent service, short texts only) ------------
def _via_mymemory(text: str, source: str, target: str) -> str:
    if len(text) > MYMEMORY_MAX_CHARS:
        raise _err(ServiceUnavailable, f"text over {MYMEMORY_MAX_CHARS:,} characters")
    src = "Autodetect" if source == "auto" else MYMEMORY_CODES.get(source, source)
    tgt = MYMEMORY_CODES.get(target, target)

    def fetch(body: str) -> str:
        response = _get(MYMEMORY_URL, params={"q": body, "langpair": f"{src}|{tgt}"})
        _check_status(response)
        try:
            data = response.json()
            status = int(data.get("responseStatus", 0))
            translated = data["responseData"]["translatedText"]
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise _err(ServiceUnavailable, "unreadable reply") from exc
        if status == 429 or "MYMEMORY WARNING" in translated.upper():
            raise _err(RateLimited, "free daily limit reached")
        if status != 200 or not translated:
            raise _err(ServiceUnavailable, f"status {status}")
        return html.unescape(translated)

    return _translate_chunks(text, MYMEMORY_CHUNK_BYTES, lambda s: len(s.encode("utf-8")), fetch)


_PROVIDERS = {
    "deep": _via_deep_translator,
    "gtx": _via_gtx,
    "lingva": _via_lingva,
    "mymemory": _via_mymemory,
}


@lru_cache(maxsize=256)
def _translate_cached(text: str, source: str, target: str) -> str:
    """Try the provider that worked last, then the others, until one answers."""
    global _preferred
    order = [_preferred] + [name for name in _PROVIDERS if name != _preferred]

    started = time.monotonic()
    failures = []  # (provider name, error)
    for name in order:
        if time.monotonic() - started > TOTAL_BUDGET_SECONDS:
            failures.append((name, _err(ServiceUnavailable, "skipped, took too long")))
            continue
        try:
            result = _PROVIDERS[name](text, source, target)
        except TranslationError as exc:
            log.warning("Provider %s failed: %s", name, exc.detail or exc)
            failures.append((name, exc))
            continue
        except Exception as exc:  # noqa: BLE001 - one broken provider must not block the others
            log.exception("Provider %s crashed", name)
            failures.append((name, _err(ServiceUnavailable, f"internal error ({exc.__class__.__name__})")))
            continue
        _preferred = name
        return result

    rate_limited = any(isinstance(exc, RateLimited) for _, exc in failures)
    if rate_limited:
        error = RateLimited(RATE_LIMIT_MESSAGE)
    elif any(isinstance(exc, ServiceUnavailable) for _, exc in failures):
        error = ServiceUnavailable(UNAVAILABLE_MESSAGE)
    else:
        error = failures[0][1]  # e.g. "text can't be translated": say so as is
    error.details = tuple(f"{LABELS[name]}: {exc.detail or exc}" for name, exc in failures)
    raise error


def translate(text, source: str, target: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise TranslationError("Enter some text to translate.")
    text = text.strip()
    if len(text) > MAX_CHARS:
        raise TranslationError(f"This text is too long ({len(text):,} characters). The limit is {MAX_CHARS:,}.")

    codes = {l["code"].lower(): l["code"] for l in get_languages()["all"]}
    src = "auto" if str(source).lower() == "auto" else codes.get(str(source).lower())
    tgt = codes.get(str(target).lower())
    if src is None or tgt is None:
        raise TranslationError("That language isn't supported.")

    if src == tgt:
        return text
    return _translate_cached(text, src, tgt)
