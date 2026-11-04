// Record a track in the browser (MediaRecorder) and add it like an uploaded file. The audio
// stays on this device until the parent taps "Hinzufügen"; the upload form (data-upload in
// app.js) then sends it with a progress bar. Browsers without a microphone API keep the
// section hidden.
(() => {
  const root = document.getElementById("recorder");
  if (!root || !window.MediaRecorder || !navigator.mediaDevices?.getUserMedia) return;
  const $ = (name) => root.querySelector(`[data-rec="${name}"]`);
  const form = root.querySelector("form");
  const fileInput = form.querySelector('input[type="file"]');
  const titleInput = form.querySelector('input[name="recording_title"]');
  const status = $("status");
  const time = $("time");
  const preview = $("preview");
  const MAX_S = 30 * 60;
  // Formats the server accepts, in order of preference (Safari records MP4).
  const FORMATS = [
    ["audio/ogg;codecs=opus", ".ogg"],
    ["audio/webm;codecs=opus", ".webm"],
    ["audio/mp4", ".m4a"],
  ];
  const format = FORMATS.find(([type]) => MediaRecorder.isTypeSupported(type));
  if (!format) return;
  root.hidden = false;

  let recorder = null;
  let chunks = [];
  let started = 0;
  let timer = 0;
  let previewUrl = "";

  const show = (name, visible) => { $(name).hidden = !visible; };
  const clock = () => {
    const s = Math.floor((Date.now() - started) / 1000);
    time.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    if (s >= MAX_S) recorder?.stop();
  };
  const reset = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = "";
    preview.removeAttribute("src");
    fileInput.value = "";
    form.hidden = true;
    show("start", true);
    show("time", false);
    status.textContent = "";
  };

  $("start").addEventListener("click", async () => {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      status.textContent = "Kein Zugriff auf das Mikrofon. Bitte im Browser erlauben.";
      return;
    }
    reset();
    chunks = [];
    recorder = new MediaRecorder(stream, { mimeType: format[0] });
    recorder.addEventListener("dataavailable", (e) => { if (e.data.size) chunks.push(e.data); });
    recorder.addEventListener("stop", () => {
      clearInterval(timer);
      stream.getTracks().forEach((t) => t.stop());
      show("stop", false);
      show("start", true);
      const blob = new Blob(chunks, { type: format[0].split(";")[0] });
      previewUrl = URL.createObjectURL(blob);
      preview.src = previewUrl;
      form.hidden = false;
      form.dataset.blobType = blob.type;
      root.recording = blob;
      status.textContent = "Anhören, Titel eingeben und hinzufügen.";
      titleInput.focus();
    });
    recorder.start(1000);
    started = Date.now();
    timer = setInterval(clock, 500);
    clock();
    show("start", false);
    show("stop", true);
    show("time", true);
    status.textContent = "Aufnahme läuft …";
  });
  $("stop").addEventListener("click", () => recorder?.stop());
  $("discard").addEventListener("click", reset);

  // Capture phase: the file is in the form before app.js sends it.
  form.addEventListener("submit", (event) => {
    const blob = root.recording;
    const title = titleInput.value.trim();
    if (!blob || !title) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    // The server takes the track title from the file name (without the extension).
    const clean = title.replace(/[\\/:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim();
    const name = (clean || "Aufnahme").slice(0, 120) + format[1];
    const files = new DataTransfer();
    files.items.add(new File([blob], name, { type: blob.type }));
    fileInput.files = files.files;
  }, true);
})();
