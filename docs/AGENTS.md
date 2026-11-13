# The 36 agents

This page is the quick inventory. For detailed purpose, inputs/outputs,
workflow, dependencies, security/error behavior, admin controls and examples
for every registered agent, see the [Admin and AI Agent Master Guide](./01-Admin-and-AI-Agent-Master-Guide.md).

Each agent has one job. **AI** agents use a free language model; **Code** agents are plain program logic (faster, free, and they do not use your AI quota). The Director runs them in this order and saves progress after every step.

## Order of work

`Security -> Quota -> Cost estimate -> Brand memory -> Analytics -> Audience -> Trends -> Research -> Script -> Story structure -> Originality -> Hook -> Fact-check -> Compliance -> Source citations -> [script review] -> Shot planner -> Style -> Footage -> Picker -> [footage review] -> Narrator -> Mastering (clean) -> Music -> Mastering (mix) -> Captions -> Accessibility -> Localization -> Editor -> SEO -> Thumbnail -> Thumbnail experiment -> License -> Rights -> Security (final scan) -> Publishing -> QA -> Repurposing -> Calendar -> Brand memory (save)`

If a **non-critical** agent fails (for example Trends or Hook), the studio logs a warning and continues. If a **critical** agent fails, the video stops and can be retried from that step.

| # | Agent | Type | What it does and how |
|---|---|---|---|
| 1 | **Director** | Code | Runs the pipeline, checkpoints each step, pauses at review gates, resumes after failures |
| 2 | **Research** | AI + Code | Reads Wikipedia pages via its free API (untrusted data), has the AI extract facts with sources and confidence |
| 3 | **Script writer** | AI | Writes original narration. Shorts: one pass. Long: outline, then one section at a time. Trims to the length budget |
| 4 | **Fact-check and editor** | AI + Code | Flags numbers not found in sources, removes repeated sentences, rewrites unsupported claims, lists every issue |
| 5 | **Shot planner** | AI + Code | Splits the script into scenes and writes three search terms per scene (specific, broader, generic) plus mood |
| 6 | **Footage finder** | Code | Searches Pexels, Pixabay (and Wikimedia Commons for history) for real video and photos; cached and rate-limited |
| 7 | **Picker** | AI + Code | Scores candidates (relevance, shape, resolution, no repeats), downloads the best, records its licence; AI settles close calls |
| 8 | **Narrator** | Code | Free neural voice (edge-tts) with word timings; speeds up slightly or trims scenes if over the length limit |
| 9 | **Music and sound** | Code | Chooses music by mood from your library (or a synthetic pad), loops it, adds soft transition sounds |
| 10 | **Editor and renderer** | Code | FFmpeg: cuts to narration, photo motion, grade, dissolves, captions, chapter titles; renders section by section |
| 11 | **SEO** | AI + Code | Five scored title options, keyword-rich description, tags and hashtags within YouTube's limits |
| 12 | **Thumbnail** | AI + Code | Three designs from real frames/photos with a short headline, each under 2 MB |
| 13 | **Quality assurance** | Code | Inspects the final file: length, resolution, loudness, voice, subtitles, thumbnails, credits, risk |
| 14 | **Trend and niche analyst** | AI + Code | YouTube search suggestions plus AI to choose the main keyword and a fresh angle |
| 15 | **Hook and retention** | AI + Code | Scores alternative openings, splits long sentences, reports pacing risks |
| 16 | **Visual style director** | Code | One consistent look: colour grade, transitions, zoom, accent colour from mood and brand |
| 17 | **Compliance and monetization safety** | AI + Code | Risky wording rules, source-overlap (originality) check, AI policy review; high risk pauses for you |
| 18 | **License and attribution** | Code | Ledger of every asset's author and licence; writes `credits.txt` and the credits block |
| 19 | **Caption and translation** | AI + Code | Word-timed subtitles (karaoke style for Shorts), `.srt` files, AI translation per language |
| 20 | **Audio mastering** | Code | Cleans each voice clip, ducks music under speech, loudness-normalises to about -14 LUFS |
| 21 | **Repurposing** | AI + Code | Picks the strongest 35-75 second moments of a long video and renders vertical Shorts |
| 22 | **Publishing package** | Code | Final title, description with chapters and credits, tags, pinned comment, checklist, upload kit |
| 23 | **Analytics and feedback** | AI + Code | Learns from your imported YouTube Studio CSV (which titles, lengths and topics work) |
| 24 | **Quota and cost manager** | Code | Sliding-window limits per API, search cache, preflight estimate, usage numbers |
| 25 | **Security guard** | Code | Cleans input, treats web text as untrusted, checks FFmpeg and disk, scans output for leaked keys |
| 26 | **Brand memory** | Code | Stores your channel profile, past videos and used clips; warns about repeated topics |
| 27 | **Audience researcher** | Code | Combines channel profile, trends, and analytics into a viewer brief |
| 28 | **Source citation** | Code | Maps researched claims to reference URLs and flags unmatched claims |
| 29 | **Originality reviewer** | Code | Compares topics and titles with the user's own past videos |
| 30 | **Story structure editor** | Code | Flags overly long openings/closings and scripts with too few sections |
| 31 | **Accessibility reviewer** | Code | Checks generated caption files and flags long subtitle lines |
| 32 | **Localization reviewer** | Code | Checks requested translation languages against generated captions |
| 33 | **Thumbnail experiment designer** | Code | Stores the available thumbnail concepts as a trackable experiment |
| 34 | **Content calendar planner** | Code | Adds a draft calendar entry when production finishes |
| 35 | **Rights and release assistant** | Code | Flags footage missing attribution or licence details |
| 36 | **Production cost analyst** | Code | Estimates scenes, API demand, and render time before production |

## Where the files are

All agent code is in `lumina/agents/` (`planning.py`, `visuals.py`, `audio_agents.py`, `publishing.py`, `repurpose.py`, `director.py`). The renderer is `lumina/render.py`, the timeline math `lumina/timeline.py`, stock search `lumina/sources.py`, voice `lumina/tts.py`.
