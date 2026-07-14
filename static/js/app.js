(() => {
  "use strict";

  const state = { matches: [], selectedId: null, voiceOn: true, history: [] };

  const el = (sel) => document.querySelector(sel);
  const clockEl = el("#clock");
  const statusPill = el("#status-pill");
  const statusText = el("#status-text");
  const sourceLabel = el("#data-source-label");
  const matchListEl = el("#match-list");
  const detailEl = el("#match-detail");
  const tickerEl = el("#ticker");
  const chatLog = el("#chat-log");
  const chatForm = el("#chat-form");
  const chatInput = el("#chat-input");
  const voiceToggle = el("#voice-toggle");
  const talkBtn = el("#talk-btn");

  function tick() {
    const now = new Date();
    clockEl.textContent = now.toUTCString().slice(17, 25) + " UTC";
  }
  setInterval(tick, 1000);
  tick();

  function pct(x) { return `${Math.round(x * 100)}%`; }

  // Render's free tier spins the server down after inactivity, and the docs warn the
  // first request after that can take "50 seconds or more" to wake it back up -- the
  // earlier linear backoff (3+6+9s ~= 18s total) gave up well before that. Exponential
  // backoff starting at 3s (3+6+12+24+48s ~= 93s total across 6 attempts) comfortably
  // covers a worst-case cold start instead of giving up on the first failed attempt.
  async function fetchJsonWithRetry(url, { attempts = 6, delayMs = 3000, options } = {}) {
    let lastError;
    for (let i = 0; i < attempts; i++) {
      try {
        const res = await fetch(url, options);
        return await res.json();
      } catch (e) {
        lastError = e;
        if (i < attempts - 1) await new Promise((r) => setTimeout(r, delayMs * Math.pow(2, i)));
      }
    }
    throw lastError;
  }

  function fmtTime(iso) {
    try {
      return new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
    } catch { return iso; }
  }

  async function loadStatus() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      statusPill.classList.toggle("online", !data.demo_mode);
      statusPill.classList.toggle("demo", data.demo_mode);
      statusText.textContent = data.demo_mode ? "SIMULATION MODE" : "LIVE";
      sourceLabel.textContent = `data source: ${data.data_source}`;
    } catch (e) {
      statusText.textContent = "OFFLINE";
    }
  }

  function renderMatchList() {
    if (!state.matches.length) {
      matchListEl.innerHTML = '<div class="loading">NO FIXTURES FOUND</div>';
      return;
    }
    matchListEl.innerHTML = state.matches.map((m) => `
      <div class="match-card ${m.match_id === state.selectedId ? "selected" : ""}" data-id="${m.match_id}">
        <div class="mc-top"><span>${m.tournament} · ${m.round}</span><span>${fmtTime(m.start_time)}</span></div>
        <div class="mc-players"><b>${m.player1.name}</b> vs <b>${m.player2.name}</b></div>
        <div class="mc-value">
          ${m.has_prediction === false
            ? `<span style="color:var(--text-dim)">WIRD ANALYSIERT…</span>`
            : m.is_value_bet
              ? `<span class="value-tag">EDGE ${(m.expected_value * 100).toFixed(1)}%</span>`
              : `<span style="color:var(--text-dim)">EV ${(m.expected_value * 100).toFixed(1)}%</span>`}
        </div>
      </div>
    `).join("");
    matchListEl.querySelectorAll(".match-card").forEach((card) => {
      card.addEventListener("click", () => selectMatch(parseInt(card.dataset.id, 10)));
    });
  }

  function renderDetail(m) {
    if (!m) {
      detailEl.innerHTML = '<div class="placeholder">Select a match to run the analysis.</div>';
      return;
    }
    if (m.has_prediction === false) {
      detailEl.innerHTML = `
        <div class="detail-head"><span class="tourney">${m.tournament} — ${m.round || "?"} — ${m.surface || "?"}</span></div>
        <div class="detail-players">${m.player1.name}<span class="vs">VS</span>${m.player2.name}</div>
        <div class="placeholder">Noch keine Modell-Prognose für dieses Match — der nächste Trainingslauf holt das nach.</div>
      `;
      return;
    }
    const p1 = m.player1, p2 = m.player2;
    const p1Pct = m.player1_win_prob, p2Pct = m.player2_win_prob;
    detailEl.innerHTML = `
      <div class="detail-head"><span class="tourney">${m.tournament} — ${m.round} — ${m.surface}</span></div>
      <div class="detail-players">${p1.name}<span class="vs">VS</span>${p2.name}</div>

      <div class="prob-row">
        <div class="prob-col">
          <div class="name">${p1.name}</div>
          <div class="gauge"><div class="gauge-fill" style="width:${pct(p1Pct)}"></div></div>
          <div class="prob-pct">${pct(p1Pct)}</div>
        </div>
        <div class="prob-col">
          <div class="name">${p2.name}</div>
          <div class="gauge"><div class="gauge-fill" style="width:${pct(p2Pct)}"></div></div>
          <div class="prob-pct">${pct(p2Pct)}</div>
        </div>
      </div>

      <div class="odds-grid">
        <div class="odds-box"><div class="label">TIPICO — ${p1.name.split(" ").pop()}</div><div class="val">${m.tipico_player1_odds.toFixed(2)}</div></div>
        <div class="odds-box"><div class="label">TIPICO — ${p2.name.split(" ").pop()}</div><div class="val">${m.tipico_player2_odds.toFixed(2)}</div></div>
        <div class="odds-box ${m.expected_value >= 0 ? "ev-positive" : "ev-negative"}"><div class="label">EXPECTED VALUE</div><div class="val">${m.expected_value >= 0 ? "+" : ""}${(m.expected_value * 100).toFixed(1)}%</div></div>
      </div>

      ${m.is_value_bet ? `<div class="value-banner">VALUE BET DETECTED — model favors ${m.pick} beyond Tipico's price</div>` : ""}
    `;
  }

  function selectMatch(id) {
    state.selectedId = id;
    renderMatchList();
    renderDetail(state.matches.find((m) => m.match_id === id));
  }

  async function loadMatches() {
    matchListEl.innerHTML = '<div class="loading">VERBINDE…</div>';
    try {
      const data = await fetchJsonWithRetry("/api/matches");
      state.matches = data.matches || [];
      renderMatchList();
      if (state.matches.length && state.selectedId === null) {
        selectMatch(state.matches[0].match_id);
      }
    } catch (e) {
      matchListEl.innerHTML = '<div class="loading">CONNECTION LOST</div>';
    }
  }

  async function loadTicker() {
    try {
      const res = await fetch("/api/value-bets");
      const data = await res.json();
      const bets = data.value_bets || [];
      if (!bets.length) {
        tickerEl.innerHTML = "<span>No positive-EV bets against Tipico's line right now.</span>";
        return;
      }
      const text = bets.map((b) => {
        const pick = b.pick || (b.player1_win_prob >= 0.5 ? b.player1?.name : b.player2?.name);
        return `${pick} @ ${b.tournament} — EV ${(b.expected_value * 100).toFixed(1)}%`;
      }).join("     •     ");
      tickerEl.innerHTML = `<span>${text}</span>`;
    } catch (e) {
      tickerEl.innerHTML = "<span>Ticker offline.</span>";
    }
  }

  function speak(text) {
    if (!state.voiceOn || !("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = "de-DE";
    utter.rate = 1.02;
    utter.pitch = 0.75;
    const voices = window.speechSynthesis.getVoices();
    const german = voices.filter((v) => v.lang.toLowerCase().startsWith("de"));
    // Voice engines rarely label gender explicitly; known male German voice names
    // (Google/Microsoft/Apple TTS) are matched first, then any other German voice,
    // then whatever the browser offers as a fallback.
    const preferred =
      german.find((v) => /male|männlich|stefan|markus|yannick|daniel|conrad/i.test(v.name)) ||
      german[0] ||
      voices.find((v) => /male/i.test(v.name)) ||
      voices[0];
    if (preferred) utter.voice = preferred;
    window.speechSynthesis.speak(utter);
  }

  function stripMarkdown(text) {
    return text
      .replace(/\*\*(.+?)\*\*/g, "$1")
      .replace(/^\s*[-*]\s+/gm, "");
  }

  function escapeHtml(str) {
    return str.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function inlineFormat(line) {
    return escapeHtml(line).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  // Small, safe subset of Markdown -- just what the assistant's system prompt is told
  // to use (bold, short paragraphs, "- " lists) -- not a full parser.
  function renderMarkdown(text) {
    let html = "";
    let inList = false;
    for (const line of text.split("\n")) {
      const bullet = line.match(/^\s*[-*]\s+(.+)/);
      if (bullet) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += `<li>${inlineFormat(bullet[1])}</li>`;
        continue;
      }
      if (inList) { html += "</ul>"; inList = false; }
      html += line.trim() ? `<p>${inlineFormat(line)}</p>` : "<br>";
    }
    if (inList) html += "</ul>";
    return html;
  }

  function addMessage(who, text, animate) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${who}`;
    wrap.innerHTML = `<span class="who">${who === "jarvis" ? "JARVIS" : "YOU"}</span><span class="txt"></span>`;
    chatLog.appendChild(wrap);
    chatLog.scrollTop = chatLog.scrollHeight;
    const txtEl = wrap.querySelector(".txt");

    if (!animate) {
      txtEl.textContent = text;
      wrap.classList.add("done");
      return;
    }
    let i = 0;
    const step = () => {
      txtEl.textContent = text.slice(0, i);
      chatLog.scrollTop = chatLog.scrollHeight;
      i += 2;
      if (i <= text.length) {
        requestAnimationFrame(() => setTimeout(step, 12));
      } else {
        txtEl.innerHTML = who === "jarvis" ? renderMarkdown(text) : escapeHtml(text);
        wrap.classList.add("done");
      }
    };
    step();
  }

  async function sendMessage(message) {
    addMessage("user", message, false);
    state.history.push({ role: "user", content: message });
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, history: state.history.slice(-10) }),
      });
      const data = await res.json();
      addMessage("jarvis", data.reply, true);
      speak(stripMarkdown(data.reply));
      state.history.push({ role: "assistant", content: data.reply });
    } catch (e) {
      addMessage("jarvis", "Die Verbindung zum Analyse-Kern wurde unterbrochen.", true);
    }
  }

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const val = chatInput.value.trim();
    if (!val) return;
    chatInput.value = "";
    sendMessage(val);
  });

  voiceToggle.addEventListener("click", () => {
    state.voiceOn = !state.voiceOn;
    voiceToggle.classList.toggle("muted", !state.voiceOn);
    voiceToggle.textContent = state.voiceOn ? "🔊" : "🔇";
    if (!state.voiceOn && "speechSynthesis" in window) window.speechSynthesis.cancel();
  });

  // Press-to-talk: tap the button, speak, JARVIS answers out loud -- same idea as
  // holding the power button for Bixby/Gemini, so no typing is needed day to day.
  const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
  function setTalkState(mode) {
    talkBtn.classList.toggle("listening", mode === "listening");
    talkBtn.classList.toggle("thinking", mode === "thinking");
    talkBtn.querySelector(".talk-label").textContent =
      mode === "listening" ? "HÖRT ZU…" : mode === "thinking" ? "DENKT NACH…" : "J.A.R.V.I.S.";
  }

  if (!SpeechRecognitionCtor) {
    talkBtn.disabled = true;
    talkBtn.querySelector(".talk-label").textContent = "NICHT VERFÜGBAR";
  } else {
    const recognition = new SpeechRecognitionCtor();
    recognition.lang = "de-DE";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    let listening = false;

    recognition.addEventListener("result", (e) => {
      const transcript = e.results[0][0].transcript.trim();
      if (transcript) {
        setTalkState("thinking");
        sendMessage(transcript).finally(() => setTalkState("idle"));
      }
    });
    recognition.addEventListener("end", () => {
      listening = false;
      if (!talkBtn.classList.contains("thinking")) setTalkState("idle");
    });
    recognition.addEventListener("error", () => {
      listening = false;
      setTalkState("idle");
    });

    talkBtn.addEventListener("click", () => {
      if (listening) {
        recognition.stop();
        return;
      }
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      listening = true;
      setTalkState("listening");
      try {
        recognition.start();
      } catch (e) {
        listening = false;
        setTalkState("idle");
      }
    });
  }

  loadStatus();
  loadMatches().then(loadTicker);
  setInterval(loadStatus, 30000);
  setInterval(loadTicker, 45000);
})();
