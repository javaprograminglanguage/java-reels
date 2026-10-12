# Daily Reel Workflow (@javaprograminglanguage)

Two reels a day. Each run builds ONE reel (about 25 s, 1080x1920, original music, typing sounds, minimal text, no narration) and saves it as a **Metricool draft** for Ayush to approve. Nothing is published without his explicit yes.

## Fixed facts
- Metricool brandId `7360176`, network `instagram`, timezone `Asia/Kolkata` (+05:30).
- Videos live in this repo: `YYYY-MM/YYYY-MM-DD-<slug>.mp4` (date in IST; the evening reel adds `-pm`, the afternoon reel `-am`, e.g. `2026-10-12-am-streams-map.mp4`).
- Public video URL for Metricool: `https://raw.githubusercontent.com/javaprograminglanguage/java-reels/main/<path>`.
- Ledger of everything made: `reels.md` (date | slot | topic | file | status).
- Builder: `python3 tools/build_reel.py spec.json out.mp4` (see `tools/spec_example.json`).

## Steps
1. **Topic (must be NEW).** Never repeat a topic that is already on the page or in a draft:
   - read `reels.md` in this repo;
   - read `posts.md` in the `java-posts` repo (attach with add_repo, access `read`, one shallow clone) - it lists carousel topics;
   - Metricool `getScheduledPosts` (brandId 7360176, last 45 days and next 14 days, timezone Asia/Kolkata) to see captions of published, scheduled and draft posts;
   - already covered, do not repeat: == vs .equals(), equals()/hashCode(), Streams API basics, Interface vs Abstract Class, Java Records, Optional, switch expressions, OOP basics, data-structure basics, REST API basics, access modifiers.
   Pick one focused topic that fits a 20-30 s reel and has a clear before/after, a common mistake, or 3 crisp rules. Afternoon reel: beginner-friendly concept or common mistake. Evening reel: intermediate/interview-style or practical tip. Use web search for anything version-specific; never state unverified facts. Any code must be correct and compile.
2. **Write the spec** (`spec.json`) with 5-6 scenes: hook, 2-3 code scenes (old/problem vs new/fix, optional extra), a `rules` scene (3 short checks), CTA, PLUS a `cover` block for the thumbnail (see below). Limits enforced by the tool: max 9 code lines, max 34 characters per code line, 18-35 s total. Minimal text: short phrases only, no sentences longer than about 5 words on screen. Save the final spec in this repo as `specs/<same name as the mp4>.json`.
   **Thumbnail (`cover`) is MANDATORY for every reel** and the tool refuses to build without it: `kicker` (short chip, e.g. "JAVA 14+", "COMMON MISTAKE", "INTERVIEW"), `title` (1-3 big lines, sizes 110-200, one key word in orange, max about 5 words in total, written as a scroll-stopping hook or a bold claim/question), optional `snippet` (up to 4 short code lines that show the payoff) and optional `highlight` words. See `tools/spec_example.json`.
3. **Build:** `python3 tools/build_reel.py spec.json <out.mp4>`. First run with `--preview` at 3-4 times (hook, each code scene end, rules, CTA): this also writes the thumbnail `<out>_cover.png`. LOOK at every still AND the cover (Read the PNGs): fix overflow, clipped text, wrong code, empty areas; the cover must be bold, uncluttered and readable at small size. Then build the full video (it writes `<out>_cover.png` next to the mp4) and check it with ffprobe (h264 + aac, 1080x1920, 18-35 s).
4. **Caption** in Ayush's style: `<Topic hook> ☕`, one short intro line, 3-4 lines starting with ✅, `Save this for revision 📌`, a question (`... Tell me below 👇`), `Follow @javaprograminglanguage for daily Java!`, then about 12 hashtags (include #reels).
5. **Commit and push** the mp4, its `_cover.png`, the spec JSON and the updated `reels.md` to `main` of this repo (attach it with add_repo, access `push`; one shallow clone, no parallel git operations). Commit messages end with:
   `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and, if known, `Claude-Session: <session url>`.
6. **Metricool draft** via `createScheduledPost`: blogId `7360176`, provider `instagram`, `instagramData {"type":"REEL","showReelOnFeed":true,"isAiGenerated":false}`, `draft: true`, `autoPublish: false`, media = [raw URL of the mp4], **`videoThumbnailUrl` = raw URL of the `_cover.png`** (public png; always include it), one `mediaAltText` line, text = caption. If Metricool rejects the thumbnail, tell Ayush and create the draft without it rather than losing the reel. `date` with +05:30 and `publicationDate {dateTime, timezone:"Asia/Kolkata"}` = the slot time (afternoon 13:00, evening 20:30 IST). If that time is already in the past, use the next occurrence (the next day) or ask. Metricool dates cannot be in the past.
7. **Notify Ayush** (in this session): send the mp4 AND the cover png with SendUserFile (display render, caption "Reel for <slot> IST: <topic>" / "Thumbnail"), write the caption directly in the chat (not as a file), and ask one question: "Post at <time> IST? Reply yes, or give a new time / change."

## When Ayush replies in the session
- **yes**: `getScheduledPosts` to find the draft's id, then `updateScheduledPost` with `draft: false` and **`autoPublish: true` (never false: false only sends a phone notification and does NOT post by itself)**, same slot time (reuse the draft's Metricool media URL, thumbnail, text and alt text). If the slot already passed, set it 2 minutes after now. Confirm the time; set status `scheduled` in `reels.md`.
- **no / change**: edit the spec or caption, rebuild, re-push, update the draft, ask again; or move to another time he names. Keep as draft until he says yes.
- **No reply:** leave the draft. Never publish on your own.

## If something fails
Do not loop retries and do not publish. Tell Ayush in one short message what failed and what he needs to do (reconnect Metricool or GitHub, etc.). Do not try to get around network blocks (PyPI, Hugging Face and similar are blocked in this workspace; no text-to-speech is available, so reels have music and typing sounds but no narration).

## Verify it really went live
Metricool's "Published" label alone is not proof. After the slot time (and after any "post now"), call `getScheduledPosts` for that day and check the instagram provider has a `publicUrl` (an instagram.com/reel/... link). If the status says Published but there is no `publicUrl`, or the post is still Pending well after its time, tell Ayush plainly that it is NOT confirmed live and offer to reschedule with `autoPublish: true`. Never tell him a reel is live without the link. A date can never be in the past: for "post now" use 3-4 minutes from now.
