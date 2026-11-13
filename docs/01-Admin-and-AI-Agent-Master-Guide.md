# Admin and AI Agent Master Guide

## What is implemented

Lumina has 36 registered pipeline agents (the Director plus 35 specialists) and a server-enforced administrative surface. “AI agent” here includes ordinary deterministic Python agents; only entries marked AI or AI + Code call a language model. There is no autonomous general-purpose tool agent: the Director chooses from a fixed pipeline and the browser cannot provide tools or code to execute.

Exactly one Super Admin can be configured. This is a UUID binding to an existing Supabase Auth identity, not a public role registration. The application does not create an owner identity from a browser form.

## Admin Control Center

The Admin button is displayed after the server returns a permitted admin capability. This is only presentation; every admin API independently checks the authenticated account's permission.

| Area | Implemented behavior |
|---|---|
| Global dashboard | Job totals/status, active jobs, API calls over seven days, AI request/token counters, admin actions, DB backend/connectivity, filesystem space, maintenance state |
| User directory | Supabase Auth user list/search, invitation, control status/role, confirmed deletion of Auth identity |
| Account status | Active/inactive/suspended; requests from disabled accounts are rejected; provider ban/unban is also requested |
| Roles and permissions | Fixed roles (`member`, `viewer`, `support`, `operator`) with server-side permission allow-lists; owner-only assignment; owner is not assignable |
| Agent management | List registered agents and 30-day persisted step statistics; toggle optional agents; critical pipeline agents cannot be disabled |
| Features | Server-enforced flags for new jobs, direct publishing, workspaces, and media library |
| Settings/integrations | Allow-listed non-secret app settings via the normal Settings UI; configured/not-configured checks; never returns secret values |
| Security/activity | Audit events and recently verified sign-in sessions with hashed session/IP identifiers |
| Notifications | Outbox status and retry for failed Resend notifications |
| Backups | List and create database backups; does not provide in-place restore |
| Health/errors | App health, database state, bounded redacted error log |
| Maintenance/global control | Stops ordinary API use while leaving health, admin access, OAuth completion and temporary publishing media available |
| Billing/payments/revenue | **Deferred; not implemented.** No provider, payment records, subscriptions, or revenue figures are fabricated |

### Role permissions

`member` is not an administrator. `viewer` reads dashboard, user/agent directory, analytics, audit, health, logs and notifications. `support` reads user/audit/health/log/session data and has the account-status grant. `operator` gets operational read grants and limited agent/setting/feature/maintenance/notification/backup/status actions. Sensitive account/role assignment, invitation and deletion remain owner-only. A role grant is always intersected with the server's hard-coded role allow-list.

The owner may configure non-owner role/status/permissions but cannot alter or delete the owner account through these APIs. Typed-email confirmation is required to delete another Auth identity. Deleting the Auth identity does **not** erase its Lumina data; data retention is explicit and must be handled under a separately approved retention policy.

### Implemented capability status

The API lists 35 catalogued administration capabilities. Most monitoring and controls above are implemented; live agent parameter tuning is `not_implemented`, backup restore is `manual_only`, and subscription/revenue/payment entries are `deferred_no_billing`. Do not interpret the catalog count as a claim that every imaginable admin function is complete. Environment secrets are changed only in the backend host's secret manager.

## Agent execution contract (applies to all 36)

The Director invokes registered agents as `run(job, phase=None)`. The job contains validated parameters, the user's project state and helpers for cancellation, persistence, files, events, and database access. A specialist normally returns a result dictionary, which the Director merges into the job state and checkpoints. Some agents also update a timeline, ledger, output file, or database record. The API authenticates/scopes a job before it is queued; agents do not have independent user-login permissions.

AI calls use Lumina's configured provider chain and quota/caching/error handling. Code agents do not spend model tokens. External calls are limited to the integrations listed below; agents do not have a general browser, shell, or arbitrary network tool. Provider credentials remain server-side.

The Director catches specialist failures. A critical step is marked failed and stops the job; a non-critical step is marked skipped with a warning and the pipeline continues. Cancellation and review pauses propagate. Step state supports retry/resume. Job state/events/steps and media/asset metadata are stored through the application database/filesystem as applicable. There is no separate per-agent permission grant. Admin may inspect run/failure durations and disable only non-critical agents. Runtime parameter tuning is not implemented.

For every specialist below:

