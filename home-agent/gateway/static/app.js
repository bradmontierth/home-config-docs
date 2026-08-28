const statusEl = document.getElementById("status");
const apkDownloadLink = document.getElementById("apkDownloadLink");
const talkBtn = document.getElementById("talkBtn");
const transcriptEl = document.getElementById("transcript");
const runBtn = document.getElementById("runBtn");
const clearBtn = document.getElementById("clearBtn");
const newConversationBtn = document.getElementById("newConversationBtn");
const terminalEl = document.getElementById("terminal");
const stopBtn = document.getElementById("stopBtn");
const sendForm = document.getElementById("sendForm");
const messageEl = document.getElementById("message");

let recorder = null;
let chunks = [];
let sessionId = null;
let socket = null;

function setStatus(text) {
  statusEl.textContent = text;
}

function appendTerminal(text) {
  terminalEl.textContent += text;
  terminalEl.scrollTop = terminalEl.scrollHeight;
  if (text.includes("AWAITING_PHONE_APPROVAL:")) {
    terminalEl.classList.add("awaiting");
    setStatus("Waiting for approval");
  }
}

function authQuery() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  return token ? `?token=${encodeURIComponent(token)}` : "";
}

apkDownloadLink.href = `/api/android-apk${authQuery()}`;

apkDownloadLink.href = `/api/android-apk${authQuery()}`;

async function apiFetch(url, options = {}) {
  const response = await fetch(`${url}${authQuery()}`, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response;
}

async function startRecording() {
  if (recorder && recorder.state === "recording") return;
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  };
  recorder.onstop = async () => {
    stream.getTracks().forEach((track) => track.stop());
    await uploadRecording();
  };
  recorder.start();
  talkBtn.classList.add("recording");
  setStatus("Recording");
}

function stopRecording() {
  if (!recorder || recorder.state !== "recording") return;
  talkBtn.classList.remove("recording");
  setStatus("Transcribing");
  recorder.stop();
}

async function uploadRecording() {
  try {
    const type = chunks[0]?.type || "audio/webm";
    const blob = new Blob(chunks, { type });
    const form = new FormData();
    form.append("audio", blob, "voice.webm");
    const response = await apiFetch("/api/transcribe", { method: "POST", body: form });
    const payload = await response.json();
    transcriptEl.value = payload.text || "";
    runBtn.disabled = !transcriptEl.value.trim();
    setStatus("Transcript ready");
  } catch (error) {
    setStatus("Transcription failed");
    appendTerminal(`\n[transcription error] ${error.message}\n`);
  }
}

async function startSession() {
  const text = transcriptEl.value.trim();
  if (!text) return;
  setStatus("Starting Codex");
  document.body.classList.add("terminal-expanded");
  terminalEl.textContent = "";
  terminalEl.classList.remove("awaiting");
  const response = await apiFetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const payload = await response.json();
  sessionId = payload.session_id;
  stopBtn.disabled = false;
  connectSession(sessionId);
}

function connectSession(id) {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${window.location.host}/ws/sessions/${id}${authQuery()}`);
  socket.onopen = () => setStatus(`Session ${id}`);
  socket.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "output") appendTerminal(payload.data);
    if (payload.type === "status") {
      if (payload.status === "exited") {
        setStatus(`Session exited (${payload.returncode})`);
        stopBtn.disabled = true;
      } else if (payload.status === "waiting") {
        const tasks = Array.isArray(payload.tasks) ? payload.tasks.join(", ") : "";
        setStatus(tasks ? `Waiting on background work: ${tasks}` : "Waiting on background work");
      } else {
        setStatus(payload.status);
      }
    }
  };
  socket.onclose = () => {
    if (sessionId) setStatus("Disconnected");
  };
}

function sendToSession(text) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  terminalEl.classList.remove("awaiting");
  socket.send(JSON.stringify({ type: "input", data: text }));
}

talkBtn.addEventListener("pointerdown", async (event) => {
  event.preventDefault();
  try {
    await startRecording();
  } catch (error) {
    setStatus("Microphone unavailable");
    appendTerminal(`\n[microphone error] ${error.message}\n`);
  }
});

talkBtn.addEventListener("pointerup", stopRecording);
talkBtn.addEventListener("pointercancel", stopRecording);
talkBtn.addEventListener("lostpointercapture", stopRecording);

transcriptEl.addEventListener("input", () => {
  runBtn.disabled = !transcriptEl.value.trim();
});

runBtn.addEventListener("click", startSession);

clearBtn.addEventListener("click", () => {
  transcriptEl.value = "";
  runBtn.disabled = true;
  setStatus("Ready");
});

newConversationBtn.addEventListener("click", () => {
  if (socket) {
    socket.close(1000, "new conversation");
    socket = null;
  }
  sessionId = null;
  transcriptEl.value = "";
  messageEl.value = "";
  terminalEl.textContent = "";
  terminalEl.classList.remove("awaiting");
  document.body.classList.remove("terminal-expanded");
  runBtn.disabled = true;
  stopBtn.disabled = true;
  setStatus("Ready");
});

stopBtn.addEventListener("click", async () => {
  if (!sessionId) return;
  sendToSession("Stop now. Do not take further actions.");
  await apiFetch(`/api/sessions/${sessionId}/stop`, { method: "POST" });
  stopBtn.disabled = true;
});

document.querySelectorAll("[data-send]").forEach((button) => {
  button.addEventListener("click", () => sendToSession(button.dataset.send));
});

sendForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = messageEl.value.trim();
  if (!text) return;
  sendToSession(text);
  messageEl.value = "";
});
