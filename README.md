# CCAST — stream your session to the students' headphones

Nothing to install, for you or for them. One Python file (already on this Mac),
a Dock app, and a QR library served locally. Works offline. Audio only:
stereo, 48 kHz, Opus 320 kb/s, no speech processing, ~100 ms.

## Every class
1. Click **CCAST** in the Dock. The server starts silently and Chrome opens
   the studio page. (First time: macOS may ask to allow Python to accept
   incoming connections → Allow; Chrome asks for the microphone once → Allow —
   that is how it reads your interface.)
2. **Programme input**: pick the device carrying your mix. Meters run at once —
   peak dBFS, clip latch, "Signal present" — before anyone hears anything.
   The line under the picker says exactly what Chrome is grabbing:
   `channels 1–2 · stereo · 48 kHz · processing off`.
3. **⛶ Projector**: project the URL + QR. Students open it and press **Listen**.
   Students are anonymous; you only see how many are listening. Esc leaves projector view.
4. **Go live.** The ON AIR light comes on.

The desk while on air:
- **Talkback** — hold to talk, click to latch (or hold **T** on the keyboard).
  Your mic (pick it under *Talkback mic*) is mixed into the stream with speech
  processing; the programme ducks −12 dB while you speak. Students see
  "Teacher talking".
- **Mute** — programme off, everyone stays connected, students see "Muted".
- **Stop** — off air; the input stays armed and metering.
- **Now playing** — a line every receiver displays (topic, a reference track…).
  Later this can become a playlist / reference queue for the students.
- Click the name **CCAST** in the header to rename it; it shows on every receiver.
- Students have their own **🔇 mute** (local only) next to Listen.

The receiver is drawn as an instrument: a live oscilloscope of what the
student actually hears (two traces, envelope, scope readouts), Listen/Mute,
volume, and **Smooth** (steady 150 ms buffer — default) / **Low latency**
(~50 ms). It reconnects by itself after anything.

## Getting your mix onto channels 1–2
Chrome captures **only channels 1–2** of the device you pick. Three ways:

**A · Pro Tools Audio Bridge 2‑A** (virtual, already installed — no cables,
independent stream mix). Audio MIDI Setup → + → Create Aggregate Device: tick
Universal Audio Thunderbolt (clock) and Pro Tools Audio Bridge 2‑A. DAW audio
device = the aggregate; master to the Apollo as usual; a send / cue / second
master to the Bridge's two extra outputs. Pick "Pro Tools Audio Bridge 2‑A" here.

**B · Physical loopback on the Apollo.** Mix or cue → spare line-out pair →
cable → Mic/Line 1–2 at Line, unity. Pick "Universal Audio Thunderbolt".

**C · Interfaces with Loopback** (Scarlett, EVO, MOTU, RME): enable Loopback
onto inputs 1–2 in the control app, pick the interface.

## If someone cannot connect
- Is the server running? Click the Dock icon: "CCAST is running" = yes.
- Same Wi-Fi as this Mac; type `http://` if entering the address by hand.
- School networks may isolate clients — test in the room once. Workaround:
  a travel router / phone hotspot everyone joins, or ask IT.
- Firewall: System Settings → Network → Firewall → allow Python.
- Port busy → it picks the next free one; the host page shows the real address.
- "Live" but silent on a student machine → a "Tap to hear" button appears; or
  their volume slider.

## Files
- `CCAST.app` — launcher (keep it here, or move it and leave the folder at ~/ClassCast)
- `classcast.py` — server + both pages · `qrcode.min.js` — QR library (MIT)
- `classcast.log` — who joined/left, errors · Terminal: `python3 ~/ClassCast/classcast.py`
