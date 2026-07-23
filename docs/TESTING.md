# SailLog testen — Anleitung für die Test-Crew 🧪⛵

Danke, dass du SailLog testest! Du brauchst **kein Python und keine
Programmierkenntnisse** — nur Windows und ein paar Minuten.

---

## 1. Programm herunterladen

Alle fertigen Versionen liegen unter **„Releases"** (rechte Spalte auf der
Projekt-Startseite, oder direkt unter `…/releases`). Nimm die **neueste**.

Es gibt zwei Varianten — beide brauchen **kein** Python:

| Download | Für wen | So geht's |
|---|---|---|
| **`SailLog-Setup-x.y.z.exe`** (Installer) | die meisten | Doppelklick → installiert, legt ein Startmenü-Symbol an. |
| **`SailLog-windows-x64.zip`** (portable) | „ohne Installieren" | ZIP entpacken → im Ordner **`SailLog.exe`** starten. |

### Windows-SmartScreen
Weil das Programm (noch) nicht kostenpflichtig signiert ist, zeigt Windows beim
ersten Start evtl. einen blauen Hinweis. Das ist normal:
**„Weitere Informationen" → „Trotzdem ausführen".**

---

## 2. Ausprobieren

- **Ohne Boot:** Du kannst die Oberfläche einfach erkunden — die Messwerte
  bleiben dann auf „—". Menüs, Eintrag erfassen, Bericht, Karte usw. lassen sich
  trotzdem anschauen.
- **Am Boot:** Unter **„Quellen…"** dein Gateway eintragen (Vorlagen für
  PredictWind, B&G/GoFree, Maretron, GPS-Maus sind dabei) → **„Verbinden"**.
- **Sprache:** oben **Extras → Sprache / Language** (Deutsch/English), wird beim
  nächsten Start übernommen.

Deine Daten bleiben **komplett lokal** auf deinem Laptop (unter
`C:\Users\<Du>\.saillog\`) — es gibt keinen Server, keine Cloud.

---

## 3. Fehler oder Ideen melden

**Am liebsten über GitHub** (dann sehe ich alles gebündelt):
„Issues" → „New issue" → **„🐞 Fehler melden"** und die kurzen Felder ausfüllen.

**Kein GitHub-Konto / zu umständlich?** Kein Problem — im gleichen
„New issue"-Menü steht ein Link **„Kein GitHub-Konto? Fehler per Formular
melden"** bzw. du schreibst einfach eine E-Mail.

### Was mir am meisten hilft
- Was hast du gemacht, was ist passiert (1–2 Sätze genügen)?
- Ein **Screenshot** (bei GitHub einfach ins Textfeld ziehen).
- Welche **Version** (Dateiname/Fenstertitel) und **Windows-Version**?
- Bei Verbindungsproblemen: der Knopf **„Rohdaten…"** zeigt, was ankommt — ein
  Foto davon ist Gold wert.

---

*Für Entwickler / eigenen Build siehe [`BUILD.md`](BUILD.md).*
