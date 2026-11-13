# Lumina Studio

Type a topic. Get a finished video and everything you need to publish it.

Lumina Studio is an all-in-one video studio with **36 cooperating agents**. It writes the script, finds **real stock footage and photos** (Pexels, Pixabay, Wikimedia Commons), records a natural voice-over, edits everything with FFmpeg, and prepares the title, description, tags, thumbnails, subtitles and credits. **No AI-generated video or images are used.** The AI is used for research, writing and planning only.

- **Shorts:** vertical 1080x1920, up to 90 seconds, karaoke-style captions
- **Long videos:** 1920x1080, up to 30 minutes, chapters, intro and outro cards
- **Live view:** a minimal interface shows each agent working, what it is doing right now, and how it works
- **Account workflows:** Supabase sign-in, personal/team workspaces, reusable templates, project versions, previews, review links, a media library, and a publishing calendar
- **Deployment:** Vercel or Cloudflare Pages for the static interface, Railway for the persistent renderer/API, PostgreSQL for hosted records, and SQLite for local use
- **Provider costs:** some providers offer free tiers, but quotas, prices, eligibility and usage charges vary and can change. Check each provider's current terms before production use.

## Quick start (about 10 minutes)

1. Install **Python 3.10+**, **FFmpeg** and **VS Code**: see [docs/SETUP.md](docs/SETUP.md)
2. In VS Code open this folder, then in the terminal:
   ```
   python -m venv .venv
   .venv\Scripts\activate          (Mac/Linux: source .venv/bin/activate)
   pip install -r requirements.txt
   ```
3. Get free keys (an AI key and a footage key) and paste them in the app's Settings, or into `.env`
4. Check everything: `python run.py doctor`
5. Start: `python run.py` (your browser opens with a private access link)

## The guides

| Guide | What it covers |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Install, free keys, VS Code, first run, low-RAM tips |
| [docs/USAGE.md](docs/USAGE.md) | Making Shorts and long videos, review screens, uploading and monetization |
| [docs/DATABASE.md](docs/DATABASE.md) | The built-in database: what it stores, how to view, back up and restore |
| [docs/GITHUB.md](docs/GITHUB.md) | Push the project to GitHub safely (without leaking keys) |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Deploy the web console on Cloudflare Pages and the renderer/API on Railway |
| [docs/AGENTS.md](docs/AGENTS.md) | All 36 agents and how each one works |
| [docs/SECURITY.md](docs/SECURITY.md) | The protections built in and what you must still do |
| [docs/01-Admin-and-AI-Agent-Master-Guide.md](docs/01-Admin-and-AI-Agent-Master-Guide.md) | Single-owner administration and behavior of all 36 agents |
| [docs/02-Complete-Application-User-Guide.md](docs/02-Complete-Application-User-Guide.md) | End-user workflows, features and troubleshooting |
| [docs/03-Deployment-and-Credentials-Guide.md](docs/03-Deployment-and-Credentials-Guide.md) | Vercel, Railway, Cloudflare and credential configuration |
| [docs/PROJECT-ARCHITECTURE.md](docs/PROJECT-ARCHITECTURE.md) | Runtime, request flow, data stores and source layout |
| [docs/SECURITY-AND-ADMIN-PERMISSIONS.md](docs/SECURITY-AND-ADMIN-PERMISSIONS.md) | Owner bootstrap, role grants and security limitations |
| [docs/DEPLOYMENT-CHECKLIST.md](docs/DEPLOYMENT-CHECKLIST.md) | Production launch and post-deployment checks |

## The 36 agents

Director · Research · Script writer · Fact-check and editor · Shot planner · Footage finder · Picker · Narrator ·
Music and sound · Editor and renderer · SEO · Thumbnail · Quality assurance · Trend and niche analyst ·
Hook and retention · Visual style director · Compliance and monetization safety · License and attribution ·
Caption and translation · Audio mastering · Repurposing · Publishing package · Analytics and feedback ·
Quota and cost manager · Security guard · Brand memory · Audience researcher · Source citation · Originality reviewer ·
Story structure editor · Accessibility reviewer · Localization reviewer · Thumbnail experiment designer ·
Content calendar planner · Rights and release assistant · Production cost analyst

## Project layout

```
run.py              start the studio / doctor / backup / clean
lumina/             the application (agents, renderer, server, database)
web/                the web interface (Vite, deployable to Cloudflare Pages)
assets/music/       your royalty-free music (optional)
assets/brand/       logo.png for a watermark (optional)
data/               created automatically: SQLite database, videos, cache, backups (never commit)
tests/              automated tests (run offline with fake services)
docs/               the guides
```

## Honest limits

- **External providers not live-verified.** Automated tests use simulated AI, Pexels, Pixabay and Wikipedia services and include FFmpeg rendering. The suite is being run in this Windows workspace, but the project has not been verified against real provider accounts or a live deployment. Run `python run.py doctor` in your own environment before production.
- **Free means limited.** Free AI and stock APIs have rate limits that change. The studio paces itself, caches searches, falls back between models, and saves progress after every step, but a 30-minute video can take a long time on free tiers.
- **Stock footage cannot illustrate everything.** Abstract or very specific topics produce looser matches. Use the footage review screen to swap clips.
- **A low-end laptop renders slowly.** Use *Draft* quality while testing.
- **No tool can guarantee monetization.** YouTube decides. The studio helps with originality checks, licence records and disclosure, but original value in every video is what counts. See [docs/USAGE.md](docs/USAGE.md).
- Internet Archive and Unsplash are not included (Unsplash's API terms require hot-linking rather than downloading).

## Tests

```
python -m unittest discover -s tests -v
```
Needs FFmpeg. Runs offline with fake services. Rendering tests take a few minutes.