- **Permissions:** The authenticated job owner/workspace controls access before execution. Agents do not authorize users or expose administrative tools.
- **Security:** Validate at API boundaries; fetched text is untrusted; keep credentials server-side; use allow-listed downloads; redact errors and generated outputs as applicable.
- **Performance/error behavior:** Work is sequential in the Director pipeline. AI/network/render time depends on provider and input. Failures follow the critical/non-critical rule above, not a silent success fallback.
- **Communication:** Agents pass results through job state, shared job helpers, database/files, and Director events; no direct agent-to-agent RPC or agent chat exists.
- **Practical usage:** A user starts a Short/Long job in the browser; the agent participates at its position in that workflow. Agent-specific examples are listed with each entry.

## Agent specifications

The implementation source (`backend/lumina/agents/`) is authoritative for exact method details. The summaries below distinguish AI-backed work from deterministic Python logic and describe the actual registered responsibilities.

### 1. Director (`director`, Code, critical)
- **Purpose/responsibility:** Orchestrate the fixed video-production pipeline and resume work.
- **Features/functions/workflow:** Selects the normal or repurpose pipeline; emits start/progress/status events; executes step agents; saves step state; pauses at script/scene review gates; retries only unfinished steps; assembles final deliverables.
- **Tools, inputs, outputs:** Uses registry, job helpers, database and timeline builder. Input is a validated job and prior step state. Output is final job status and `deliverables` list; step-specific results are persisted in state.
- **Integrations/database:** Application DB (`steps`, `events`, job rows), job directory/files, specialist agents.
- **Decisions/example:** Skips already-completed steps, honors review settings/compliance hold, stops on critical failure. Example: turn on “review script” and the Director pauses after compliance/citation checks for editing.
- **Admin:** Always protected as a critical agent; no live tuning.

### 2. Research (`research`, AI + Code, critical)
- **Purpose/responsibility:** Gather source-grounded reference facts for the topic.
- **Features/functions/workflow:** Retrieves reference pages (including Wikipedia), sanitizes fetched text as untrusted, and asks the configured model to extract useful facts with sources/confidence.
- **Inputs/outputs:** Topic and research context; returns a structured research/fact result in job state for scripting and citation.
- **Tools/integrations:** Wikipedia/reference HTTP integration and LLM chain; no arbitrary agent tool use.
- **Database/dependencies:** Shared job context and cached HTTP/provider behavior where available; required input for trustworthy script flow.
- **Decisions/example:** “How volcanoes form” yields cited reference points to inform the script, not a guarantee of authoritative fact checking.
- **Admin:** Critical; cannot be disabled.

### 3. Script writer (`script`, AI, critical)
- **Purpose/responsibility:** Create original spoken narration.
- **Features/functions/workflow:** Generates a hook, coherent body and closing; long videos use an outline and section-by-section writing; trims to the requested word/time budget.
- **Inputs/outputs:** Topic, duration, language, research, brand/audience and review parameters; script title, sections/headings/narration and word metadata.
- **Tools/integrations:** Configured LLM chain only.
- **Database/dependencies:** Reads prior job state; writes script state later used by review, narration, captions and publishing.
- **Decisions/example:** A 60-second Short requests concise narration; a Long job uses an outline and several sections.
- **Admin:** Critical; cannot be disabled.

### 4. Fact-check and editor (`factcheck`, AI + Code, non-critical)
- **Purpose/responsibility:** Compare script claims with available research and improve clarity.
- **Features/functions/workflow:** Finds repeated sentences and unsupported claims/numbers, attempts corrections or safer wording, and records issues.
- **Inputs/outputs:** Script plus research; updated script and issue report.
- **Tools/integrations:** Deterministic text checks and configured LLM.
- **Database/dependencies:** Shared job state; informs compliance and optional user script review.
- **Decisions/example:** Unsupported precise statistic is flagged or softened rather than presented as proven.
- **Admin:** Optional; may be disabled. Failure produces warning/skip, not a fabricated check.

### 5. Shot planner (`shots`, AI + Code, critical)
- **Purpose/responsibility:** Convert narration into searchable visual scenes.
- **Features/functions/workflow:** Segments narration, proposes specific-to-general search terms, visual type and mood for each scene.
- **Inputs/outputs:** Script and duration; scene records/search terms consumed by style/footage agents.
- **Tools/integrations:** Configured LLM and local scene-planning helpers.
- **Database/dependencies:** Shared job state; required by footage acquisition and rendering.
- **Decisions/example:** A scene about volcanic ash gets narrower and broader visual queries so the search has fallbacks.
- **Admin:** Critical; cannot be disabled.

