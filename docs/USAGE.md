# User guide

## Making a video

1. Type a topic. Any niche works: "the history of coffee", "how volcanoes work", "5 habits of calm people".
2. Choose **Short** (up to 90 s) or **Long** (up to 30 min) and drag the length slider.
3. Pick the narration language. Under **Options** you can also:
   - review the script before it continues, and/or review the footage before rendering,
   - turn background music off,
   - add subtitle languages (for example `es, fr`),
   - (Long only) create 2 Shorts from the best moments.
4. Optionally select a saved template or save the current settings as a reusable template.
5. Press **Run preflight** to check the AI provider, FFmpeg, stock footage keys, and available disk space.
6. Press **Create video** and watch.

Need a topic? Press **Need ideas?** and type your niche. The studio reads what people search on YouTube and suggests specific topics.

## The live view

- The **left list** shows all 36 agents. A pulsing blue dot is the agent working now; green is done; yellow means it was skipped or needs attention; red failed.
- The **top card** explains in plain words how the current agent works, shows a progress bar and its latest message.
- Cards below show results as they appear: keywords, research facts, the script, the footage chosen for each scene, fact-check issues, compliance notes, subtitles, titles and thumbnails.
- **Live log** (bottom) shows every message, including which AI model answered.

Safe to close the tab: the studio keeps working. Open **Videos** to return to any video.

## Templates, projects and collaboration

- Templates save validated format, length, language, review, music, and subtitle options for reuse.
- During footage review, reorder scenes and optionally set a per-scene voice ID and pace; otherwise the project voice defaults apply.
- Open a finished project to create a lower-resolution preview, save/restore a project version, or create an expiring read-only review link.
- **Workspace** creates or selects a team workspace. Owners can invite editors or reviewers by email, inspect the member roster, remove members, and revoke pending invitations. Invitation acceptance requires a signed-in account using that email.
- **Media** stores reusable uploaded assets with licence and attribution notes. Files are limited in size; remove unused items from the library when they are no longer needed.
- **Calendar** shows draft and scheduled videos. Scheduling in Lumina records your intended publication time; it does not automatically publish a video.

## Review screens (optional)

If you chose review, the video pauses at a **gate**:

- **Script review:** edit the title, section headings and narration. A word counter shows the length. The studio stops you if the script would exceed the video length limit. The compliance agent also pauses the job here automatically when it finds a **high** monetization risk.
- **Footage review:** each scene shows its clip. Choose an alternative from the dropdown or tick *remove scene*. Press **Approve and render**.

## When it finishes

You get a result panel with:

- the **video player**,
- **title, description (with chapters and credits), tags and pinned comment** with Copy buttons,
- other title options, three **thumbnail** designs,
- a **before-you-upload checklist**,
- the **quality check** (length, resolution, loudness, subtitles, thumbnails, credits, risk),
- **download links** for every file: `final.mp4`, `script.txt`, `description.txt`, `tags.txt`, `upload_kit.md`, `credits.txt`, thumbnails, and `.srt` subtitle files.

If the quality check found a problem the status is **needs attention**: review it before publishing.

If a video stops (for example a key was missing), fix the cause and press **Retry**. Steps that finished are not repeated.

## Uploading to YouTube manually

1. YouTube Studio > **Create > Upload videos** and choose `final.mp4`.
2. Paste the title and description; add tags under *Show more*.
3. Upload a thumbnail (long videos) and the `.srt` subtitle files.
4. Answer **Altered content** honestly: the narration is a synthetic (AI) voice and the footage is real stock footage. The upload kit includes this reminder.
5. Choose whether the video is made for kids (usually **No**).
6. Publish, then add the pinned comment.

## Direct publishing (optional)

Open a completed video's **Publish this video** panel to connect eligible YouTube, Facebook Page, Instagram professional, or TikTok accounts. Review the selected destinations, title, caption, and visibility, then choose **Review and publish** and confirm. Lumina never auto-publishes a finished video.

Configure provider OAuth credentials, `LUMINA_OAUTH_ENCRYPTION_KEY`, and `LUMINA_PUBLIC_URL` on the Railway service before connecting accounts. Meta and TikTok need to fetch a time-limited HTTPS URL for the final video; TikTok also requires the URL domain to be verified with the provider. Instagram publishing uses Reels, so its current API and account duration/format limits apply. Platform app review, scopes, quotas, eligibility, and sender verification are controlled by each provider and cannot be guaranteed by Lumina.

