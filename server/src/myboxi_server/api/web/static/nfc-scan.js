// Web NFC (Chrome on Android): reads a chip's serial number into the UID field, so nobody
// types it. Other browsers (iPhone, desktop) keep the button hidden.
const button = document.getElementById("nfc-scan");
const status = document.getElementById("nfc-status");
const SCAN_MS = 30000;

function say(text) {
  status.textContent = text;
  status.hidden = false;
}

async function scan(input) {
  const controller = new AbortController();
  const reader = new NDEFReader();
  const read = new Promise((resolve) => {
    reader.addEventListener("reading", (event) => resolve(event.serialNumber || ""));
    reader.addEventListener("readingerror", () => resolve(null));
    setTimeout(() => resolve(undefined), SCAN_MS);
  });
  try {
    await reader.scan({ signal: controller.signal });
  } catch (error) {
    say(error.name === "NotAllowedError"
      ? "NFC ist für diese Seite nicht erlaubt. In Chrome unter Website-Einstellungen → NFC erlauben."
      : "NFC ist nicht verfügbar. Ist NFC am Handy eingeschaltet?");
    return;
  }
  say("Halte den Chip an die Rückseite des Handys …");
  const serial = await read;
  controller.abort();  // stop scanning
  if (serial === undefined) {
    say("Kein Chip gefunden. Bitte noch einmal versuchen.");
  } else if (!serial) {
    say("Der Chip ließ sich nicht lesen. Bitte noch einmal versuchen oder die UID von Hand eintragen.");
  } else {
    input.value = serial.replaceAll(":", "").toUpperCase();
    say("Gelesen.");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

if (button && "NDEFReader" in window) {
  const input = document.getElementById(button.dataset.target);
  button.hidden = false;
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await scan(input);
    } finally {
      button.disabled = false;
    }
  });
}
