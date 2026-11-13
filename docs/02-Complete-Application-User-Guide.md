# Complete Application User Guide

## 1. What Lumina does

Lumina Studio is a web app that turns a topic into a video production package. Its fixed Director workflow researches and writes a script, plans scenes, finds real stock footage, creates voice audio and captions, renders with FFmpeg, checks outputs and assembles upload materials. It does not generate AI video or AI images. It supports Short and Long formats; video length, output and feature behavior are bounded by the current settings and provider capabilities.

The browser is the user interface. The API and renderer must remain running during production. On a hosted setup, the browser is normally Vercel or Cloudflare Pages and the Python API/renderer runs on Railway.

## 2. Sign-in and account setup

1. Open the configured frontend URL.
2. Select **Create an account** to enter full name, email, password and password confirmation. Or use **Continue with Google** / **Continue with GitHub** when the provider is enabled in Supabase.
3. Use **Sign in** with email/password for an existing account. Use **Forgot password?** for Supabase email recovery.
4. Local development may instead use the local studio token printed by `python run.py`; hosted multi-user operation should use Supabase Auth.

OAuth buttons depend on Supabase's provider settings, provider credentials, callback URLs and allowed redirects. Lumina cannot enable an OAuth provider just by showing its button.

### Profile and preferences

**Profile** stores account profile information such as name, handle, bio, website/avatar URL, channel name/niche/audience, tone, accent, call to action, locale, time zone and email preference (based on configured Supabase profile schema). **Settings > Studio preferences** includes theme, default format/language/duration, review gates, music/repurpose defaults, draft remembering, reduced motion, compact feed, email preferences and remembered job.

These are account preferences, not global Admin permissions. Users cannot make themselves Super Admin through a profile setting.

## 3. Start and manage a video

1. Enter a concrete topic. Avoid personal data and topics that require specialist factual verification.
2. Choose **Short** or **Long**, set duration, and select the narration language.
3. Under **Options**, choose script/footage review gates, background music, subtitle languages and optional repurposed Shorts for a Long video.
4. Load a saved template or save the current validated options as a template.
5. Run **Preflight** to inspect required services, renderer and storage. It is not a guarantee of later API availability.
6. Select **Create video**. Watch the live feed; it shows agent stages, progress, messages and output cards. A job continues on the backend if the tab closes.

**Videos** shows existing jobs. Open a job to review its current state, resume/retry an eligible failure, view results and download permitted deliverables. Retry resumes from saved steps; fix a missing key/provider problem first.

### Review gates

- **Script review:** edit title, headings or narration before production continues. A compliance high-risk finding may trigger this gate even when the user did not request it.
- **Footage review:** inspect scene choices, select alternatives/reorder or remove scenes where supported, then approve rendering.

Use the review result to verify accuracy, rights and suitability yourself. Automated review is not a guarantee.

## 4. Video results and publication

The result panel may include the final MP4, script, description, tags, upload kit, credits, thumbnails and subtitle files. QA may mark a result `needs_attention`; review its checks before publishing.

### Manual upload

Use the platform's own uploader, paste the provided metadata, upload thumbnails/subtitles as appropriate, answer content/disclosure questions accurately, and verify visibility/scheduling in the platform.

### Direct publishing

The Publish panel supports account connections and user-confirmed uploads for YouTube, Facebook Pages, eligible Instagram professional accounts (Reels), and TikTok, depending on provider configuration and approval. The user must review the destinations, text and visibility and explicitly confirm. Calendar scheduling is not equivalent to a platform upload and the app does not silently auto-publish.

Platform app review, permissions, quotas, media constraints, account eligibility and URL verification are controlled by each platform. A failed platform can be retried without repeating successful destinations where supported.

### Email notices

When Resend is configured, Lumina can send a “package ready” review notice and a final per-platform publishing attempt summary. The Results email setting selects the destination (otherwise the sign-in address is used). The notification outbox records retries/status. Email delivery depends on a valid Resend API key and verified sender.

## 5. Main application areas

