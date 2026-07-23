# Launch & distribution playbook

Copy/paste drafts to take cortex from "built" to "used." Order of impact:
**demo GIF → one-line install → Omarchy → launch posts.**

---

## 0. Before posting anywhere
- [ ] Record the **demo GIF** (`docs/demo.gif`) — hold F8, overlay, text pastes. Single biggest conversion lever.
- [ ] Tag a release (`git tag v0.2.0 && git push --tags`) so the prebuilt AppImage exists.
- [ ] Make sure `curl … | install.sh | bash` works on a clean machine.

---

## 1. Show HN

**Title:**
> Show HN: cortex – Wispr Flow–style voice typing for Linux, powered by Groq (free)

**Body:**
> I wanted Wispr Flow on Linux, but local dictation forces a bad trade: tiny Whisper models that are fast but inaccurate, or big ones that are accurate but slow and eat your GPU.
>
> cortex takes the other path — it transcribes with Groq's hosted Whisper `large-v3-turbo`. You get the full, accurate model in ~0.5s, with no GPU and no model downloads. Hold F8, speak, release, and it pastes into whatever app you're in (editor, terminal, an AI chat box). There's a live waveform overlay, it pauses your music while you talk, and you can say "press enter" to fire off an AI prompt hands-free.
>
> The part that surprised me: cost. Groq's free tier is ~2,000 transcriptions/day, so for one person it's effectively free; past that it's $0.04/hour of audio.
>
> It's for Wayland/Hyprland (built on Omarchy). Install is one line. Honest tradeoff: if you need offline, a local tool still wins. Feedback welcome — especially on accuracy vs. your current setup.
>
> [repo link]

*Post Tue–Thu, ~8–10am ET. Reply to every comment in the first 2 hours.*

---

## 2. Reddit — r/hyprland, r/linux, r/Omarchy, r/wayland

**Title:**
> cortex: voice dictation for Hyprland/Wayland that uses Groq Whisper — more accurate than local models, and basically free

**Body:** (lead with the GIF)
> Built a push-to-talk dictation tool for Hyprland. Hold F8 → speak → it pastes anywhere. Unlike Voxtype/nerd-dictation (which run a small local Whisper), cortex uses Groq's hosted `large-v3-turbo` — full-size model, ~0.5s, and Groq's free tier covers basically anyone.
>
> Features: live recording overlay, auto-pauses media, spoken "press enter" to submit prompts, one-shot paste. One-line install on Arch/Omarchy.
>
> Repo + 5-sec demo: [link]. Offline users: a local tool is still the right call — this is for the "I have wifi and want accuracy" case.

---

## 3. Omarchy Discord / community
> Made cortex — voice typing for Omarchy. Hold F8, it pastes into any app. Uses Groq Whisper so it's more accurate than local dictation and effectively free (Groq's free tier). One-line install, has a Waybar module + recording overlay that matches the Omarchy vibe. Would love for it to become the default dictation option — feedback/PRs welcome: [link]

---

## 4. Get into omarchy-pkgs (the beachhead)

Goal: a `cortex` (or `cortex-bin`) package in [omacom-io/omarchy-pkgs](https://github.com/omacom-io/omarchy-pkgs) so users get it via Omarchy's repo.

**PR title:** `Add cortex — Groq-powered voice dictation`

**PR body:**
> Adds `cortex`, push-to-talk voice‑to‑text for Omarchy (Hyprland/Wayland). Transcribes via Groq Whisper `large-v3-turbo` — more accurate than the local models in Voxtype/nerd-dictation, ~0.5s, and free for typical use on Groq's free tier. Ships F8 push-to-talk, a recording overlay, media-pause, and one-shot paste. Runtime deps: `ydotool playerctl gtk4-layer-shell python-gobject python-cairo webkit2gtk-4.1 jq`.

**Next step:** write a `PKGBUILD` that pulls the tagged release AppImage (`cortex-bin`) or builds from source, plus the post-install (ydotoold service, hotkey). I can draft this — it's the main remaining work for repo distribution.

---

## Messaging guardrails (don't get torched on HN)
- ✅ Claim: "full Whisper model, ~0.5s, effectively free, more accurate than a local model of the same speed."
- ❌ Don't claim: "faster than local" flat-out (there's a network floor) or "100% private" (audio goes to Groq for transcription).
- Always concede the offline case up front — it pre-empts the top comment.
