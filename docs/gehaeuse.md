# Gehäuse: Box gestalten, drucken, anfragen

Das Gehäuse der Myboxi kommt aus dem 3D-Drucker. Unter **app.myboxi.eu/gestalten** wählt man:
- eine Form: Radio, Würfel, Bär, Einhorn, Katze, Hase, Frosch
- einen Namen und drei Farben
- das Lautsprechergitter

Danach sieht man die Box in 3D und lädt die Druckdateien herunter. Der Generator steckt in `hardware/case` (Paket `myboxi-case`). Er läuft auch ohne Server:

```bash
uv run myboxi-case build form=unicorn name=Emma color_front=flieder --out emma.zip
uv run myboxi-case render form=bear name=Mia --out mia.png
uv run myboxi-case check --all
```

Lizenzen:
- Druckdateien: CC BY-SA 4.0
- Generator: GPL-3.0-or-later

## Teile

| Teil | Druckrichtung | Hinweis |
|---|---|---|
| Korpus | Oberseite auf dem Bett | Deckel, Seiten, Rückwand, Rahmen für den NFC-Leser, Schienen für die Front |
| Front | Sichtseite auf dem Bett | Lautsprechergitter, Name, bei Tierfiguren das Gesicht |
| Boden | liegend | Abstandshalter für den Pi, Halter für den Verstärker, zwei Kabelbinder-Halter, bei Bedarf Powerbank-Fach mit Schlitzen für ein Klettband |
| Lautsprecherring | liegend | klemmt den Lautsprecher an die Front |
| Ohren, Augen, Horn | liegend, Horn stehend | nur bei Tierfiguren |
| Schnauze | auf dem flachen Rücken | nur bei Tierfiguren: plastisch, mit Nase, Mund und Tonlöchern; wird in die Vertiefung der Front geklebt |
| Figurensockel | liegend | Druckpause bei 2,2 mm, NFC-Tag einlegen |

Alles druckt ohne Stützmaterial. Jedes Teil passt auf 180 × 180 mm, also auch auf kleine Drucker.

**Tierboxen mit Schnauze:** Bär, Katze, Hase, Einhorn und Frosch bekommen eine plastische Schnauze im weichen Stil der Figuren (`hardware/case/src/myboxi_case/faces.py`), mit Nase und Mund in der Akzentfarbe und einer eigenen Farbe (Kopf 4, je Tier vorgeschlagen). Sie liegt beim Druck auf ihrem flachen Rücken und hängt nirgends über.
- Hinter ihr hat die Front eine große Öffnung über dem Lautsprecher, ringsum bleibt ein Rand zum Kleben. Die Schnauze hat eigene Tonlöcher, die entlang der Oberfläche geneigt sind (höchstens 35°), damit an der Wölbung keine Messerkanten entstehen. Sie dämpft den Klang etwas.
- Sie wird in eine 0,6 mm tiefe Vertiefung der Front geklebt (2K-Kleber oder Sekundenkleber-Gel) und ist so groß, dass sie nicht als Kleinteil gilt (passt nicht in den Kleinteile-Zylinder nach EN 71-1).
- Augen, Schnurrhaare und Wimpern bleiben als Einlage auf der Front.

**Look:** Der Korpus ist ein Rahmen in der Gehäusefarbe mit stark gerundeten Kanten; oben, links, rechts und hinten sind farbige Felder in der Frontfarbe eingelassen (0,6 mm, Kopf 2), wie Füllungen in einem Holzrahmen. Vorn sitzt ein großes rundes Gitter in der Akzentfarbe: vor dem Lautsprecher gehen die Löcher durch, darüber hinaus setzt sich das Muster als flache Mulden fort (dahinter liegen Sitzring und Schraubdome). In den oberen Ecken der Front stehen Noten, wo sie Gitter und Namen nicht berühren. Oben liegt ein breiter Ring in der Akzentfarbe um den Figurenplatz. Vorgeschlagen für den Würfel: Rahmen Sand, Felder Rot, Akzente Weiß.