### 6. Footage finder (`footage`, Code, critical)
- **Purpose/responsibility:** Find real stock video and stills for scenes.
- **Features/functions/workflow:** Searches Pexels/Pixabay and uses Wikimedia Commons for suitable historical/reference content; search is cached and quota-limited.
- **Inputs/outputs:** Scene queries and format; candidate asset metadata.
- **Tools/integrations:** Pexels, Pixabay, Wikimedia Commons, stock cache/rate limits.
- **Database/dependencies:** Search cache/API counters and job asset metadata; picker consumes candidates.
- **Decisions/example:** Searches multiple query fallbacks when the specific phrase has no useful matches.
- **Admin:** Critical; cannot be disabled.

### 7. Picker (`picker`, AI + Code, critical)
- **Purpose/responsibility:** Select the best usable candidate and avoid repeated footage.
- **Features/functions/workflow:** Scores relevance, aspect ratio, resolution, duplication and scene fit; downloads the selected asset and records attribution/licence. AI can resolve close candidate choices.
- **Inputs/outputs:** Candidate list, scene context, user history; selected/downloaded asset plus metadata/ledger entry.
- **Tools/integrations:** Local scoring, optional LLM, allow-listed asset downloads.
- **Database/dependencies:** Asset ledger and used-assets history; depends on footage candidates.
- **Decisions/example:** Prefers a high-resolution vertical clip over a weak landscape candidate for a Short.
- **Admin:** Critical; cannot be disabled.

### 8. Narrator (`narrator`, Code, critical)
- **Purpose/responsibility:** Generate spoken audio aligned to narration text.
- **Features/functions/workflow:** Uses configured speech engine, records word timings, fits audio to the duration by modest speed adjustment or trimming less important scenes.
- **Inputs/outputs:** Script, selected language/voice, scene timing and duration; audio files and word timing data.
- **Tools/integrations:** `edge-tts` and configured local fallback engines when installed; filesystem/audio helpers.
- **Database/dependencies:** Job files/state; required by mastering and captions.
- **Decisions/example:** If narration overruns the target, adjusts within implemented bounds before reducing content.
- **Admin:** Critical; cannot be disabled.

### 9. Music and sound (`music`, Code, non-critical)
- **Purpose/responsibility:** Add mood-matched background music and transitions.
- **Features/functions/workflow:** Selects from local royalty-free music library, loops/crops it, and can synthesize a soft ambient fallback and subtle transition effects.
- **Inputs/outputs:** Script mood, duration, audio preferences and assets folder; music/audio paths and timeline data.
- **Tools/integrations:** Local files and audio utilities; no external stock music API.
- **Database/dependencies:** Job files/state; consumed by mastering/editor.
- **Decisions/example:** With no local track, produces an ambient pad; user-provided library is preferable for final work.
- **Admin:** Optional; may be disabled.

### 10. Editor and renderer (`editor`, Code, critical)
- **Purpose/responsibility:** Render the assembled video.
- **Features/functions/workflow:** Cuts media to scene timing, adds photo movement, grade/transitions/captions/chapter titles, renders sections and combines mastered audio.
- **Inputs/outputs:** Scene assets/timeline, narration, captions, format/quality settings; rendered video file(s).
- **Tools/integrations:** FFmpeg/FFprobe through media/render helpers.
- **Database/dependencies:** Persistent job files and step state; depends on visuals and audio outputs.
- **Decisions/example:** A Short uses vertical output settings; a Long video is rendered in sections to limit memory.
- **Admin:** Critical; cannot be disabled. Runtime quality knobs use ordinary allow-listed app settings.

### 11. SEO agent (`seo`, AI + Code, critical)
- **Purpose/responsibility:** Prepare search/discovery metadata.
- **Features/functions/workflow:** Produces title options, description, tags/hashtags and validates platform constraints; scores title candidates.
- **Inputs/outputs:** Topic, script, keywords, audience and channel profile; metadata package used by publishing.
- **Tools/integrations:** Configured LLM and local length/score helpers.
- **Database/dependencies:** Job state; publishing package later adds credits/chapters.
- **Decisions/example:** Generates multiple title alternatives and checks lengths before delivery; does not guarantee ranking.
- **Admin:** Critical; cannot be disabled.

