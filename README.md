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

**Visual metronome**: the beat dot on the receiver panel blinks in time; the
**Visual** button opens a fullscreen metronome (Esc leaves) in three modes —
**Simple** (whole screen flashes, downbeat green), **Moderate** (big beat
circle, beat dots, tempo, bar.beat), **Complex** (adds a pendulum, 16th
subdivisions, and a readout of tempo, position, next bar, beat length, click
alignment, clock link, stream state, now playing). Keys 1/2/3 switch modes.
Same phase-locked, latency-compensated grid as the audio click, works with the
click on or off.

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
Three receiver settings (the **buffer** switch):
- **Smooth** — WebRTC/Opus, steady 150 ms jitter buffer. Music never warbles.
- **Min** — WebRTC/Opus at its floor: 10 ms frames, jitter target 0 (NetEq holds only
  what the network needs), raw device tracks from the studio (no mixer in the path),
  direct media-element playout. ≈ 55–70 ms on a real Mac.
- **Ultra** — a different transport: raw 16‑bit PCM in 5.3 ms blocks over a WebRTC
  data channel, played by an audio-thread worklet with our own ~10 ms adaptive jitter
  buffer and drift-correcting resampler. No codec, no NetEq, no browser audio-output
  stage. ≈ 35–45 ms on a real Mac. ~1.5 Mb/s per student (fine on a classroom LAN).
  Talkback and the multi-channel cue mixer stay on Opus; Ultra carries channel 1.

**The https "ultra link"** (shown on the studio next to the normal address, also as a
small QR, e.g. `https://192.168.2.103:8443`): browsers only allow the audio-thread
worklet on secure pages, so over plain http Ultra falls back to a main-thread path
(~10 ms slower, marked "(http)" in the readout). Choosing **Ultra** on the normal page
takes the student there automatically; the link uses CCAST's own certificate, so
Chrome shows a warning once per device — **Advanced → Proceed** — and the receiver
opens already set to Ultra.

The header estimate (`≈ 40 ms end-to-end · ultra pcm`) is built from Chrome's own
measured numbers plus our buffer fill. The stream volume knob does not touch the
click level (click has its own knob); local mute silences both.

In the room: teacher's Mac on Ethernet, students on 5 GHz Wi‑Fi, wired headphones
(Bluetooth adds 100–200 ms; the receiver warns). To measure for real: play a click
from the DAW, cable a student's headphone out into a spare interface input, record
both and read the offset. A drummer tracking to a click needs <15 ms — no browser
path gets there.

## One studio tab
The studio runs in exactly one browser tab. If a second one is opened (e.g. the
app relaunched and opened a fresh tab), the older tab steps down and says so —
close it. Students always talk to the newest studio tab.

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