Die Oberkante des Korpus ist mit 10 mm gerundet; der Innenraum folgt der Rundung, damit die Wand überall 2,4 mm stark bleibt. Weil der Korpus kopfüber gedruckt wird, läuft die Rundung zur Oberseite hin in 45° aus; so hängt am Druckbett nichts über. Das Lautsprechergitter „Punkte“ hat runde Löcher auf Ringen wie ein klassischer Lautsprecher; Ohren und Augen sind auf der Rückseite gerundet.

## Drucken

**Allgemein**
- PETG, 0,2 mm Schichthöhe, 4 Wände, 15 % Füllung.
- PLA geht mit dem Pi Zero 2 W auch. Der Pi 4 wird warm, dafür PETG nehmen.

**Snapmaker U1:** Im ZIP liegt unter `snapmaker-u1/` ein fertiges Projekt für Snapmaker Orca. Es enthält die gewählten Farben je Kopf und verweist auf die Systemprofile „Snapmaker U1 (0.4 nozzle)“, „0.20mm Standard“ und „Snapmaker PLA Basic“. Orca nimmt dabei die Profile, die auf dem Rechner installiert sind; aus der Datei kommen nur die Farben. Filamente bei Bedarf an die eingelegten anpassen.
- Warum ein eigenes Projekt: Orca übernimmt Filamentfarben nur aus einer vollständigen Projektkonfiguration. Die Farben im 3MF selbst ordnen nur die Köpfe zu.
- Die neutrale 3MF im Hauptordner ist für alle anderen Drucker und Slicer gedacht; dort die Farben im Slicer selbst wählen. Welche Farbe auf welchen Kopf gehört, steht in der `LIESMICH.txt`.
- Die Vorlage (`hardware/case/src/myboxi_case/profiles/snapmaker-u1-project.json`) ist mit Snapmaker Orca 2.4.0 exportiert (`--export-3mf`) und steht wie dessen Profile unter AGPL-3.0.

**Mehrfarbig (Snapmaker U1, Orca, Bambu Studio)**
- Die 3MF-Dateien ordnen jedes Teil einem Kopf zu:

  | Kopf | Teile |
  |---|---|
  | 1 | Korpus, Boden, Ohren |
  | 2 | Front, Lautsprecherring |
  | 3 | Einlagen (Name, Tastensymbole, Figurenring, Gesicht), Horn und Figurensockel |

- Die Teile sind für eine 270-mm-Platte angeordnet. Die Ecke links hinten bleibt frei für den Reinigungsturm des U1.
- Passt nicht alles auf eine Platte, gibt es mehrere Dateien (`…-platte-1.3mf`, `…-platte-2.3mf`), etwa beim Radio.

**Einfarbig:** Im Konfigurator „Druck: Einfarbig“ wählen. Name und Symbole sind dann nur eingraviert. In einer mehrfarbigen Datei auf einem Einzeldüsen-Drucker die Teile „… Einlage“ löschen.

Die ZIP-Datei enthält eine `LIESMICH.txt` mit Stückliste und Montage.

## Stückliste

| Teil | Hinweis |
|---|---|
| Raspberry Pi Zero 2 W oder Pi 4 | Zero 2 W mit angelöteter Stiftleiste |
| NFC-Modul PN532 V3 | 42,7 × 40,4 mm, auf I2C gestellt |
| Verstärker MAX98357A | klemmt im Halter auf dem Boden, Platine bis 20 mm breit |
| Lautsprecher 40 mm, 3 W, 4 Ω | Radio: auch 50 oder 57 mm |
| 4 Taster 16 mm | Radio: auch 24-mm-Arcade-Taster |
| USB-C-Einbaubuchse mit Kabel | Ausschnitt 13 × 7 mm, Schrauben im Abstand von 24 mm |
| Schrauben | 4 × M3 × 10 (Boden), 3 × M2,5 × 6 (Lautsprecherring), 4 × M2,5 × 6 (Pi); Einhorn: 1 × M3 × 10 für das Horn |
| NFC-Tags | NTAG213 oder NTAG215, 25 mm, für die Figurensockel |

Verkabelung: `docs/hardware.md`.

