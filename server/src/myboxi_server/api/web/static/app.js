// Small helpers for every page; the app works without them (plain forms).

// Forms with data-confirm ask before they submit: deleting a figure or a content by a
// slip of the finger is not undone. Runs in the capture phase, before htmx sees the submit.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.dataset.confirm) return;
  if (!window.confirm(form.dataset.confirm)) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}, true);

// Forms with data-upload send their files with a progress bar: large audio files take
// minutes on a phone and the page would look frozen. The server answers as for a plain
// form, a redirect on success or the page with the error.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !("upload" in form.dataset) || event.defaultPrevented) return;
  event.preventDefault();
  const button = form.querySelector("button[type=submit], button:not([type])");
  const bar = document.createElement("progress");
  bar.max = 100;
  bar.value = 0;
  const label = document.createElement("p");
  label.className = "muted";
  label.setAttribute("role", "status");
  label.textContent = "Wird hochgeladen …";
  form.append(bar, label);
  if (button) button.disabled = true;

  const xhr = new XMLHttpRequest();
  xhr.open(form.method || "post", form.action);
  xhr.upload.addEventListener("progress", (e) => {
    if (!e.lengthComputable) return;
    const percent = Math.round((e.loaded / e.total) * 100);
    bar.value = percent;
    label.textContent = percent < 100 ? `Wird hochgeladen … ${percent} %` : "Wird verarbeitet …";
  });
  xhr.addEventListener("load", () => {
    if (xhr.status < 400 && xhr.responseURL && xhr.responseURL !== form.action) {
      window.location.assign(xhr.responseURL);
      return;
    }
    // An error page (for example a file too large): show it as a plain submit would.
    document.open();
    document.write(xhr.responseText);
    document.close();
  });
  xhr.addEventListener("error", () => {
    bar.remove();
    label.textContent = "Hochladen fehlgeschlagen. Bitte die Verbindung prüfen und noch einmal versuchen.";
    if (button) button.disabled = false;
  });
  xhr.send(new FormData(form));
});