### 12. Thumbnail agent (`thumbnail`, AI + Code, critical)
- **Purpose/responsibility:** Create thumbnail choices from actual job imagery.
- **Features/functions/workflow:** Selects strong frames/photos, proposes short headline text, renders multiple designs and keeps them within size limits.
- **Inputs/outputs:** Footage/frame assets, script/title and brand styling; thumbnail images and metadata.
- **Tools/integrations:** Configured LLM for text plus local image/frame processing.
- **Database/dependencies:** Job files/state; used by deliverables/QA.
- **Decisions/example:** Produces three candidate designs; they are based on real source imagery, not AI-generated video.
- **Admin:** Critical; cannot be disabled.

### 13. Quality assurance (`qa`, Code, critical)
- **Purpose/responsibility:** Check deliverables before the user publishes.
- **Features/functions/workflow:** Checks duration, frame dimensions, audio/loudness, subtitles, thumbnails, credits and risk flags; reports findings.
- **Inputs/outputs:** Rendered media and all job deliverables; status and issue list.
- **Tools/integrations:** FFprobe/media/file inspection and job state.
- **Database/dependencies:** Job files/state; final Director status uses the QA result.
- **Decisions/example:** A missing subtitle or invalid render is surfaced as attention/failure rather than claimed as clean.
- **Admin:** Critical; cannot be disabled.

### 14. Trend and niche analyst (`trends`, AI + Code, non-critical)
- **Purpose/responsibility:** Find search phrasing and a timely angle.
- **Features/functions/workflow:** Reads YouTube search suggestions, asks the model to rank relevant terms/angles, passes them forward.
- **Inputs/outputs:** Topic/niche; keyword and angle suggestions.
- **Tools/integrations:** Search-suggestion HTTP endpoint and LLM.
- **Database/dependencies:** API quota/cache and job state; can be absent without blocking the script.
- **Decisions/example:** Uses related query suggestions to suggest a narrower angle; it does not fetch private YouTube analytics.
- **Admin:** Optional; may be disabled.

### 15. Hook and retention (`hook`, AI + Code, non-critical)
- **Purpose/responsibility:** Improve the opening and spoken pacing.
- **Features/functions/workflow:** Produces/ranks hook alternatives, checks long sentences and flags pacing concerns.
- **Inputs/outputs:** Draft script and length; revised opening and pacing notes.
- **Tools/integrations:** LLM and sentence/word helpers.
- **Database/dependencies:** Job state; feeds script/narration downstream.
- **Decisions/example:** Keeps a concise opening that best fits the selected format; scores are advisory.
- **Admin:** Optional; may be disabled.

### 16. Visual style director (`style`, Code, critical)
- **Purpose/responsibility:** Keep visuals consistent.
- **Features/functions/workflow:** Chooses grade, transition/zoom treatment and caption accent based on content mood and brand.
- **Inputs/outputs:** Script, scene descriptors and brand profile; style parameters in state.
- **Tools/integrations:** Deterministic style helpers; no external service.
- **Database/dependencies:** Reads profile/state; renderer consumes the style values.
- **Decisions/example:** Uses the saved brand accent where available while retaining format-safe text treatment.
- **Admin:** Critical; cannot be disabled.

### 17. Compliance and monetization safety (`compliance`, AI + Code, critical)
- **Purpose/responsibility:** Flag risky language and source overlap for human review.
- **Features/functions/workflow:** Applies deterministic wording checks and AI policy/originality review; a high risk can force the Director's script review gate.
- **Inputs/outputs:** Script, sources and configured compliance mode; risk assessment and block/review flags.
- **Tools/integrations:** Local rules and LLM.
- **Database/dependencies:** Job state/events; executes before review and production.
- **Decisions/example:** High risk pauses for a user decision; this is assistance, not a legal or platform-policy guarantee.
- **Admin:** Critical; cannot be disabled.

### 18. License and attribution (`license`, Code, critical)
- **Purpose/responsibility:** Record attribution information for used assets.
- **Features/functions/workflow:** Consolidates asset author/source/licence records and creates credits output.
- **Inputs/outputs:** Selected footage/media and track metadata; attribution ledger, credits file/text.
- **Tools/integrations:** Local job asset records and files.
- **Database/dependencies:** Asset ledger; QA and rights checks consume it.
- **Decisions/example:** An asset with incomplete licence metadata remains visible for user attention.
- **Admin:** Critical; cannot be disabled.