## Maße vor dem Druck prüfen

Diese Teile gibt es in vielen Varianten. Der Generator geht von den folgenden Maßen aus (`hardware/case/src/myboxi_case/components.py`):

| Teil | Annahme |
|---|---|
| Taster 16 mm | Loch 16,2 mm, Mutter und Schlüssel brauchen Ø 24 mm, Körper 30 mm tief |
| USB-C-Buchse | Stecker bis 12,4 × 6,6 mm, Schrauben ± 12 mm von der Mitte |
| Lautsprecher | Rand 3 mm dick, Korb bis 22 mm tief |
| Powerbank | 93 × 61 × 23 mm |

**Spiel für Passungen** (Vorgabe 0,2 mm) passt für die meisten Drucker. Sitzt die Front zu stramm in den Schienen, 0,25 oder 0,3 wählen.

## Zusammenbau

Alles wird geschraubt oder gesteckt, nichts geklebt (außer Ohren und Augen der Tierfiguren).

1. Lautsprecher in die Front legen, Lautsprecherring aufschrauben.
2. NFC-Modul von unten in den Rahmen unter der Figurenmarke drücken. Die Lippen rasten ein.
3. Taster oben einsetzen und verschrauben, USB-C-Buchse hinten einschrauben.
4. Tierfiguren:
   - Ohren oder Augen in die Schlitze kleben.
   - Beim Einhorn das Horn von innen mit M3 × 10 festschrauben.
5. Pi auf den Boden schrauben. Den Verstärker von oben in seinen Halter schieben: Die Platine läuft in zwei Schlitzen und klemmt.
6. Verkabeln (`docs/hardware.md`). Die Kabel mit kleinen Kabelbindern (bis 3,6 mm) an den beiden Haltern auf dem Boden bündeln, damit die Front beim Einschieben nichts einklemmt. Mit Powerbank: diese mit einem Klettband (bis 20 mm) durch die zwei Schlitze im Boden festzurren.
7. Front von unten in die Schienen hinter dem Fenster schieben.
8. Boden einsetzen und mit 4 Schrauben M3 festschrauben. Die Front lässt sich nur bei offenem Boden herausnehmen; Kinder kommen nicht an die Elektronik.

**Ohne Löten**
- Raspberry Pi Zero 2 W mit vorgelöteter Stiftleiste („Zero 2 WH“).
- Taster mit Anschlusslitzen.
- Dupont-Kabel Buchse/Buchse, 20 cm.
- PN532-Module gibt es mit eingelöteter Stiftleiste; oft liegt sie aber lose bei.
- Der Lautsprecher kommt an die Schraubklemme des Verstärkers.

## Automatische Prüfungen

`myboxi-case check --all` baut jede erlaubte Kombination und prüft sie. Die Tests in CI tun dasselbe.

**Druckbarkeit**
- Jedes Teil ist ein geschlossener Körper und liegt flach auf dem Bett.
- Keine Stelle braucht Stützmaterial: Überhänge sind höchstens 44° steil, Brücken höchstens 16 mm lang.
- Keine Wand ist dünner als 0,8 mm.

**Bauteile**
- Jedes Bauteil hat mindestens 0,5 mm Abstand zu Teilen, an denen es nicht befestigt ist, und steckt nirgends im Kunststoff.
- Der Weg für den USB-C-Stecker ist frei.
- Das NFC-Feld unter der Figur ist frei von Metall und Lautsprecher; die Antenne liegt höchstens 4 mm unter der Oberfläche.

**Lautsprechergitter**
- Mindestens 30 % der Fläche sind offen.
- Kein Loch ist breiter als 5 mm, damit Finger und Stifte draußen bleiben.

Die 3MF-Dateien wurden mit Snapmaker Orca 2.4.0 und dem U1-Profil (0,20 mm Standard, PETG) gesliced: ohne Fehler, mit den Köpfen wie oben.

**Nicht automatisch prüfbar:** der echte Druck. Beim ersten Testdruck prüfen:
- Front in den Schienen
- Taster, USB-C-Buchse und Lautsprecher
- Leseweite des NFC-Lesers durch den Deckel mit Figur und Sockel
- Klang durch das Gitter

