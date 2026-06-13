"""Blog content for vibeclip.dev — single source of truth for every article.

SEO-first: the index and article pages are SERVER-RENDERED from this list
(see blog_render.py), so search engines get real HTML + metadata, not a
client-side JS shell. Adding a post = appending one dict here.

Every body string is trusted, hand-authored HTML (no user input) — it is
injected verbatim into the page. Keep it valid and self-contained.
All copy is English (product/marketing output is English-only).
"""

import os

# Public base URL — drives canonical/sitemap/OG tags. Override with SITE_URL on
# a self-hosted instance (defaults to the hosted site).
SITE_URL = os.getenv("SITE_URL", "https://vibeclip.dev").rstrip("/")
SITE_NAME = os.getenv("SITE_NAME", "VibeClip")
OG_IMAGE = f"{SITE_URL}/static/icon-512.png"

# Newest first. `date` is ISO (used for <time>, JSON-LD, sitemap lastmod).
POSTS = [
    {
        "slug": "vibeclip-vs-opus-clip",
        "title": "VibeClip vs. Opus Clip: An Open-Source AI Clip Editor Compared",
        "description": "Opus Clip auto-clips long videos in the cloud on a credit/"
                       "subscription model. VibeClip is the open-source, self-hostable, "
                       "bring-your-own-key alternative you edit by talking. Here's how they differ.",
        "keywords": "opus clip alternative, vibeclip vs opus clip, open source opus clip, "
                    "ai clip generator, free ai video editor, self-hosted clip maker",
        "tag": "COMPARE",
        "read_min": 8,
        "date": "2026-06-13",
        "excerpt": "Opus Clip popularised one-click auto-clipping. VibeClip takes the same "
                   "long-video-to-shorts job and makes it open source, self-hostable, and "
                   "editable by chat — on your own LLM key.",
        "body": """
<p class="lede">If you've looked for a tool that turns a long video into a stack of
short, captioned clips, you've met <strong>Opus Clip</strong>. It made one-click
auto-clipping mainstream. <strong>VibeClip</strong> tackles the same job from a different
angle: it's <strong>open source (AGPL-3.0)</strong>, you can <strong>self-host</strong> it,
and you drive the edit by <strong>talking</strong> to it on <em>your own</em> LLM key.</p>

<p>This isn't a teardown — Opus Clip is a polished product. It's a comparison of two
philosophies so you can pick the one that fits how you work.</p>

<h2>The core difference: a closed cloud service vs. an open, ownable studio</h2>
<p>Opus Clip is a hosted SaaS. You upload to their cloud, it runs on their infrastructure,
and you pay through credits and subscription tiers. That's frictionless — and it also means
your footage lives on someone else's servers and your costs scale with their pricing.</p>
<p>VibeClip is software you can actually own. Run the hosted version, or clone the repo and
<code>docker compose up</code> on your own box. When you self-host, your footage never
leaves your machine, speech-to-text and rendering run locally, and the only outbound call is
to the LLM provider <em>you</em> chose with the key <em>you</em> control.</p>

<h2>Side by side</h2>
<table>
  <thead><tr><th>&nbsp;</th><th>Opus Clip</th><th>VibeClip</th></tr></thead>
  <tbody>
    <tr><td>Model</td><td>Hosted SaaS</td><td>Open source (AGPL-3.0) + optional hosted</td></tr>
    <tr><td>Self-host</td><td>No</td><td>Yes — full studio, nothing held back</td></tr>
    <tr><td>Where your video lives</td><td>Their cloud</td><td>Your machine (when self-hosted)</td></tr>
    <tr><td>Pricing</td><td>Credits / subscription</td><td>Free to self-host; you pay your own LLM tokens (cents per short)</td></tr>
    <tr><td>How you edit</td><td>Auto-clip, then UI tweaks</td><td>Auto-clip <em>and</em> refine by chat: "cut the silences", "bigger captions"</td></tr>
    <tr><td>Control over the AI</td><td>Their models</td><td>Bring your own key — OpenAI, Claude, Gemini, local models</td></tr>
    <tr><td>Edits reversible</td><td>Yes, in-app</td><td>Yes — every change is an A/B you approve; nothing destructive</td></tr>
  </tbody>
</table>

<h2>Where VibeClip wins</h2>
<ul>
  <li><strong>Ownership &amp; privacy.</strong> Self-host and your raw footage and keys stay
  on infrastructure you control — the right call for agencies, regulated teams, and anyone
  who'd rather not upload client material to a third party.</li>
  <li><strong>Cost at scale.</strong> No per-clip credits. You pay your LLM provider directly,
  which for most creators is a few cents per short instead of a monthly clip quota.</li>
  <li><strong>Talk-to-edit refinement.</strong> Auto-clipping gets you 80% there; the last
  20% is taste. VibeClip lets you say what you want in plain words and stages a before/after,
  so you're not hunting through panels. (Here's <a href="/blog/talk-to-edit-video-editing-by-chat">how editing by chat works</a>.)</li>
  <li><strong>No lock-in.</strong> It's AGPL — fork it, extend it, run it forever.</li>
</ul>

<h2>Where a hosted SaaS still makes sense</h2>
<p>If you never want to think about infrastructure and prefer a fixed monthly bill to managing
an LLM key, a turnkey cloud tool is genuinely simpler on day one. VibeClip's hosted version
exists for exactly that — but unlike a closed product, you can always take the open-source
build and walk.</p>

<h2>The bottom line</h2>
<p>Opus Clip is a great closed product for hands-off cloud clipping. VibeClip is for creators
who want the same long-video-to-shorts speed <em>plus</em> ownership, privacy, lower running
cost, and an editor they can talk to. New to the category? Start with
<a href="/blog/what-is-an-ai-video-editor">what an AI video editor actually is</a>, or just
<a href="/signup">try VibeClip free</a> and turn your next long video into shorts.</p>
""",
    },
    {
        "slug": "open-source-vs-saas-video-editor",
        "title": "Open-Source vs. SaaS Video Editing: Why Self-Hosting Wins for Creators",
        "description": "Cloud video tools are convenient but rent-forever and upload your "
                       "footage to someone else's servers. Here's the case for a free, "
                       "open-source, self-hosted AI video editor — and when SaaS still fits.",
        "keywords": "free open source video editor, self-hosted video editor, open source "
                    "video editing, private video editor, agpl video editor, byok video editor",
        "tag": "COMPARE",
        "read_min": 7,
        "date": "2026-06-13",
        "excerpt": "Convenience vs. ownership. Why an open-source, self-hosted AI editor "
                   "beats a rented cloud tool on privacy and cost — and the cases where "
                   "SaaS is still the right call.",
        "body": """
<p class="lede">Almost every AI video tool today is a <strong>SaaS</strong>: you upload your
footage to a cloud, it processes on their servers, and you pay a recurring bill. It's
convenient — and it quietly trades away two things creators increasingly care about:
<strong>ownership</strong> and <strong>privacy</strong>. Here's the honest case for the
open-source, self-hosted alternative.</p>

<h2>What "self-hosted" actually means</h2>
<p>A self-hosted app runs on infrastructure <em>you</em> control — your laptop, your VPS, your
company server. With an open-source AI video editor like VibeClip, that means your raw video
files, your transcripts, and your renders never leave your machine. Speech-to-text and every
render run locally; the only thing that touches the network is the LLM provider you pick,
called with a key you own (that's the "bring your own key" / BYOK model).</p>

<h2>Three reasons it wins</h2>
<ul>
  <li><strong>Privacy by default.</strong> Unreleased footage, client material, internal
  recordings — none of it gets uploaded to a third party. For agencies and regulated teams
  that's not a nice-to-have, it's a requirement.</li>
  <li><strong>Cost that doesn't compound.</strong> SaaS pricing scales with <em>their</em>
  margins and your usage tier. Self-hosting is free; you pay your LLM provider directly for
  tokens — typically a few cents per short. No per-clip credits, no seat fees.</li>
  <li><strong>No lock-in, ever.</strong> Open source (VibeClip is AGPL-3.0) means you can read
  the code, fork it, extend it, and keep running it even if the company behind it disappears.
  A closed SaaS can change pricing, gate features, or shut down — and your workflow goes with it.</li>
</ul>

<h2>The convenience tax</h2>
<p>Let's be fair: SaaS is easier on day one. There's nothing to install, no key to manage, no
server to keep alive. For a creator who edits occasionally and prefers a predictable monthly
fee, that simplicity is worth real money.</p>
<p>The open-source answer to "but setup is hard" is to make setup trivial. VibeClip runs in the
browser, and self-hosting is one <code>docker compose up</code>. You add your LLM key once and
you're editing. (Curious how the editing itself feels?
<a href="/blog/talk-to-edit-video-editing-by-chat">You talk to it.</a>)</p>

<h2>When SaaS still makes sense</h2>
<p>If you have zero interest in infrastructure, edit rarely, and want one bill with support
attached, a managed product is a reasonable choice. The best of both worlds is a tool that
offers a hosted option <em>and</em> an open-source build — so you start easy and can take
ownership whenever you outgrow the cloud.</p>

<h2>How VibeClip fits</h2>
<p>VibeClip is open source under AGPL-3.0, self-hostable in one command, and BYOK — and there's
a hosted version for people who want turnkey. Same studio either way; no feature held back for
the paid tier. If you're weighing tools, here's a concrete head-to-head:
<a href="/blog/vibeclip-vs-opus-clip">VibeClip vs. Opus Clip</a>. Or just
<a href="/signup">spin it up free</a> and see how
<a href="/blog/turn-long-video-into-shorts-with-ai">one long video becomes a week of shorts</a>.</p>
""",
    },
    {
        "slug": "capcut-alternative-ai-video-editor",
        "title": "A CapCut Alternative for Creators Who Hate the Timeline",
        "description": "CapCut is powerful but it's still a manual timeline editor. If you're "
                       "repurposing long videos into shorts, a talk-to-edit AI editor like "
                       "VibeClip is faster — and it's open source. Here's the comparison.",
        "keywords": "capcut alternative, capcut for podcasts, ai video editor vs capcut, "
                    "no timeline video editor, repurpose long video, open source capcut alternative",
        "tag": "COMPARE",
        "read_min": 7,
        "date": "2026-06-13",
        "excerpt": "CapCut gives you every knob — and every knob is your job. For turning long "
                   "recordings into shorts, a talk-to-edit AI editor removes the timeline "
                   "entirely. Here's how VibeClip compares.",
        "body": """
<p class="lede"><strong>CapCut</strong> is the default mobile/desktop editor for a huge number
of creators, and for good reason — it's free to start, feature-rich, and great for hands-on,
frame-level work. But if your actual job is <em>repurposing long recordings into shorts</em>,
CapCut still asks you to do the editing. <strong>VibeClip</strong> is the alternative that does
the editing for you — you just talk to it.</p>

<h2>Two different jobs</h2>
<p>CapCut is a <strong>manual timeline editor</strong> with AI features bolted on. You import
clips, arrange them on a timeline, and control every transition, caption, and cut yourself.
That's perfect when you want total creative control over a single piece.</p>
<p>VibeClip is built for the <strong>long-video-to-shorts</strong> workflow specifically. Drop
in an hour of podcast or stream, and it finds the postable moments, reframes them to vertical,
captions them, and tightens the pacing — then you refine by describing what you want. There's
no timeline to scrub. (New to the idea?
<a href="/blog/what-is-an-ai-video-editor">Here's what an AI video editor does.</a>)</p>

<h2>Side by side</h2>
<table>
  <thead><tr><th>&nbsp;</th><th>CapCut</th><th>VibeClip</th></tr></thead>
  <tbody>
    <tr><td>Editing model</td><td>Manual timeline + AI tools</td><td>Talk-to-edit; AI does the work, you approve</td></tr>
    <tr><td>Best at</td><td>Hands-on, frame-level edits</td><td>Turning long videos into many shorts fast</td></tr>
    <tr><td>Finds clips for you</td><td>No — you scrub and cut</td><td>Yes — highlight detection proposes the moments</td></tr>
    <tr><td>Open source / self-host</td><td>No</td><td>Yes — AGPL-3.0, runs on your server</td></tr>
    <tr><td>Your footage</td><td>Processed by the app/cloud</td><td>Stays on your machine when self-hosted</td></tr>
    <tr><td>Learning curve</td><td>Real — it's a full NLE</td><td>Describe the edit in plain words</td></tr>
  </tbody>
</table>

<h2>When to reach for which</h2>
<p>Keep CapCut for one-off hero edits where you want to place every element by hand. Reach for
VibeClip when you have a backlog of long recordings and need to ship shorts on a schedule —
batching a week of content out of one upload is exactly the case timelines make slow.
(See <a href="/blog/repurpose-one-video-into-a-week-of-shorts">how to repurpose one video into a week of shorts</a>.)</p>

<h2>Why creators switch for repurposing</h2>
<ul>
  <li><strong>Speed.</strong> No timeline means no scrubbing. The bottleneck — finding and
  cutting the good moments — is what the AI handles.</li>
  <li><strong>Ownership.</strong> VibeClip is open source and self-hostable; your footage and
  your LLM key stay yours. (More on that:
  <a href="/blog/open-source-vs-saas-video-editor">open source vs. SaaS</a>.)</li>
  <li><strong>Approval, not surrender.</strong> Every AI edit is staged as a before/after you
  accept or reject — you keep creative control without the manual labour.</li>
</ul>

<h2>Try it on your next long video</h2>
<p>If timelines are the part of editing you dread, that's exactly the part VibeClip removes.
<a href="/signup">Start free</a>, drop in a long recording, and describe the shorts you want.</p>
""",
    },
    {
        "slug": "what-is-an-ai-video-editor",
        "title": "What Is an AI Video Editor? How It Turns Long Videos Into Shorts",
        "description": "An AI video editor reads your footage and does the editing for "
                       "you — captions, reframing, cuts. Here's how it works and how to "
                       "turn one long video into viral-ready shorts.",
        "keywords": "ai video editor, what is an ai video editor, ai video editing, "
                    "long video to shorts, automatic video editing",
        "tag": "GUIDE",
        "read_min": 7,
        "date": "2026-06-12",
        "excerpt": "The plain-English explanation: what an AI video editor actually does, "
                   "what it doesn't, and why it's the fastest way to turn a long recording "
                   "into a stack of shorts.",
        "body": """
<p class="lede">An <strong>AI video editor</strong> is software that understands what's
<em>inside</em> your footage — the words, the speaker, the pauses, the highlights — and
does the mechanical editing work for you. Instead of dragging clips on a timeline for
hours, you point it at a long video and it returns finished vertical shorts, captioned and
reframed, ready to post.</p>

<p>For the last decade, "video editing" meant a timeline, a playhead, and a thousand tiny
manual decisions. AI changes the unit of work: you stop editing <em>frames</em> and start
editing <em>intent</em>. You say what you want; the editor figures out the frames.</p>

<h2>What an AI video editor actually does</h2>
<p>Under the hood, a modern AI editor chains together a few specialised models. Each one
handles a job that used to be manual:</p>
<ul>
  <li><strong>Transcription.</strong> Speech-to-text turns your audio into a time-aligned
  transcript. This is the backbone — once the editor knows <em>what is said when</em>, it can
  cut, caption, and search by meaning.</li>
  <li><strong>Highlight detection.</strong> It scores the transcript to find the moments most
  likely to travel — a strong hook, a punchline, a clean takeaway — and proposes them as
  clips.</li>
  <li><strong>Smart reframe.</strong> It tracks the speaker and re-crops a 16:9 landscape
  frame into a 9:16 vertical one, keeping the face centred instead of chopping it off.</li>
  <li><strong>Captions.</strong> It burns in word-by-word subtitles that stay in sync — the
  single biggest driver of watch time on muted feeds.</li>
  <li><strong>Cleanup.</strong> Silence removal, filler-word trimming, and pacing so the clip
  feels tight, not raw.</li>
</ul>

<h2>Why "long video to shorts" is the killer use case</h2>
<p>Most creators already sit on a goldmine: podcasts, streams, webinars, lectures,
interviews. One hour of talking-head footage easily contains five to fifteen postable
moments. Finding and cutting those by hand is the bottleneck — not the talent, not the
camera. An AI video editor collapses that bottleneck from an afternoon into minutes.</p>
<p>The workflow looks like this: drop in the long file → the editor transcribes and finds
candidate clips → you pick the ones you like → it reframes, captions, and exports each as a
vertical short. One recording becomes a week of content.</p>

<h2>What it doesn't do (and shouldn't)</h2>
<p>An AI video editor isn't a replacement for taste. It can find a strong moment, but it
can't know your brand voice, your in-jokes, or the one line you personally care about. The
best tools keep you in the loop: they <em>propose</em>, you <em>approve</em>. Editing stays
reversible, and nothing ships without a human nod.</p>

<h2>How VibeClip approaches it</h2>
<p>VibeClip is an AI video editor you drive by <strong>talking</strong>. You describe the
edit in plain language — "cut the silences," "make the captions bigger," "pull the best 30
seconds" — and it stages a before/after so you approve every change. No timeline scrubbing,
no keyframes, no install. <a href="/signup">Start free</a> and turn your next long video into
shorts in a single sitting.</p>
""",
    },
    {
        "slug": "turn-long-video-into-shorts-with-ai",
        "title": "How to Turn a Long Video Into Shorts With AI (Step by Step)",
        "description": "A simple, repeatable workflow for turning one long video into "
                       "multiple vertical shorts with AI — from upload to captioned, "
                       "reframed export.",
        "keywords": "turn long video into shorts, long video to shorts ai, how to make "
                    "shorts from long videos, ai clip generator, repurpose video",
        "tag": "TUTORIAL",
        "read_min": 6,
        "date": "2026-06-11",
        "excerpt": "From a single long recording to a stack of ready-to-post vertical clips — "
                   "the exact step-by-step, and the three decisions that make or break each clip.",
        "body": """
<p class="lede">You already recorded the long thing — the podcast, the stream, the talk. The
hard part was never the recording; it's slicing it into shorts people actually watch. Here's
the step-by-step for doing it with an <strong>AI video editor</strong>, and the few judgment
calls that matter.</p>

<h2>Step 1 — Start with the right source</h2>
<p>The best raw material is <strong>talking-driven</strong> footage: interviews, podcasts,
lectures, reaction streams, tutorials. Anything where the value lives in what's <em>said</em>.
A clear voice track matters more than 4K — captions and clip detection both ride on the
transcript, so clean audio beats a pretty image.</p>

<h2>Step 2 — Let AI find the moments</h2>
<p>Upload the full video and let the editor transcribe and scan it. Instead of scrubbing an
hour looking for gold, you get a shortlist of candidate clips ranked by how likely they are to
land. Each one usually centres on a single idea: a hook, a hot take, a clean how-to.</p>
<p><strong>Tip:</strong> aim for clips that can be understood with zero context. If a moment
only makes sense if you watched the previous ten minutes, it won't survive the feed.</p>

<h2>Step 3 — Reframe to vertical (9:16)</h2>
<p>Shorts, Reels, and TikTok all live in a vertical 9:16 frame. A naïve centre-crop chops
heads off the moment someone leans or moves. Smart reframe tracks the speaker and keeps them
in frame as the crop follows them — the difference between "made for vertical" and "obviously
a chopped landscape clip."</p>

<h2>Step 4 — Add captions</h2>
<p>Most feeds autoplay muted. Word-by-word burned-in captions are the single highest-leverage
edit you can make — they routinely lift watch time because viewers can follow with the sound
off. Keep them large, high-contrast, and synced tightly to the audio.</p>

<h2>Step 5 — Tighten the pace</h2>
<p>Remove dead air, long pauses, and filler. A short should feel like it's leaning forward.
Cutting silences alone can shave 20–30% off a raw clip and noticeably improve retention.</p>

<h2>Step 6 — Review, then export</h2>
<p>Before exporting, watch the clip once at full speed. Does the first second earn the next
five? Is the payoff actually in frame? Good AI editors stage every change as a reversible
proposal, so you can tweak the hook or recut the ending before anything is final. Export in
clean 1080p vertical and you're ready to post.</p>

<h2>The three decisions that matter most</h2>
<ol>
  <li><strong>The hook.</strong> The first 1–2 seconds decide everything. Start on the most
  arresting line, not the windup.</li>
  <li><strong>The length.</strong> Shorter than you think. If it can be 22 seconds, don't make
  it 40.</li>
  <li><strong>The payoff.</strong> Every clip needs a reason it was worth watching — a laugh, a
  lesson, a "huh."</li>
</ol>

<p>VibeClip runs all of this from a single chat box: describe what you want and approve each
result. <a href="/signup">Try it free</a> on your next long recording.</p>
""",
    },
    {
        "slug": "talk-to-edit-video-editing-by-chat",
        "title": "Talk-to-Edit: Editing Video by Chat Instead of a Timeline",
        "description": "Talk-to-edit lets you edit video with plain-language instructions "
                       "instead of a timeline. Here's what changes, where it shines, and "
                       "where a sentence still beats a mouse.",
        "keywords": "talk to edit, chat video editor, edit video with text, natural language "
                    "video editing, ai video editor chat",
        "tag": "CRAFT",
        "read_min": 6,
        "date": "2026-06-10",
        "excerpt": "What happens when the edit bay listens instead of waiting for keyframes — "
                   "and the surprising places a single sentence outperforms a timeline.",
        "body": """
<p class="lede">The timeline has run video editing since the 1990s: a horizontal strip, a
playhead, and you, nudging clips frame by frame. <strong>Talk-to-edit</strong> proposes a
different contract — you say what you want in plain words, and the editor does the
frame-level work. It's less "operate the software" and more "direct the edit."</p>

<h2>What actually changes</h2>
<p>With a timeline, the gap between <em>knowing</em> what you want and <em>achieving</em> it
is full of micro-skills: ripple deletes, keyframes, track management, export presets. Each is
learnable, but each is friction. Talk-to-edit removes the friction layer:</p>
<ul>
  <li>"Cut the first 20 seconds" instead of dragging the in-point.</li>
  <li>"Add captions and make them bigger" instead of opening a title tool.</li>
  <li>"Pull the best 30 seconds" instead of scrubbing an hour.</li>
  <li>"Make it punchier" instead of manually trimming every pause.</li>
</ul>
<p>The intent was always in your head. Talk-to-edit just lets you express it directly.</p>

<h2>Where it shines</h2>
<p><strong>Repetitive, describable edits.</strong> Captions, silence removal, reframing,
pacing, format conversion — anything you could explain to an assistant in a sentence is faster
spoken than clicked. For turning long videos into shorts, where you repeat the same handful of
operations across many clips, the speed-up is dramatic.</p>
<p><strong>Beginners.</strong> There's no UI to learn. The first edit a new user makes is as
fast as their hundredth, because the interface is language they already speak.</p>

<h2>Where a sentence still loses to a mouse</h2>
<p>Talk-to-edit isn't magic, and good tools are honest about it. Frame-perfect creative work —
a precise music-synced cut, a hand-tuned motion graphic, color grading by eye — is still
faster and better with direct manipulation. The right model is <strong>hybrid</strong>: talk
for the 90% that's describable, reach for fine controls on the 10% that isn't.</p>

<h2>The trust problem — and the fix</h2>
<p>The obvious worry: if the AI edits, how do I know it did what I meant? The answer is
<strong>proposal, not autopilot</strong>. Every instruction should produce a staged change you
can see — a before/after — that you approve or reject. Nothing destructive, full history,
always reversible. That's what makes talk-to-edit trustworthy instead of a gamble.</p>

<p>VibeClip is built entirely around this: you edit by chatting, in plain language, and approve
every result before it sticks. <a href="/signup">Start free</a> and edit your next clip by
talking to it.</p>
""",
    },
    {
        "slug": "auto-captions-smart-reframe-silence-removal",
        "title": "Auto Captions, Smart Reframe & Silence Removal: The AI Editing Stack",
        "description": "The three AI edits that turn raw footage into scroll-stopping shorts — "
                       "auto captions, smart reframe, and silence removal — explained simply.",
        "keywords": "auto captions, smart reframe, silence removal, ai captions video, "
                    "vertical video reframe, ai video editing features",
        "tag": "EXPLAINER",
        "read_min": 5,
        "date": "2026-06-09",
        "excerpt": "Three edits do most of the heavy lifting on every viral short. Here's what "
                   "each one is, why it works, and how AI does it automatically.",
        "body": """
<p class="lede">Strip a viral short down to its mechanics and you'll find the same three edits
almost every time: <strong>captions</strong>, <strong>a vertical reframe</strong>, and
<strong>tight pacing</strong>. Each used to be manual. An <strong>AI video editor</strong> does
all three automatically. Here's what they are and why they matter.</p>

<h2>1. Auto captions</h2>
<p>Most social video is watched on mute — in bed, on transit, in a meeting. Burned-in,
word-by-word captions let viewers follow with zero sound, which is why captioned clips
consistently hold attention longer than bare ones.</p>
<p><strong>How AI does it:</strong> speech-to-text transcribes the audio with per-word
timestamps, then renders each word on screen exactly when it's spoken. The result is the
karaoke-style caption you see on nearly every successful short — and because it's driven by the
transcript, it stays in sync even after you cut. Good editors treat captions as
<em>sacred</em>: cuts and speed changes must never knock them out of alignment.</p>

<h2>2. Smart reframe (16:9 → 9:16)</h2>
<p>Your camera shot landscape. The feed wants vertical. A dumb centre-crop loses everyone who
isn't standing dead-centre — and chops heads the instant someone moves.</p>
<p><strong>How AI does it:</strong> the editor detects and tracks the speaker, then moves the
9:16 crop window to follow them through the shot. Two people talking? It can cut between them.
The output looks <em>shot</em> for vertical, not awkwardly cropped from something else — the
detail that separates pro-looking shorts from obvious repurposes.</p>

<h2>3. Silence removal & pacing</h2>
<p>Raw speech is full of pauses, "um"s, and dead air. On a short, every dead second is an exit
ramp — a reason to scroll on.</p>
<p><strong>How AI does it:</strong> using the same word-timed transcript, the editor finds gaps
and filler and trims them, tightening the clip without making it sound chopped. Removing
silence alone often cuts 20–30% of runtime and visibly improves retention — the clip leans
forward instead of dragging.</p>

<h2>Why they compound</h2>
<p>None of these is impressive alone. Together they're the whole game: captions keep muted
viewers in, reframing makes it feel native, and tight pacing stops the scroll. Stacked and
automated, they turn a raw 45-minute recording into a clean vertical short in minutes instead
of an afternoon.</p>

<p>VibeClip runs the full stack from one chat box — captions, reframe, and silence removal on
request, each staged for your approval. <a href="/signup">Try it free</a>.</p>
""",
    },
    {
        "slug": "repurpose-one-video-into-a-week-of-shorts",
        "title": "Repurpose One Long Video Into a Week of Shorts",
        "description": "A repeatable system for turning a single long recording into a week of "
                       "vertical shorts — without burning out or running out of ideas.",
        "keywords": "repurpose video, content repurposing, one video into many shorts, "
                    "batch create shorts, content workflow creators",
        "tag": "GROWTH",
        "read_min": 6,
        "date": "2026-06-08",
        "excerpt": "Posting daily doesn't mean filming daily. The batching system that turns one "
                   "recording into seven posts — and keeps the well from running dry.",
        "body": """
<p class="lede">The creators who post every day are almost never filming every day. They're
<strong>repurposing</strong> — turning one long recording into many short ones. Here's the
system, and why it's the most underrated growth move for anyone with a back catalog.</p>

<h2>The math that changes everything</h2>
<p>One hour of good talking-head footage holds, conservatively, five to ten standalone
moments. Record once a week and you've got enough raw material to post a short every single
day — without ever pointing a camera at yourself a second time. The constraint was never ideas;
it was the hours it took to find and cut them. An <strong>AI video editor</strong> removes that
constraint.</p>

<h2>The weekly batching system</h2>
<ol>
  <li><strong>Record one anchor piece.</strong> A podcast, a livestream, a long-form talk —
  anything 30–90 minutes where you're genuinely interesting.</li>
  <li><strong>Mine it for moments.</strong> Run it through an AI editor and collect every
  candidate clip. Don't self-edit yet — gather first.</li>
  <li><strong>Pick seven.</strong> Choose the strongest standalone moments. Each must work with
  zero context.</li>
  <li><strong>Cut, caption, reframe in one sitting.</strong> Batch the same operations across
  all seven. This is where AI saves hours — the work is identical per clip.</li>
  <li><strong>Schedule the week.</strong> One clip a day. Now you're "posting daily" off a
  single afternoon of work.</li>
</ol>

<h2>Keeping the well from running dry</h2>
<p>Repurposing fails when every clip feels the same. Vary the <em>angle</em>, not just the
timestamp: pull a teaching moment, a hot take, a behind-the-scenes aside, a one-liner, a
story. The same recording can feed five different content "lanes" if you look for them.</p>

<h2>Cross-post, don't clone</h2>
<p>The same vertical short fits Shorts, Reels, and TikTok — but tweak the framing per platform:
a slightly different hook, a caption tuned to each audience. Same core clip, native feel
everywhere.</p>

<h2>The payoff</h2>
<p>Repurposing turns volume from a grind into a system. You film when you're at your best,
then spend a focused session converting that into a week of posts. Consistency stops depending
on motivation and starts depending on a workflow — which is the only way it survives long
enough to compound.</p>

<p>VibeClip is built for exactly this loop: drop in the long video, pull the clips, caption and
reframe them by chat, export the set. <a href="/signup">Start free</a> and turn your back
catalog into a content engine.</p>
""",
    },
]

POSTS_BY_SLUG = {p["slug"]: p for p in POSTS}


def get_post(slug: str):
    return POSTS_BY_SLUG.get(slug)
