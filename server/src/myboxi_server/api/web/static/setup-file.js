// Builds myboxi-setup.json in the browser (SPEC v0.14 §9.7): the Wi-Fi password and SSH keys
// never reach the server. The page provides server address and one-time token.
const root = document.getElementById("setup-file");
const error = document.getElementById("setup-error");
const value = (id) => document.getElementById(id).value;

function fail(message) {
  error.textContent = message;
  error.hidden = false;
}

function build() {
  const setup = JSON.parse(root.dataset.setup);
  const ssid = value("wifi-ssid").trim();
  const password = value("wifi-password");
  if (ssid) {
    if (new TextEncoder().encode(ssid).length > 32) return fail("Der WLAN-Name ist zu lang (höchstens 32 Zeichen).");
    if (password && (password.length < 8 || password.length > 63)) {
      return fail("Das WLAN-Passwort hat 8 bis 63 Zeichen. Für ein offenes WLAN leer lassen.");
    }
    setup.wifi = password ? { ssid, password } : { ssid };
  } else if (password) {
    return fail("Bitte auch den WLAN-Namen eintragen.");
  }
  setup.wifi_country = value("wifi-country");
  const keys = value("ssh-keys").split(/\r?\n/).map((k) => k.trim()).filter(Boolean);
  if (keys.some((k) => !/^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com|ssh-rsa) [A-Za-z0-9+/=]{40,}/.test(k))) {
    return fail("Das sieht nicht wie ein öffentlicher SSH-Schlüssel aus (z. B. „ssh-ed25519 AAAA…“).");
  }
  if (keys.length > 5) return fail("Höchstens 5 SSH-Schlüssel.");
  if (keys.length) setup.ssh_authorized_keys = keys;
  return setup;
}

document.getElementById("download-setup").addEventListener("click", () => {
  error.hidden = true;
  const setup = build();
  if (!setup) return;
  const blob = new Blob([JSON.stringify(setup, null, 2) + "\n"], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "myboxi-setup.json";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
});