## Figurensockel

Unter **app.myboxi.eu/gestalten/figur** („Figur gestalten“) entsteht ein Sockel mit eingebautem NFC-Chip. Er macht jede Figur zur Box-Figur.

**Auswahl:**
- Form: rund, eckig, Herz oder Stern. Spitzen und Ecken sind abgerundet.
- Größe: 40 oder 50 mm.
- Oberseite:
  - **3D-Figur** aus unserer Sammlung, wie kleine Spielfiguren: Bär in roter Jacke mit Stern, Katze in blauer Jacke und Hase im rosa Kleid mit Herz, Frosch in gelber Weste, Einhorn mit Stern, Kind im roten Shirt mit Herz. Sie stehen auf zwei Schuhen, etwa 5 cm hoch (beim 50-mm-Sockel etwas größer).
    - Weich modelliert: Kopf, Körper, Arme und Beine gehen ineinander über wie bei einer geformten Figur (Abstandsfelder mit weichen Übergängen, `hardware/case/src/myboxi_case/figures3d.py`).
    - Aufrecht und in einem Stück mit dem Sockel gedruckt, ohne Stützen und ohne Kleben. Unter allem, was steiler als 45° überhängt, sitzt eine weiche Kehle; was die Figur hält, lehnt am Bauch, die Schnauze ruht auf der Brust. Die Tests prüfen, dass keine Schicht in der Luft beginnt.
    - Vierfarbig auf dem Snapmaker U1: Kopf 1 Sockel und Helles (Schnauze, Stern oder Herz), Kopf 2 Kleidung, Kopf 3 Figur, Kopf 4 Gesicht, Schuhe und Name. Die Kleidung wird je Figur vorgeschlagen.
    - Einfarbig ist das Gesicht eingraviert.
    - Wird es mit Namen zu eng (die Spitze des Sterns), wird die Figur bis zu 14 % kleiner.
- Die Sockel sind standardmäßig weiß, wie bei Spielfiguren auf der Box; mit Figur ist der Name dunkel wie das Gesicht.
  - **Flach**, um eine vorhandene Figur daraufzukleben, etwa ein Spieltier. Am besten mit 2K-Kleber oder Sekundenkleber-Gel.
  - **Noppen** für Klemmbausteine: Eine Minifigur oder etwas Gebautes steckt direkt darauf. Die Noppen sitzen im üblichen 8-mm-Raster, als rechteckiger Block. Wie fest sie halten, regelt „Spiel für die Noppen“: weniger Spiel heißt dickere Noppen und festeren Halt.
  - **Aufsteller**: dieselben Figuren flach, etwa 5 cm hoch und 6 mm dick, oder eine eigene Zeichnung.
    - Die Figur wird flach neben dem Sockel gedruckt, mit dem Gesicht nach oben; so wird die Vorderseite sauber und mehrfarbig.
    - Danach steckt sie mit einem Steg in einem Schlitz des Sockels und wird dort festgeklebt.
    - Zweifarbig druckt der Snapmaker U1 alles in einem Durchgang: Kopf 1 Sockel, Kopf 2 Name, Kopf 3 Figur, Kopf 4 Gesicht.
  - **Eigene Zeichnung** (als Aufsteller): Das Kind malt eine Figur, ihr ladet ein Foto davon hoch (JPEG, PNG oder WebP, bis 16 MB).
    - Am besten mit dunklem Stift auf hellem Papier malen und gerade von oben fotografieren. Das Blatt darf am Rand angeschnitten sein; Tisch und Blattkante werden ignoriert.
    - Der Server erkennt die Striche, füllt geschlossene Flächen und legt einen 2,5-mm-Rand um alles, wie bei einem ausgeschnittenen Aufkleber. Dünne Linien werden so druckbar, Spitzen rund. Die Striche selbst kommen als zweite Farbe auf die Vorderseite.
    - Liegt das Foto quer, lässt sich die Zeichnung in 90°-Schritten drehen.
    - Sind mehrere Dinge getrennt gemalt (eine Sonne neben dem Haus), wird nur das größte zur Figur, außer sie liegen nah genug beieinander.
    - Das Foto wird nicht gespeichert, nur die erkannten Striche, 7 Tage lang. Danach muss man das Foto neu hochladen, um den Link wieder zu öffnen.
