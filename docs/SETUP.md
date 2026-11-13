# Setup guide

Time needed: about 10 minutes. You need a computer with 4 GB RAM or more and an internet connection.

## 1. Install the three tools

**Python 3.10 or newer** (python.org/downloads). On Windows, tick **"Add python.exe to PATH"** in the installer.
Check: open a terminal and run `python --version`.

**FFmpeg** (the video engine).
- Windows: open PowerShell and run `winget install Gyan.FFmpeg`, then **close and reopen the terminal**.
- Mac: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

Check: `ffmpeg -version` should print a version number (4.4 or newer recommended).

**VS Code** (code.visualstudio.com). Open it, choose *File > Open Folder* and pick the `lumina-studio` folder.
VS Code suggests recommended extensions (Python, SQLite Viewer): click **Install**.

## 2. Install the project

In VS Code open the terminal (*Terminal > New Terminal*) and run:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
On Mac/Linux use `source .venv/bin/activate` instead of the second line.
(If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.)

## 3. Configure provider keys

For AI-assisted production, configure at least one AI provider key. A footage
provider key is optional; stock availability may be limited without one.
Provider plans, free tiers, charges and credit-card requirements vary and can
change. Review each provider's current terms before use.

| Purpose | Service | Where |
|---|---|---|
| AI (pick at least one) | Gemini | https://aistudio.google.com/apikey |
| | OpenRouter | https://openrouter.ai/keys |
| | Groq (optional) | https://console.groq.com/keys |
| | Cerebras (optional) | https://cloud.cerebras.ai |
| Footage (pick at least one) | Pexels | https://www.pexels.com/api/ |
| | Pixabay | https://pixabay.com/api/docs/ (sign in to see your key) |

Two ways to add them:
- **In the app:** *Settings > keys* (recommended: stored only on your computer, never shown again), or
- **In a file:** copy `.env.example` to `.env` (`copy .env.example .env` on Windows) and paste the keys after the `=` signs. Never upload `.env`.

Rate limits and model availability vary by provider, model, region and account.
The app applies configured request pacing and can use configured provider
fallbacks, but cannot guarantee that an account has free quota or avoid provider
charges.

## 4. Check everything

```
python run.py doctor
```
Every line should say `OK`. Lines that say `FIX` tell you what to do.

## 5. Start

```
python run.py
```
Your browser opens with a link like `http://127.0.0.1:8787/#token=...`. The token is your private password; it is saved in the browser. Run `python run.py token` to see the link again.

## 6. Make your first video

Type a topic, keep **Short**, and press **Create video**. Watch the agents work on the left. A 60-second Short is a good first test; choose **Draft** quality in Settings for the fastest test.

## Optional upgrades

- **Music:** put royalty-free tracks in `assets/music/` (see the README there).
- **Logo watermark:** save a transparent `logo.png` in `assets/brand/`.
- **Your channel:** Settings > *Your channel* (name, tone, accent colour, call to action).
- **Better voices:** the studio uses `edge-tts` (free neural voices) automatically. Offline fallbacks: Piper or eSpeak, if installed.

## Tips for a low-RAM laptop (4 GB)

- Close other programs and browser tabs before rendering.
- Use **Draft** quality for tests and **Standard** for final videos. Avoid **High**.
- Make Shorts first. A long video renders section by section (so memory stays low) but takes much longer.
- Free disk space: a long video downloads many clips. Run `python run.py clean` to delete cached downloads and temporary render files (finished videos are kept).
- Rendering speed depends on your CPU. As a rough guide, expect several minutes for a Short and an hour or more for a 30-minute video on a low-end laptop.

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` is not recognised | Reinstall Python and tick "Add to PATH", then reopen the terminal |
| `ffmpeg` is not recognised | Install FFmpeg, then close and reopen the terminal |
| The video has no voice | `pip install -r requirements.txt` (installs `edge-tts`) and check your internet |
| "Add a free AI key" | Settings > keys, then press **Save keys** |
| "No footage found" | Add a Pexels or Pixabay key; try a more concrete topic |
| "Every AI model is rate-limited" | Wait a few minutes, or add a second AI key |
| Browser says "Not authorised" | Run `python run.py token` and open the link, or paste the token in Settings |
| Something failed halfway | Open the video in *Videos* and press **Retry**: finished steps are not repeated |

Errors are written to `data/logs/errors.log` (keys are removed from it automatically).