### 19. Caption and translation (`captions`, AI + Code, critical)
- **Purpose/responsibility:** Deliver timed captions and requested translations.
- **Features/functions/workflow:** Maps word timing to subtitle segments, emits SRT files, and translates text for requested languages.
- **Inputs/outputs:** Narration timings, script, language/translation preferences; subtitle files and caption metadata.
- **Tools/integrations:** Local caption timing/formatting and configured LLM for translation.
- **Database/dependencies:** Job state/files; renderer, accessibility and localization use outputs.
- **Decisions/example:** Writes an English SRT and additional requested-language SRTs where selected.
- **Admin:** Critical; cannot be disabled.

### 20. Audio mastering (`mastering`, Code, critical)
- **Purpose/responsibility:** Clean voice audio and mix voice/music.
- **Features/functions/workflow:** `clean` phase processes voice clips; `mix` phase balances music under speech and normalizes output loudness.
- **Inputs/outputs:** Narration clips, music and timeline; mastered voice/mix files.
- **Tools/integrations:** FFmpeg/audio filters.
- **Database/dependencies:** Job filesystem/state; used by final render.
- **Decisions/example:** Director invokes both phases at their defined pipeline positions.
- **Admin:** Critical; cannot be disabled.

### 21. Repurposing (`repurpose`, AI + Code, non-critical)
- **Purpose/responsibility:** Make Shorts from strong parts of a long video.
- **Features/functions/workflow:** Finds candidate moments and renders vertical derivative jobs with captions/metadata.
- **Inputs/outputs:** Parent long-video script/timeline/media and repurpose preference; child short-job records/files.
- **Tools/integrations:** Local timeline/render helpers and LLM assistance where configured.
- **Database/dependencies:** Parent/child job and filesystem state; normal Director pipeline for the child.
- **Decisions/example:** User enables “create Shorts” for a Long video; candidates are bounded to short segments.
- **Admin:** Optional; may be disabled.

### 22. Publishing package (`publish`, Code, critical)
- **Purpose/responsibility:** Prepare the manual-upload kit.
- **Features/functions/workflow:** Combines title/description, chapters, credits, tags, pinned comment, checklist and AI-voice reminder.
- **Inputs/outputs:** Script, SEO, render duration, credits and publishing metadata; `publish.json` and text deliverables.
- **Tools/integrations:** Local formatting and job files.
- **Database/dependencies:** Job state/files; direct platform uploads are a separate explicit user-confirmed service workflow.
- **Decisions/example:** Package can be copied into YouTube Studio; it does not auto-publish.
- **Admin:** Critical; cannot be disabled.

### 23. Analytics and feedback (`analytics`, AI + Code, non-critical)
- **Purpose/responsibility:** Use account-imported YouTube CSV performance data as planning feedback.
- **Features/functions/workflow:** Analyzes title, length and topic patterns and shares lessons with script/SEO.
- **Inputs/outputs:** User-imported CSV-derived analytics; observations/heuristics in job state.
- **Tools/integrations:** Local CSV parsing/statistics and LLM summarization where used.
- **Database/dependencies:** `analytics` rows scoped to account; gracefully skips without data.
- **Decisions/example:** A pattern that number-led titles performed better is a suggestion, not a promise.
- **Admin:** Optional; may be disabled. Global admin analytics shows aggregated usage, not a user's private CSV contents.

### 24. Quota and cost manager (`quota`, Code, critical)
- **Purpose/responsibility:** Preflight provider availability and estimated request usage.
- **Features/functions/workflow:** Checks AI/stock configuration, API limits, cached searches and expected demand.
- **Inputs/outputs:** Job dimensions and provider/quota state; preflight/usage estimates and warnings.
- **Tools/integrations:** Local quota manager and DB counters.
- **Database/dependencies:** `api_calls`, `llm_usage`, cache and configured limits.
- **Decisions/example:** Warns if a free API quota may slow a large video; external provider charges are not calculated unless known.
- **Admin:** Critical; cannot be disabled.

