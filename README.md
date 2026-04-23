# Twitch Viral Clipper — MVP Étage 1

Pipeline de détection temps réel de moments viraux sur un stream Twitch.
Version MVP : **Watcher + Detector (3 signaux) + Clipper**. Pas encore d'édition ni d'upload.

## Architecture (Étage 1 seul)

```
        ┌─────────────┐
Twitch ─┤ VideoCapture │──► buffer/<channel>/seg_*.ts  (chunks 10s, fenêtre 2min)
        │ (streamlink  │
        │  + ffmpeg)   │
        └─────────────┘
        ┌─────────────┐
Twitch ─┤ AudioMonitor │──► loudness en RAM (ebur128)
        │ (streamlink  │
        │  + ebur128)  │
        └─────────────┘
        ┌─────────────┐     ┌──────────────────────┐
Chat  ──┤   ChatBot   ├────►│ 3 Trackers           │
        │ (twitchio)  │     │  - velocity          │     ┌─────────┐
        └─────────────┘     │  - emote density     ├────►│ Scorer  │──► Candidate
                            │  - audio snapshot    │     │ + cool- │
                            └──────────────────────┘     │  down   │       │
                                                         └─────────┘       ▼
                                                                   ┌──────────────┐
                                                                   │   Clipper    │
                                                                   │  (ffmpeg     │
                                                                   │   concat)    │
                                                                   └──────────────┘
                                                                          │
                                                                          ▼
                                                              data/clips/*.mp4 + SQLite
```

## Prérequis

### Outils système

**Linux / macOS**
```bash
sudo apt install ffmpeg      # ou brew install ffmpeg
pip install streamlink
```

**Windows** (applicable à ton poste PwC)
```powershell
winget install Gyan.FFmpeg
winget install streamlink.streamlink
# verifier que les 2 sont dans le PATH (redemarrer le terminal si besoin)
ffmpeg -version
streamlink --version
```

### Python et dépendances

Python 3.12+ requis.

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -e .
# pour les tests
pip install -e ".[dev]"
```

### Tokens Twitch

1. Créer une app sur https://dev.twitch.tv/console/apps
   - Redirect URL : `http://localhost` (non utilisé pour le MVP mais requis)
   - Récupérer `CLIENT_ID` et `CLIENT_SECRET` → dans `.env`

2. Token OAuth chat (IRC)
   - Générer sur https://twitchtokengenerator.com/ avec scope `chat:read`
   - Format attendu : `oauth:xxxxxxxxxxxxx`
   - **Recommandé** : créer un compte Twitch dédié au bot (pas ton compte perso)

Copier `.env.example` vers `.env` et remplir.

## Lancement

```bash
python -m src.main watch --streamer <login_twitch>
# ex:
python -m src.main watch --streamer zerator --log-level DEBUG
```

Le pipeline :
1. Se connecte au chat IRC
2. Lance deux streamlinks (video pour le buffer, audio pour l'ebur128)
3. Attend 20-30s que la baseline chat + audio se stabilise
4. À chaque candidat détecté, écrit un `.mp4` dans `data/clips/` et l'enregistre en SQLite

**Arrêt** : `Ctrl+C`.

## Tuning des seuils

Tout est dans `config/settings.yaml`. Les valeurs par défaut sont conservatrices.

Pour un **gros streamer** (xQc, Kai Cenat) : monter `chat_velocity_threshold` à 20-40 et `velocity_multiplier` à 2.5. Sinon tu vas clipper en continu.

Pour un **petit streamer** (< 500 viewers) : baisser `chat_velocity_threshold` à 2.0. La baseline plancher (0.2 msg/s dans le code) évite les divisions par zéro.

Overrides par streamer possibles dans `config/streamers/<login>.yaml`.

## Structure du projet

```
src/
├── main.py                    # CLI
├── core/
│   ├── config.py              # env + YAML
│   ├── db.py                  # SQLite async
│   ├── events.py              # dataclasses
│   └── logging.py
├── watcher/
│   ├── chat_capture.py        # IRC via twitchio
│   ├── video_capture.py       # streamlink -> ffmpeg segment
│   └── audio_capture.py       # streamlink -> ffmpeg ebur128
├── detector/
│   ├── chat_velocity.py       # signal 1
│   ├── emote_spam.py          # signal 2
│   └── scorer.py              # fusion + cooldown
├── clipper/
│   └── extractor.py           # ffmpeg concat + cut
└── orchestrator/
    └── pipeline.py            # colle tout
```

## Tests

```bash
pytest tests/ -v
```

12 tests unitaires, pas de réseau requis. Couvre les 3 signaux et la logique de fusion/cooldown.

## Limitations connues (phase 2 à venir)

- Pas d'édition vidéo (9:16, sous-titres, recadrage webcam)
- Pas d'appel Claude pour classification post-clip
- Pas d'upload TikTok
- Détection d'emotes par regex textuelle (ne capte pas les emotes natives Twitch transmises comme bitmaps IRC — suffit pour KEKW/OMEGALUL/etc qui arrivent comme du texte)
- Windows : `add_signal_handler` non supporté sur ProactorEventLoop → Ctrl+C fonctionne mais via KeyboardInterrupt

## Prochaines étapes

1. Lancer sur un streamer connu pendant 1h, observer les logs DEBUG
2. Tuner `chat_velocity_threshold` et `min_viral_score` selon le streamer
3. Phase 2 : Editor (Whisper + ffmpeg 9:16 + sous-titres) + Claude classifier
4. Phase 3 : Publisher TikTok (ou notification Telegram en fallback)