- Name vorn auf dem Sockel, bis 10 Zeichen. Zweifarbig als Einlage, einfarbig graviert.
- NFC-Chip: Münze oder Aufkleber, NTAG213 oder NTAG215, Ø 25 mm.

**Drucken:**
- Der Sockel liegt richtig herum auf der Platte und braucht keine Stützen.
- Der Chip liegt 0,8 mm über der Unterseite, also nah am Leser der Box.
- Seine Tasche ist rundum geschlossen. Die 3MF enthält deshalb eine **Druckpause** genau über der Tasche, bei der Münze 2,2 mm.
  - Hält der Drucker an, den Chip flach einlegen und fortsetzen.
  - Orca Slicer, Snapmaker Orca und Bambu Studio übernehmen die Pause aus der Datei. Geprüft mit dem Profil des Snapmaker U1: Die Pause kommt vor der ersten Schicht über der Tasche, ein- und zweifarbig.
  - Mit den STL-Dateien setzt man die Pause im Slicer selbst.

**Danach:** Den Sockel auf die Box legen. Die Figur erscheint in der App unter „Figuren → Unbekannte Figuren“ und lässt sich dort übernehmen. Wer die UID schon vorher braucht, scannt den Chip vor dem Einlegen in der App unter „Figuren“ mit dem Handy (Android mit Chrome).

**Sicherheit:**
- Jeder Sockel ist größer als der Kleinteile-Zylinder nach EN 71-1 (Ø 31,7 mm).
- Der Chip ist ganz eingeschlossen; Magnete gibt es keine.
- Eine aufgeklebte Figur muss fest sitzen und selbst für das Alter des Kindes geeignet sein.

Von der Kommandozeile: `uv run myboxi-case figure shape=heart top=bricks name=Mia --out mia.zip` (oder `--out mia.png`).

## Anfragen für gedruckte Gehäuse

Wer keinen Drucker hat, kann ein gedrucktes Gehäuse anfragen (ohne Elektronik). Die Funktion ist aus, bis `MYBOXI_SERVER_ORDER_NOTIFY_EMAIL` gesetzt ist (`docs/betrieb-debian13.md`).

**Ablauf**
1. Die anfragende Person füllt das Formular aus: Name, E-Mail, Land, Anzahl, Nachricht, Einwilligung.
2. Sie bekommt eine Mail mit einem Bestätigungslink, 48 Stunden gültig. Der Link zeigt nur einen Knopf; erst der Klick bestätigt, damit Mail-Scanner nichts auslösen.
3. Nach der Bestätigung gehen zwei Mails raus:
   - an den Betreiber: alle Angaben, Link zur Gestaltung und zu den Druckdateien; „Antworten“ geht direkt an die anfragende Person
   - an die anfragende Person: Eingangsbestätigung
4. Der Betreiber schickt selbst ein Angebot. Erst dessen Annahme ist eine Bestellung.

**Verwaltung:** `myboxi-server case-requests list [--status confirmed]`, `show <id>`, `status <id> answered|done|cancelled`, `delete <id>`.

**Aufbewahrung:** Unbestätigte Anfragen werden nach 48 Stunden gelöscht, alle anderen 12 Monate nach der letzten Änderung. Details in der Datenschutzerklärung von myboxi.eu.

**Missbrauchsschutz**
- Unsichtbares Feld gegen Bots.
- Höchstens 5 Anfragen pro Stunde und IP-Adresse, 3 pro Tag und E-Mail-Adresse.

**Vor dem ersten Verkauf klären** (nicht Teil der Software):
- Produktsicherheit eines Gehäuses für ein Kinderprodukt (Kleinteile, Material).
- Angebot und AGB: Bei personalisierten Waren entfällt das Rücktrittsrecht (FAGG § 18).
- Umsatzsteuer, Versand.