### 25. Security guard (`security`, Code, critical)
- **Purpose/responsibility:** Enforce input/output and runtime safety checks.
- **Features/functions/workflow:** Cleans topics, treats external text as untrusted, checks FFmpeg/disk requirements, scans output for accidental secret text.
- **Inputs/outputs:** Job input and generated/fetched content; check results and blocking errors.
- **Tools/integrations:** Local security helpers, filesystem/runtime checks.
- **Database/dependencies:** Job context and security audit events as applicable.
- **Decisions/example:** Rejects invalid topics or blocks a deliverable if a known secret pattern is found.
- **Admin:** Critical; cannot be disabled.

### 26. Brand memory (`brand`, Code, critical)
- **Purpose/responsibility:** Keep content aligned with the user's channel profile and prevent repeats.
- **Features/functions/workflow:** Loads profile/tone/style/banned phrases, checks video/topic history and used assets, then saves completed project history.
- **Inputs/outputs:** Account-scoped brand profile and current job; brand context and history updates.
- **Tools/integrations:** Local app/DB methods and asset ledger.
- **Database/dependencies:** `kv`, `history`, `used_assets` and profile context, scoped to the user's account.
- **Decisions/example:** Warns when a topic resembles a previous title or when footage has already been used.
- **Admin:** Critical; cannot be disabled.

### 27. Audience researcher (`audience`, Code, non-critical)
- **Purpose/responsibility:** Summarize the intended viewer.
- **Features/functions/workflow:** Combines channel profile, search signals and imported performance into a practical audience brief.
- **Inputs/outputs:** Topic, brand/channel profile, trends and analytics; viewer brief.
- **Tools/integrations:** Local analysis; no separate external audience data source.
- **Database/dependencies:** Shared job state and account profile/analytics data.
- **Decisions/example:** Provides an assumed viewer need to the writer; does not claim demographic identity data.
- **Admin:** Optional; may be disabled.

### 28. Source citation (`source_citation`, Code, non-critical)
- **Purpose/responsibility:** Connect script claims to research references.
- **Features/functions/workflow:** Maps claims to gathered source URLs and flags untraced statements.
- **Inputs/outputs:** Script and research results; citation mapping and missing-source warnings.
- **Tools/integrations:** Local text/source matching.
- **Database/dependencies:** Research in shared state; supports compliance, credits and review.
- **Decisions/example:** An assertion without a matching source is labeled for human checking.
- **Admin:** Optional; may be disabled.

### 29. Originality reviewer (`originality`, Code, non-critical)
- **Purpose/responsibility:** Detect repetition against the user's own past content.
- **Features/functions/workflow:** Compares current topic/script/title with history and reports similarity.
- **Inputs/outputs:** Current job plus account-owned history; repeat-risk report.
- **Tools/integrations:** Local similarity helpers.
- **Database/dependencies:** Account-scoped history table.
- **Decisions/example:** Flags a near-duplicate topic for revision; it cannot compare against all videos on the internet.
- **Admin:** Optional; may be disabled.

### 30. Story structure editor (`story_structure`, Code, non-critical)
- **Purpose/responsibility:** Check script structure before recording.
- **Features/functions/workflow:** Applies local checks to opening, section flow and closing.
- **Inputs/outputs:** Script sections; structural warnings/summary.
- **Tools/integrations:** Deterministic text/length checks; no LLM by default.
- **Database/dependencies:** Shared script state.
- **Decisions/example:** Warns if an opening/closing dominates a short script.
- **Admin:** Optional; may be disabled.

### 31. Accessibility reviewer (`accessibility`, Code, non-critical)
- **Purpose/responsibility:** Inspect caption readability and deliverables.
- **Features/functions/workflow:** Checks generated captions and flags long subtitle lines or missing accessible output.
- **Inputs/outputs:** Subtitle files/caption state; findings.
- **Tools/integrations:** Local SRT and text checks.
- **Database/dependencies:** Job files/state.
- **Decisions/example:** Flags a subtitle line that is difficult to read; it is not a full WCAG certification.
- **Admin:** Optional; may be disabled.

### 32. Localization reviewer (`localization`, Code, non-critical)
- **Purpose/responsibility:** Verify requested translated subtitle outputs.
- **Features/functions/workflow:** Checks language requests against generated SRT files and flags omissions.
- **Inputs/outputs:** Requested language list and caption outputs; completeness report.
- **Tools/integrations:** Local file/language checks.
- **Database/dependencies:** Job parameters and subtitle files.
- **Decisions/example:** Reports a requested French file missing after translation.
- **Admin:** Optional; may be disabled.

