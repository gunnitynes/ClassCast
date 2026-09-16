# CCAST — stream your session to the students' headphones

Nothing to install, for you or for them. One Python file (already on this Mac),
a Dock app, and a QR library served locally. Works offline. Audio only:
stereo, 48 kHz, Opus 320 kb/s, no speech processing, ~100 ms.

## Every class
1. Click **CCAST** in the Dock. The server starts silently and Chrome opens
   the studio page. (First time: macOS may ask to allow Python to accept
   incoming connections → Allow; Chrome asks for the microphone once → Allow —
   that is how it reads your interface.)
2. **Channels**: pick the device carrying your mix (add more for a cue system, see below). Meters run at once —
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

## Channels — a cue system
The studio can send up to **six stereo channels + talkback**. With one channel
it is the normal stream. With more, every student gets a **cue mixer** on the
receiver: a knob, mute and solo per channel, their own mix in their headphones.
Channels a student mutes are switched off at the source, so Wi‑Fi bandwidth
stays low (each active channel is 320 kb/s).

Chrome captures **only channels 1–2 of each device** — but it can open several
devices at once. This Mac has six virtual stereo devices installed with Pro Tools
(Audio Bridge 2‑A, 2‑B, 6, 16, 32, 64):
1. Audio MIDI Setup → **+** → Create Aggregate Device: tick your interface
   (clock source) and the Audio Bridges you want.
2. DAW audio device = the aggregate. Master to the interface as usual; route
   sends/stems (drums, vocal, click, reference…) to the Bridges' outputs.
3. In the studio, **+ Channel** for each, name it, pick its Bridge.

Other ways onto channels 1–2: a physical loopback (spare line-out → inputs
1–2), or an interface's Loopback feature (Scarlett, EVO, MOTU, RME).

## Click track — follows the DAW
Students have a native **click** (soft sidestick or shaker, accent on the 1) that
runs in time with your DAW's transport: on when they want it, off when not, and
it stops when you stop. It is synthesised on their machine and **delayed by the
measured stream latency**, so it lands on the music they hear, not ahead of it.
Timing is within ~0.5 ms of the beat grid.

Sync source is **MIDI Clock over the IAC bus** read by the studio page (Web MIDI)
— no installs, works with Live and Pro Tools alike. (Ableton Link needs a native
library, which a browser + stdlib Python cannot host; MIDI clock gives the same
result here.)
1. Audio MIDI Setup → Window → MIDI Studio → IAC Driver → **Device is online**.
2. Ableton Live: Preferences → Link/Tempo/MIDI → Output *IAC Driver Bus 1* →
   **Sync** on. Pro Tools: Setup → Peripherals → Synchronization →
   **MIDI Beat Clock** → IAC Bus 1.
3. Studio page → **Clock · MIDI in** → pick the IAC bus; set **beats / bar**.
   The transport readout shows ▶ tempo · bar.beat while the DAW plays.

## Latency
Opus runs in **10 ms frames**; the receiver's **Low lat** setting floors the
jitter buffer at 20 ms (it rises by itself if the Wi‑Fi gets jittery), **Smooth**
holds a steady 150 ms so music never warbles. The receiver header shows an
honest end-to-end estimate (`≈ 60 ms`) built from Chrome's own stats: capture,
frame, half the round trip, jitter buffer, output latency. Realistic floor on
Wi‑Fi is 40–60 ms: right for monitoring while producing, not for a drummer
playing to a click (that needs <15 ms, which a browser cannot do).

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
