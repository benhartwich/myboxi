# Sicherheit

Myboxi steht in Kinderzimmern. Sicherheitslücken bitte **nicht** als öffentliches Issue melden.

## Melden

- E-Mail an **support@webinx.eu** mit dem Betreff „Myboxi Sicherheit“.
- Bitte angeben: betroffener Teil (Box-Agent, Server, Image, Gehäuse-Generator), Version oder Commit, Schritte zum Nachvollziehen und die mögliche Auswirkung.
- Wir bestätigen den Eingang innerhalb von 7 Tagen und melden uns mit einer Einschätzung und einem Zeitplan für die Behebung.
- Bitte gib uns 90 Tage, bevor du Details veröffentlichst. Wir nennen dich gern in den Release-Notizen, wenn du das möchtest.

## Unterstützte Versionen

| Teil | Unterstützt |
|---|---|
| Box-Agent | die neueste Version im Update-Kanal `channel-stable`; Boxen aktualisieren sich selbst (`docs/updates.md`) |
| Server | der aktuelle Stand von `main` |
| Image | das neueste Release `box-v*` |

## Im Fokus

Besonders interessieren uns:

- der Kanal zwischen Box und Server: Kopplung, Geräte-API, MQTT (SPEC §6, §7, §10)
- die signierten Box-Updates (SPEC §11.1)
- der Einrichtungsmodus der Box (SPEC §9.3)
- Mandanten-Isolation im Server: Ein Haushalt darf nie Daten eines anderen sehen
- Lautstärke- und Ruhezeit-Grenzen, die sich von außen aushebeln lassen (SPEC §9.2)

Nicht im Fokus: physischer Zugriff auf eine Box (SD-Karte), Angriffe, die schon Root auf dem Server voraussetzen, und Denial of Service durch reine Last.