### 33. Thumbnail experiment designer (`thumbnail_experiment`, Code, non-critical)
- **Purpose/responsibility:** Record distinct thumbnail variants for later comparison.
- **Features/functions/workflow:** Packages available concepts into an experiment record without changing the selected final image.
- **Inputs/outputs:** Thumbnail candidates and job/user IDs; experiment row and variant metadata.
- **Tools/integrations:** Application DB and local metadata.
- **Database/dependencies:** `experiments`; user later enters observed metrics.
- **Decisions/example:** Save three candidate variants so an operator can track results; does not run a platform A/B test itself.
- **Admin:** Optional; may be disabled.

### 34. Content calendar planner (`calendar`, Code, non-critical)
- **Purpose/responsibility:** Record a draft calendar item for a completed video.
- **Features/functions/workflow:** Creates the draft after production; user can set a schedule in the calendar UI.
- **Inputs/outputs:** Job ID, title, account/workspace; draft calendar record.
- **Tools/integrations:** Application DB/calendar methods.
- **Database/dependencies:** `content_calendar`.
- **Decisions/example:** Completion creates a draft; scheduling is user intent and is not an automatic platform upload.
- **Admin:** Optional; may be disabled.

### 35. Rights and release assistant (`rights`, Code, non-critical)
- **Purpose/responsibility:** Flag asset ledger records lacking attribution/licence detail.
- **Features/functions/workflow:** Reviews used-asset metadata and emits missing-rights information.
- **Inputs/outputs:** Asset/license ledger; warnings and checklist context.
- **Tools/integrations:** Local DB/state only.
- **Database/dependencies:** Asset records created during selection.
- **Decisions/example:** Flags a missing source URL for the creator to resolve; it does not grant legal rights.
- **Admin:** Optional; may be disabled.

### 36. Production cost analyst (`cost`, Code, non-critical)
- **Purpose/responsibility:** Estimate production scale, provider demand and render effort.
- **Features/functions/workflow:** Uses duration/scene/provider context to estimate scenes, API requests and render time.
- **Inputs/outputs:** Validated job parameters and available provider/quota data; estimate and caution.
- **Tools/integrations:** Local formulae and quota/provider status; no payment API.
- **Database/dependencies:** Shared job state and usage counters.
- **Decisions/example:** A 30-minute Long video is estimated to need more scene/API/render work than a Short. Actual provider charges are not inferred.
- **Admin:** Optional; may be disabled.

## Collaboration and application integration

The Director orders execution. Research feeds writing; analytics/trends/audience inform the script; script checks and review gates precede shot planning; shot planning feeds footage search and picking; selected assets flow to narration/timing/render; captions, SEO, thumbnails, rights/license, QA and publishing package create deliverables. Repurpose can create child jobs; Calendar and Brand memory save results. State and events are the common integration contract. A failed optional step is visible as skipped; a critical dependency failure blocks completion.

The separate direct-publishing service (`backend/lumina/publishing.py`) is not a specialist's autonomous tool. It requires a signed-in user, connected provider account and explicit destination review/confirmation. It supports the implemented YouTube, Facebook Page, eligible Instagram Reels and TikTok flows subject to provider approval and configuration. Resend sends a ready reminder and final attempt summary when configured.

## Practical owner workflow

1. Configure Supabase and set `LUMINA_SUPER_ADMIN_USER_ID` to the owner's existing Auth UUID on the backend.
2. Sign in as that account; use Admin to inspect system health, roles, users and agents.
3. Invite a user instead of creating a password on their behalf. Grant a non-owner role only when needed.
4. Review the user/session/audit views and provider status; change secret values in Railway, not in Admin.
5. Test maintenance, optional agent toggles and backups in a staging environment before production.
6. Do not use billing entries; they are deferred until a provider and scope are separately approved.

## References and implementation limits

- Agent classes and actual registration: `backend/lumina/agents/`; metadata and live descriptions are from each registered class.
- Pipeline ordering/retry/review gates: `backend/lumina/agents/director.py`.
- Admin permissions/operations: `backend/lumina/admin.py`; route authorization: `backend/lumina/server.py`.
- Data structures: `backend/lumina/db.py`.
- Some external integration claims depend on provider application approval, account type, quotas and API changes. The implementation is not proof that the owner's live credentials have been tested.
