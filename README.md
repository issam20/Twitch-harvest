# Twitch Harvest

Pipeline de détection temps réel de moments viraux sur un live Twitch.
Surveille le chat en continu, détecte les pics d'activité via 5 signaux indépendants, crée automatiquement des clips via l'API Helix et les télécharge en MP4.

## Architecture

```
Chat IRC (twitchio)
        |
        v
+-------------------------------+
|         5 Détecteurs          |
|  1. Velocity  (Z-score adapt) |
|  2. Emote density             |
|  3. Unique chatters           |
|  4. Caps ratio                |
|  5. Repetition / copypasta    |
+---------------+---------------+
                |  score 0-100 par signal
                v
        +-------------+
        | ViralScorer |  gate 1 : velocity > 0
        |             |  gate 2 : >= 1 autre signal
        |             |  gate 3 : score composite >= min_viral_score
        |             |  cooldown 120s
        +------+------+
               |
               v
    POST /helix/clips  (Twitch API)
               |
               v
    streamlink -> data/clips/raw/*.mp4
               |
               v
    SQLite  (sessions + clips + scores)
               |
               v
    Dashboard FastAPI (SSE live + historique)
```

## Signaux

| Signal | Déclenchement | Algorithme |
|--------|--------------|-----------|
| **Velocity** | Obligatoire | Z-score sur fenêtre glissante 5min — auto-calibré à la taille du chat |
| **Emote** | >= 1 requis | Ratio emotes/messages sur fenêtre 10s |
| **Unique chatters** | >= 1 requis | Ratio nouveaux chatters vs baseline 60s |
| **Caps** | >= 1 requis | Ratio messages en majuscules |
| **Répétition** | >= 1 requis | Ratio copypasta (messages quasi-identiques) |

Score composite = 50% velocity + 50% moyenne des signaux déclenchés.

## Prérequis

**Python 3.12+** et **streamlink** :

```bash
# Windows
winget install streamlink.streamlink

# Linux / macOS
pip install streamlink
```

```bash
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\Activate.ps1     # Windows

pip install -e .
```

## Configuration

Copier `.env.example` -> `.env` et renseigner :

```env
TWITCH_CLIENT_ID=...
TWITCH_CLIENT_SECRET=...
TWITCH_IRC_TOKEN=oauth:...    # token chat:read
TWITCH_IRC_NICK=...           # login du compte bot
TWITCH_USER_TOKEN=...         # token clips:edit (sans oauth:)
```

Générer `TWITCH_USER_TOKEN` via Device Flow intégré :

```bash
python -m src.main auth
```

### Notifications Telegram (optionnel)

Après chaque render réussi, le bot envoie automatiquement le MP4 + métadonnées.

**1. Créer le bot**

```
@BotFather sur Telegram → /newbot → copier le token
```

**2. Récupérer le `chat_id`**

Envoyer un message au bot, puis :

```bash
curl https://api.telegram.org/bot<TOKEN>/getUpdates
# → "chat": {"id": 123456789, ...}
```

**3. Renseigner le `.env`**

```env
TELEGRAM_BOT_TOKEN=123456789:AAF...
TELEGRAM_CHAT_ID=123456789
```

Si `TELEGRAM_BOT_TOKEN` est absent ou vide, les notifications sont silencieusement ignorées.
Fichiers > 50 MB : le bot envoie un message texte avec le chemin local à la place du fichier.

### Tuning (`config/settings.yaml`)

```yaml
detector:
  z_score_threshold: 2.5    # sigmas au-dessus de la moyenne pour déclencher
  min_viral_score: 60.0     # score composite minimum pour créer un clip
  cooldown_seconds: 120     # pause entre deux clips
  warmup_samples: 30        # ticks avant activation (~60s)
```

Surcharges par streamer dans `config/streamers/<login>.yaml`.

## Lancement

```bash
# Harvest seul
python -m src.main harvest --streamer <login>

# Avec dashboard web
python -m src.main harvest --streamer <login> --dashboard

# Options
--cooldown 90       # cooldown personnalisé
--port 8080         # port dashboard (défaut 8000)
--log-level DEBUG
```

## Dashboard

| Page | URL | Contenu |
|------|-----|---------|
| Live | `http://localhost:8000/` | Graphiques velocity + signaux, stats session, tableau clips |
| Historique | `http://localhost:8000/sessions` | Tableau numérique triable par signal, toutes sessions |

Fonctionnalités live :
- Velocity msg/s vs baseline (moyenne mobile 5min)
- Scores des 5 signaux + composite (0-100)
- Z-score coloré dans le header (neutre → orange → rouge)
- Tableau des clips triable par colonne (Score/V/E/U/C/R)
- Lignes verticales sur les graphiques à chaque clip créé
- Barre de warmup et cooldown en temps réel
- Flash rouge sur le body à chaque nouveau clip

## Structure

```
src/
├── main.py                    # CLI (typer)
├── core/
│   ├── config.py              # env + YAML (pydantic-settings)
│   ├── db.py                  # SQLite async (aiosqlite) — sessions/clips
│   ├── events.py              # dataclasses (ChatEvent, TwitchClip...)
│   └── logging.py
├── api/
│   ├── twitch.py              # client Helix (httpx async)
│   └── dashboard.py           # FastAPI + SSE + HTML embarqué
├── watcher/
│   └── chat_capture.py        # IRC twitchio
├── detector/
│   ├── chat_velocity.py       # Z-score adaptatif
│   ├── emote_spam.py          # densité d'emotes
│   ├── chat_signals.py        # caps, unique chatters, répétition
│   └── scorer.py              # fusion + gates + cooldown
└── orchestrator/
    └── harvest.py             # pipeline principal
```

## Tests

```bash
pytest tests/ -v
```

## Limitations connues

- Emotes FFZ/BTTV non détectées — seules les emotes listées dans `config/settings.yaml` sont reconnues
- Windows : `add_signal_handler` non supporté sur ProactorEventLoop — Ctrl+C fonctionne via KeyboardInterrupt
- streamlink doit être installé séparément (non inclus dans `pyproject.toml`)

## Roadmap

- **Phase 2B** : recadrage 9:16 (ffmpeg), sous-titres (Whisper)
- **Phase 3** : classification Claude (catégorie, viralité prédite)
- **Phase 4** : upload TikTok / notification Telegram