The optional **Results email** field controls where Lumina sends (1) the video package-ready review reminder, and (2) a per-platform publishing results summary after an attempt finishes. Leave it empty to use the signed-in email. Configure `RESEND_API_KEY` and a verified `RESEND_FROM_EMAIL` on Railway. Failed destinations can be retried on their own from Publishing history; successful platforms are not repeated.

## Music, logo and your brand

- **Music:** put royalty-free tracks in `backend/assets/music/` (mood by folder name: `calm`, `dark`, `upbeat`, `epic`, `inspiring`, `news`). Add `credits.json` so licences appear in your description. See `backend/assets/music/README.md`. Without music the studio generates a soft ambient pad (a placeholder; real music sounds better).
- **Logo:** save a transparent `logo.png` in `backend/assets/brand/` for a corner watermark.
- **Brand memory:** *Settings > Your channel* stores your tone, accent colour, banned phrases and call to action. The studio also remembers past videos and clips so it avoids repeating them.
- **Analytics:** export a CSV from YouTube Studio (Analytics > Content > Export) and upload it in *Settings > Analytics*. The studio finds patterns (for example whether titles with numbers do better for you) and applies them.
- **Experiments:** thumbnail concepts are recorded for tracking. Enter views and click-through rate in *Experiments* as results become available.
- **Storage and audit:** *Settings* shows project/media storage estimates and free server disk space. Export your account-scoped audit log from the same screen.

## Subtitles in other languages

Add language codes in Options (`es`, `fr`, `de`, `pt`, `hi`, `ur`, `ar`, `ru`, `ja`, `zh`, `tr`, `id`, `it`, `ko`). The AI translates the script and writes one `.srt` per language. Upload them in YouTube Studio > Subtitles. The voice stays in the video's main language.

## Monetization: what to know

No tool can guarantee monetization. YouTube decides, and its rules change. What the studio does to help:

- real stock footage with a licence ledger and credits (no reused creators' videos),
- script originality check against the research text, written in new words,
- fact-check with sources, advertiser-friendly wording check,
- reminders to disclose the AI voice.

What you must do:

- Make videos that give real value: a clear point of view, accurate facts, a hook, a good pace. Review scripts for your first videos. Channels that look mass-produced or repetitive risk being rejected, so **vary topics and structure, and add your own insight**.
- Read the YouTube Partner Program requirements yourself. At the time of writing they are about **1,000 subscribers plus 4,000 public watch hours in 12 months** (or 10 million Shorts views in 90 days), with an earlier tier (roughly 500 subscribers, 3 public uploads in 90 days and 3,000 watch hours) that unlocks some features. Reports say the watch-hour bar for new applicants rises to 8,000 hours from February 2027. **Check YouTube's official page before planning.**
- Check each stock site's licence for your use. Pexels and Pixabay allow free commercial use under their own licences, with some restrictions (for example you cannot sell unaltered copies or imply endorsement). Wikimedia items may require credit, which the studio adds.
- Use sensitive topics (war, tragedy, health, finance) carefully. Avoid shocking imagery, which can limit ads.

## Free limits and how the studio protects them

The **Quota agent** counts requests for each service and waits when a limit is near. Searches are cached for 24 hours and downloaded clips are reused. **Settings > Usage today** shows your numbers. A 30-minute video needs roughly 60 AI requests and a few hundred stock searches; if a limit is reached the studio waits and continues instead of failing.

## Frequently asked questions

**Can it make a video about anything?** Any topic that real footage can illustrate. Abstract topics get looser matches; review the footage screen.

**Can I change the voice?** Settings > Output > Voice (for example `en-GB-RyanNeural`). List voices with `edge-tts --list-voices`. Recording your own narration is not built in yet.

**Why is a clip repeated?** Stock libraries are finite for niche topics. Use the footage review to swap it.

**Where are my files?** `backend/data/jobs/<id>/`. Use *Videos* in the app to open them.

**How do I free disk space?** Run `python run.py clean` from `backend/` to remove cached downloads and temporary render files; finished videos stay.