| Area | How to use it |
|---|---|
| Live production | Track current agent, progress, results and live log |
| Videos | Reopen projects, inspect state, retry and download outputs |
| Calendar | View draft entries and set intended publication time; does not itself publish |
| Media | Upload/reuse media with licence and attribution notes; honor upload limits |
| Workspace | Create/select workspaces; owner invites editors/reviewers, inspects roster and manages invitations |
| Experiments | Record thumbnail variants and enter observed metrics manually; not an external platform A/B test |
| Settings | Local/server connection, output quality/voice/captions, channel profile, analytics import, storage and audit export; shared API keys/output configuration require an admin grant in hosted multi-user mode |
| Profile | Edit profile information/preferences stored by Supabase |
| Admin | Owner/admin-only control center; unauthorized requests are rejected by the backend |

Workspace roles (owner/editor/reviewer) are project collaboration roles. They are separate from global admin roles (member/viewer/support/operator/Super Admin).

## 6. AI agents and data processing

The Director invokes the 36 registered agents in a fixed order. They exchange results through job state, database records and generated files. Some use the configured AI provider chain; others use ordinary Python checks. Stock searches use Pexels/Pixabay/Wikimedia where configured; fetched text is treated as untrusted input. The renderer uses FFmpeg. See [the agent master guide](./01-Admin-and-AI-Agent-Master-Guide.md) for per-agent purpose, inputs, outputs, dependencies, limits and examples.

AI output can be inaccurate or incomplete. Verify facts, rights, translations, generated narration and all platform-specific settings. Imported analytics are user-provided. Do not upload sensitive information unless your deployment's data policy allows it.

## 7. Data, accounts and privacy

Supabase Auth stores identities. Application metadata uses PostgreSQL in hosted mode and SQLite locally. Generated videos, render working files, media uploads, cache, error logs and backups are on the backend's persistent filesystem. A database backup alone is not a copy of the large media files. See [database guide](./DATABASE.md) and [architecture](./PROJECT-ARCHITECTURE.md).

The application owner can disable/suspend accounts. Deleting a Supabase Auth account through Admin retains Lumina application data; it is not a complete erasure request. Use an approved data-retention process before making the application available to users.

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| Sign-in button unavailable/fails | Frontend Supabase URL/key, Auth providers, provider callback URLs and allowed redirects |
| API says unauthorized | Sign in again; confirm `VITE_API_URL` and that Supabase issuer/JWT configuration match |
| API reports origin blocked | Add the exact frontend origin to backend `LUMINA_ALLOWED_ORIGINS` |
| Preflight says model unavailable | Configure at least one supported AI provider key on the server or app Settings |
| No useful footage | Configure Pexels/Pixabay as appropriate; revise broad/abstract query |
| Job stops midway | Read the visible error, check server logs/provider quota/disk/FFmpeg, then retry |
| Video is slow | Rendering is CPU-intensive; use Draft quality and test with a Short |
| Direct publish unavailable | Check OAuth credentials, HTTPS callback URLs, app review/scopes, eligible account and encryption key |
| No emails arrive | Verify Resend API key, verified sender, destination email and notification outbox status |
| Admin button missing | Only permitted signed-in identities see it. Set the correct owner UUID in backend config and ensure it was configured before restart |
| Backup restore needed | Stop writes and use the manual recovery process; the UI does not replace a production database in place |

## 9. FAQ

**Does the calendar publish automatically?** No. It records planned time; direct publishing is an explicit separate confirmation.

**Can Lumina guarantee fact accuracy, licensing or monetization?** No. Its checks are aids. Verify sources, terms and platform requirements.

**Can I tune every agent?** No. Optional agents may be enabled/disabled by an authorized admin. Critical steps are protected; live parameter editing is not implemented.

**Is billing included?** No. Payments, subscriptions and revenue features are deferred.

**Can I deploy only to Vercel or Cloudflare?** Those can host the static web client. The Python/FFmpeg API requires a persistent service such as the configured Railway backend.

## Related documentation

- [Admin and AI agent master guide](./01-Admin-and-AI-Agent-Master-Guide.md)
- [Deployment and credentials](./03-Deployment-and-Credentials-Guide.md)
- [Security and admin permissions](./SECURITY-AND-ADMIN-PERMISSIONS.md)
- [Existing feature walkthrough](./USAGE.md)
