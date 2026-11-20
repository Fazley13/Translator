(() => {
  "use strict";

  const MAX_CHARS = 5000; // keep in sync with MAX_CHARS in translator_service.py
  const DEBOUNCE_MS = 1200;   // only used when "Translate as I type" is on
  const COOLDOWN_SECONDS = 20; // pause after Google answers "too many requests"
  const HISTORY_MAX = 8;
  const KEYS = { langs: "translator.langs", history: "translator.history", auto: "translator.auto" };

  const $ = (selector) => document.querySelector(selector);
  const el = {
    src: $("#srcLang"),
    tgt: $("#tgtLang"),
    text: $("#srcText"),
    out: $("#outText"),
    count: $("#count"),
    status: $("#status"),
    swap: $("#swapBtn"),
    translate: $("#translateBtn"),
    auto: $("#autoToggle"),
    clear: $("#clearText"),
    copy: $("#copyBtn"),
    speakSrc: $("#speakSrc"),
    speakOut: $("#speakOut"),
    recent: $("#recent"),
    list: $("#historyList"),
    clearHistory: $("#clearHistory"),
    toast: $("#toast"),
  };

  // ---- Helpers --------------------------------------------------------------
  function h(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    node.append(...children.flat().filter((c) => c != null && c !== false));
    return node;
  }

  const store = {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ }
    },
  };

  const hasOption = (select, value) => Array.from(select.options).some((o) => o.value === value);
  const langName = (code) => {
    const option = Array.from(el.src.options).find((o) => o.value === code);
    return option ? option.textContent : code;
  };

  let toastTimer = null;
  function toast(message) {
    el.toast.textContent = message;
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.toast.hidden = true; }, 2800);
  }

  // ---- State ----------------------------------------------------------------
  let state = { phase: "empty", result: null, error: null, details: [] }; // phase: empty | loading | done | error
  let debounceTimer = null;
  let saveTimer = null;
  let controller = null;
  let sequence = 0;
  let history = store.get(KEYS.history, []);

  // ---- Rendering ------------------------------------------------------------
  function renderOutput() {
    el.out.dataset.phase = state.phase;

    if (state.phase === "error") {
      el.out.replaceChildren(
        h("p", { class: "err", role: "alert", text: state.error }),
        state.details && state.details.length
          ? h("p", { class: "err-details", text: `Details: ${state.details.join("; ")}` })
          : null,
        h("button", { type: "button", class: "btn", text: "Try again", onclick: translateNow }),
      );
    } else if (state.result) {
      el.out.textContent = state.result; // kept (dimmed) while a new request is running
    } else {
      el.out.replaceChildren(
        h("span", {
          class: "placeholder",
          text: state.phase === "loading" ? "Translating…" : "Your translation will appear here.",
        }),
      );
    }

    const ready = state.phase === "done" && Boolean(state.result);
    el.copy.disabled = !ready;
    el.speakOut.disabled = !ready;
  }

  function setStatus(message = "") {
    el.status.textContent = message;
  }

  function updateCount() {
    const length = el.text.value.length;
    el.count.textContent = `${length.toLocaleString()} / ${MAX_CHARS.toLocaleString()}`;
    el.count.dataset.near = String(length > MAX_CHARS * 0.9);
  }

  function applyLanguages() {
    const src = el.src.value;
    const tgt = el.tgt.value;
    el.text.lang = src === "auto" ? "" : src;
    el.out.lang = tgt;
    el.swap.disabled = src === "auto";
    el.swap.title = src === "auto" ? "Choose a source language to swap" : "Swap languages";
    store.set(KEYS.langs, { src, tgt });
  }

  // ---- Translate button and cooldown ----------------------------------------
  let cooldownUntil = 0;
  let cooldownTimer = null;
  const cooldownLeft = () => Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));

  function updateTranslateButton() {
    const wait = cooldownLeft();
    if (wait > 0) {
      el.translate.disabled = true;
      el.translate.textContent = `Wait ${wait}s`;
    } else {
      el.translate.disabled = state.phase === "loading";
      el.translate.textContent = state.phase === "loading" ? "Translating…" : "Translate";
    }
  }

  function startCooldown(seconds) {
    cooldownUntil = Date.now() + seconds * 1000;
    clearInterval(cooldownTimer);
    cooldownTimer = setInterval(() => {
      updateTranslateButton();
      if (cooldownLeft() === 0) clearInterval(cooldownTimer);
    }, 500);
    updateTranslateButton();
  }

  // ---- Translating ----------------------------------------------------------
  function scheduleTranslate() {
    clearTimeout(debounceTimer);
    if (!el.text.value.trim()) {
      translateNow(); // clears immediately
      return;
    }
    if (el.auto.checked) {
      debounceTimer = setTimeout(translateNow, DEBOUNCE_MS);
    } else if (state.phase === "done") {
      setStatus("Press Translate to update.");
    }
  }

  async function translateNow() {
    clearTimeout(debounceTimer);
    const text = el.text.value.trim();
    const source = el.src.value;
    const target = el.tgt.value;
    const mine = ++sequence;
    if (controller) controller.abort();

    if (!text) {
      state = { phase: "empty", result: null, error: null };
      setStatus();
      renderOutput();
      updateTranslateButton();
      return;
    }

    if (source === target) {
      state = { phase: "done", result: text, error: null };
      setStatus("Both languages are the same.");
      renderOutput();
      updateTranslateButton();
      return;
    }

    if (cooldownLeft() > 0) {
      toast(`Google asked us to slow down. Try again in ${cooldownLeft()} s.`);
      return;
    }

    controller = new AbortController();
    state = { ...state, phase: "loading" };
    setStatus("Translating…");
    renderOutput();
    updateTranslateButton();

    try {
      const response = await fetch("/api/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, source, target }),
        signal: controller.signal,
      });
      let data = null;
      try { data = await response.json(); } catch { /* non-JSON error page */ }
      if (mine !== sequence) return;

      if (!response.ok) {
        const failure = new Error((data && data.error) || `The server returned an error (${response.status}).`);
        failure.status = response.status;
        failure.details = (data && data.details) || [];
        throw failure;
      }
      state = { phase: "done", result: data.translation, error: null };
      setStatus();
      renderOutput();
      updateTranslateButton();
      scheduleSave(text, source, target, data.translation);
    } catch (error) {
      if (error.name === "AbortError" || mine !== sequence) return;
      state = {
        phase: "error",
        result: null,
        error: error instanceof TypeError
          ? "Couldn't reach the local server. Check that app.py is still running."
          : error.message,
        details: error.details || [],
      };
      setStatus();
      if (error.status === 429) startCooldown(COOLDOWN_SECONDS);
      renderOutput();
      updateTranslateButton();
    }
  }

  // ---- History --------------------------------------------------------------
  function scheduleSave(text, src, tgt, out) {
    clearTimeout(saveTimer);
    // Wait until the person stops editing so half-typed sentences aren't saved.
    saveTimer = setTimeout(() => {
      history = [
        { text, src, tgt, out },
        ...history.filter((e) => !(e.text === text && e.src === src && e.tgt === tgt)),
      ].slice(0, HISTORY_MAX);
      store.set(KEYS.history, history);
      renderHistory();
    }, 2000);
  }

  function renderHistory() {
    el.recent.hidden = history.length === 0;
    el.list.replaceChildren(
      ...history.map((entry) =>
        h("li", {},
          h("button", {
            type: "button",
            onclick: () => restore(entry),
            "aria-label": `Restore translation from ${langName(entry.src)} to ${langName(entry.tgt)}`,
          },
            h("span", { class: "pair-label", text: `${langName(entry.src)} to ${langName(entry.tgt)}` }),
            h("span", { class: "src", dir: "auto", text: entry.text }),
            h("span", { class: "res", dir: "auto", text: entry.out }),
          ),
        ),
      ),
    );
  }

  function restore(entry) {
    if (!hasOption(el.src, entry.src) || !hasOption(el.tgt, entry.tgt)) return;
    el.src.value = entry.src;
    el.tgt.value = entry.tgt;
    el.text.value = entry.text;
    sequence++;
    if (controller) controller.abort();
    state = { phase: "done", result: entry.out, error: null };
    applyLanguages();
    updateCount();
    setStatus();
    renderOutput();
    window.scrollTo({ top: 0, behavior: "smooth" });
    el.text.focus();
  }

  // ---- Copy and speech ------------------------------------------------------
  let copyTimer = null;
  async function copyResult() {
    if (!state.result) return;
    try {
      await navigator.clipboard.writeText(state.result);
    } catch {
      const area = h("textarea", { "aria-hidden": "true", style: "position:fixed;opacity:0" });
      area.value = state.result;
      document.body.append(area);
      area.select();
      const ok = document.execCommand && document.execCommand("copy");
      area.remove();
      if (!ok) { toast("Couldn't copy. Select the text and copy it manually."); return; }
    }
    el.copy.textContent = "Copied";
    el.copy.dataset.done = "true";
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => {
      el.copy.textContent = "Copy translation";
      el.copy.dataset.done = "false";
    }, 1600);
  }

  let speakingButton = null;
  function speak(text, lang, button) {
    const synth = window.speechSynthesis;
    if (!synth || !text.trim()) { toast("Speech isn't available in this browser."); return; }

    const wasSame = speakingButton === button;
    if (synth.speaking || synth.pending) {
      synth.cancel();
      if (speakingButton) speakingButton.dataset.active = "false";
      speakingButton = null;
      if (wasSame) return;
    }

    const base = (lang || "").split("-")[0].toLowerCase();
    const voices = synth.getVoices();
    if (base && base !== "auto" && voices.length && !voices.some((v) => v.lang.toLowerCase().startsWith(base))) {
      toast(`No ${langName(lang)} voice is installed on this device.`);
      return;
    }

    const utterance = new SpeechSynthesisUtterance(text);
    if (base && base !== "auto") utterance.lang = lang;
    const finish = () => {
      button.dataset.active = "false";
      if (speakingButton === button) speakingButton = null;
    };
    utterance.onend = finish;
    utterance.onerror = finish;
    button.dataset.active = "true";
    speakingButton = button;
    synth.speak(utterance);
  }

  // ---- Events ---------------------------------------------------------------
  el.text.addEventListener("input", () => {
    updateCount();
    scheduleTranslate();
  });

  el.text.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      translateNow();
    }
  });

  el.text.addEventListener("paste", (event) => {
    const pasted = event.clipboardData ? event.clipboardData.getData("text") : "";
    const selected = el.text.selectionEnd - el.text.selectionStart;
    if (el.text.value.length - selected + pasted.length > MAX_CHARS) {
      toast(`Text was cut to ${MAX_CHARS.toLocaleString()} characters.`);
    }
  });

  function onLanguageChange() {
    applyLanguages();
    const hasText = el.text.value.trim().length > 0;
    // Re-translate only if there is already a result on screen (or auto mode is on),
    // so flipping through the menu doesn't fire a request per click.
    // A request still running is for the old language pair, so it is replaced too.
    if (hasText && (el.auto.checked || ["done", "error", "loading"].includes(state.phase))) translateNow();
  }
  el.src.addEventListener("change", onLanguageChange);
  el.tgt.addEventListener("change", onLanguageChange);

  el.translate.addEventListener("click", translateNow);
  el.auto.addEventListener("change", () => {
    store.set(KEYS.auto, el.auto.checked);
    if (el.auto.checked && el.text.value.trim()) scheduleTranslate();
  });

  el.swap.addEventListener("click", () => {
    if (el.src.value === "auto") return;
    const previousSource = el.src.value;
    el.src.value = el.tgt.value;
    el.tgt.value = previousSource;
    if (state.phase === "done" && state.result) el.text.value = state.result;
    el.swap.classList.toggle("flip");
    applyLanguages();
    updateCount();
    translateNow();
  });

  el.clear.addEventListener("click", () => {
    el.text.value = "";
    updateCount();
    translateNow();
    el.text.focus();
  });

  el.copy.addEventListener("click", copyResult);
  el.speakSrc.addEventListener("click", () => speak(el.text.value, el.src.value, el.speakSrc));
  el.speakOut.addEventListener("click", () => speak(state.result || "", el.tgt.value, el.speakOut));

  el.clearHistory.addEventListener("click", () => {
    history = [];
    store.set(KEYS.history, history);
    renderHistory();
  });

  if (!("speechSynthesis" in window)) {
    el.speakSrc.hidden = true;
    el.speakOut.hidden = true;
  }

  // ---- Start ----------------------------------------------------------------
  const saved = store.get(KEYS.langs, null);
  if (saved && hasOption(el.src, saved.src) && hasOption(el.tgt, saved.tgt)) {
    el.src.value = saved.src;
    el.tgt.value = saved.tgt;
  }
  el.auto.checked = store.get(KEYS.auto, false) === true;
  applyLanguages();
  updateCount();
  renderOutput();
  renderHistory();
  updateTranslateButton();
})();
