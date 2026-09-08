# Migration des Immich-POC auf den Raspberry Pi

Diese Anleitung geht von einer vorhandenen, benutzerspezifischen Installation mit `pipx` und dem systemd-User-Dienst `photobooth-app.service` aus. Der POC lädt jedes neu angelegte Medium genau einmal in zwei Varianten hoch:

- das unveränderte Kameraoriginal
- das verarbeitete Ergebnis mit Filter und Overlay

Lokales Löschen oder späteres Bearbeiten verändert bereits hochgeladene Immich-Assets nicht. Fehlgeschlagene Uploads bleiben lokal vorgemerkt und werden während des Betriebs sowie nach einem Neustart erneut versucht.

## 1. Installation und Arbeitsdaten prüfen

Als derselbe Linux-Benutzer ausführen, unter dem Photobooth läuft:

```bash
pipx list
systemctl --user status photobooth-app.service
systemctl --user cat photobooth-app.service
```

Die Zeile `WorkingDirectory=` merken. Alle relativen Verzeichnisse wie `config`, `database` und `media` liegen darunter. In den folgenden Beispielen wird `/home/pi/photobooth` verwendet; den Pfad gegebenenfalls ersetzen.

## 2. Sicherung erstellen

```bash
systemctl --user stop photobooth-app.service
cd /home/pi/photobooth
tar -czf "$HOME/photobooth-backup-$(date +%Y%m%d-%H%M%S).tar.gz" config database media
```

## 3. Fork über pipx installieren

Die vorhandene pipx-Umgebung wird unter demselben Paketnamen ersetzt. Arbeitsdaten im `WorkingDirectory` werden dadurch nicht entfernt.

```bash
pipx install --force 'git+https://github.com/JanTristanH/photobooth-app.git@main'
pipx list
```

Für einen reproduzierbaren Produktivstand sollte `main` nach dem POC durch einen freigegebenen Commit-Hash oder Tag ersetzt werden.

## 4. Dienst starten und Datenbankmigration prüfen

```bash
systemctl --user daemon-reload
systemctl --user start photobooth-app.service
systemctl --user status photobooth-app.service
journalctl --user -u photobooth-app.service -n 100 --no-pager
```

Beim ersten Start legt Alembic automatisch die Tabelle `immich_assets` an. Ein Fehler an dieser Stelle muss vor der Konfiguration behoben werden.

## 5. Immich konfigurieren

Im Photobooth Admin Center unter **Configuration → Synchronizer and Share-Link Generation → Immich** folgende Werte setzen:

| Feld | Wert |
|---|---|
| Enabled | `true` |
| Server URL | `https://immich.heisecke.org` |
| API key | den Immich-Key `FotoBox` eintragen |
| Album ID | `d8108311-7ae9-4ee6-8390-797d6f399042` |
| Shared album slug | `fotobox` |
| Request timeout | `30` |

Der Key benötigt mindestens `asset.upload` und `albumAsset.create`. Für die Validierung des Zielalbums ist zusätzlich `album.read` sinnvoll. Der Key wird in der Admin-API maskiert, liegt aber für den Dienst lesbar in `config/plugin_synchronizer.json`; die Dateirechte dieses Verzeichnisses entsprechend schützen.

Nach dem Speichern die Dienste über das Admin Center neu laden oder aus der Shell neu starten:

```bash
systemctl --user restart photobooth-app.service
journalctl --user -u photobooth-app.service -f
```

## 6. Funktionstest

1. Ein neues Foto aufnehmen.
2. Im Log zwei erfolgreiche Uploads kontrollieren: `original` und `processed`.
3. Das Album `Fotobox Test` öffnen und beide Assets prüfen.
4. In der Photobooth-Bildansicht auf **QR Code** tippen.
5. Den QR-Code mit einem Mobiltelefon scannen. Er muss direkt auf das verarbeitete Asset unter `https://immich.heisecke.org/s/fotobox/photos/<asset-id>` führen.

Bereits vor der Migration vorhandene Medien werden nicht nachträglich hochgeladen. Der POC verarbeitet nur neue `collection_files_added`-Ereignisse.

## Rollback

Den Dienst stoppen, die gewünschte veröffentlichte Version wieder über pipx installieren und die Sicherung nur bei Bedarf zurückspielen:

```bash
systemctl --user stop photobooth-app.service
pipx install --force 'photobooth-app==9.0.0'
systemctl --user start photobooth-app.service
```

Die zusätzliche Tabelle `immich_assets` stört die ältere Anwendung nicht. Eine Rücksicherung der Datenbank ist daher normalerweise nicht erforderlich.
