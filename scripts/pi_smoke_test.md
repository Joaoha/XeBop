# Pi Bench Smoke Test — XEB-5

Step-by-step runbook to validate the XeBop greeter end-to-end on Raspberry Pi 5 hardware.
Run on a freshly flashed Pi 5 (Raspberry Pi OS 64-bit, Bookworm). Mic, speaker, LCD, and
camera module attached. Network reachable.

Capture every deviation from these steps in a comment on XEB-5.

## 0. Pre-flight (board / human at bench)

- Flash microSD with Raspberry Pi OS 64-bit (Bookworm, desktop image).
- First boot: set hostname (suggest `xebop-pi`), enable SSH, connect to WiFi, set locale + timezone.
- Confirm:
  - `ssh xebop-pi.local` reaches a shell from the dev workstation
  - `arecord -l` lists the USB mic
  - `aplay -l` lists the speaker output
  - `libcamera-hello --list-cameras` lists the camera module
  - LCD displays the desktop (DSI or HDMI)
- Report hostname, SSH user, network details on XEB-5 once ready.

## 1. Repo + dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git
git clone https://github.com/JoaoHa/XeBop.git ~/XeBop  # adjust remote once known
cd ~/XeBop
chmod +x setup.sh
./setup.sh
```

Pass criteria: `setup.sh` finishes with `Setup Complete!`. Note any apt or pip failures —
common Pi 5 hits are `portaudio19-dev` already present (harmless) and Piper architecture
warning on non-aarch64 (should not fire on a real Pi).

## 2. Ollama models

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma3:1b
ollama pull moondream
ollama list   # confirm both present
```

Pass criteria: `ollama list` shows `gemma3:1b` and `moondream`.

## 3. Wake-word + branded assets sanity

```bash
ls -1 faces/idle faces/listening faces/thinking faces/speaking
python3 -c "import json; b=json.load(open('config.json'))['branding']; print(b['wake_word'], b['opening_line'])"
ls -1 "$(python3 -c "import json;print(json.load(open('config.json'))['branding']['wake_word']['model_path'])")"
```

Pass criteria: the wake-word model named by `branding.wake_word.model_path` exists (a
trained model like `HeyXebop.onnx`, or the `wakeword.onnx` default `setup.sh` downloads),
all four face folders have ≥1 PNG, and `config.json` prints the XENON-branded wake word +
opening line.

## 4. Audio loopback (mic + speaker)

```bash
# 5-second mic capture
arecord -d 5 -f cd -t wav /tmp/mic.wav
aplay /tmp/mic.wav
```

Pass criteria: playback contains the recorded audio at recognizable volume. If silent or
distorted, fix audio routing before continuing (`alsamixer`, `pactl set-default-sink`).

> If `arecord` on the default device fails with `capture slave is not defined`, or the
> agent's wake-word stream later dies with `PaErrorCode -9999`, you're on the ReSpeaker
> rig — do Section 4a first.

## 4a. USB audio device setup (ReSpeaker 4-Mic Array + USB speaker)

The bench rig uses a **ReSpeaker 4-Mic Array (UAC1.0)** for input and a **USB DAC
(UACDemoV1.0)** for output. Two things bite a fresh Pi:

- The ReSpeaker is a **6-channel, 16 kHz-only** device. Python `sounddevice`/PortAudio
  opens the *raw* `hw` device and asks for mono, which ALSA won't downmix — the agent's
  wake-word stream dies with `PaAlsaStreamComponent_RegisterChannels ... [PaErrorCode -9999]`.
- There is often no working ALSA **default capture** (`arecord` on the default device
  reports `capture slave is not defined`).

Fix once per device: route the default through `plughw` (which converts channels + rate)
by creating `~/.asoundrc` **as the same user the agent runs as**:

```bash
cat > ~/.asoundrc <<'EOF'
pcm.!default {
    type asym
    playback.pcm "plughw:CARD=UACDemoV10,DEV=0"
    capture.pcm  "plughw:CARD=ArrayUAC10,DEV=0"
}
ctl.!default {
    type hw
    card ArrayUAC10
}
EOF
```

Replace the card names with yours from `arecord -l` / `aplay -l` (here capture card
`ArrayUAC10`, playback card `UACDemoV10`). Then in `config.json`:

- `input_sample_rate`: `16000` — the ReSpeaker's native rate (44100 silently fails)
- `input_device`: `"default"` (or `null`; both now resolve to the plug)
- `aplay_device`: `"plughw:CARD=UACDemoV10,DEV=0"` — TTS plays via `aplay`, not sounddevice
- `output_device`: `null` — sound effects use the plug default

Verify:

```bash
# default capture now works (no -D needed)
arecord -d 3 -f S16_LE -r 16000 -c 1 /tmp/t.wav && aplay -D plughw:CARD=UACDemoV10,DEV=0 /tmp/t.wav
# PortAudio now lists a 'default' device and gets real levels (peak > 0.1 while speaking)
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

Pass criteria: the `arecord`/`aplay` round-trip is audible, `sd.query_devices()` shows a
`default` entry, and (Section 6) the wake-word stream opens with
`[AUDIO] Listening with rate 16000` and no `PaErrorCode -9999`.

## 5. Dry-run (no audio, no display) — fast smoke

```bash
source venv/bin/activate
python dry_run.py
```

Pass criteria: script exits 0 and logs at least one synthesized greeter line to stdout.
This isolates the LLM + prompt pipeline from audio/video hardware.

## 6. Full agent — golden path

```bash
source venv/bin/activate
python agent.py
```

Then at the Pi:

1. Wait for the idle face on the LCD.
2. Say the configured wake word — whatever `branding.wake_word.phrase` is and the
   `model_path` model was trained on (e.g. "Hey XeBop"). Watch the console: `[Oww] Score`
   should climb and `[WAKE] Triggered` fire.
3. Confirm the listening face renders.
4. Give your name, then a host (e.g. "I'm here to see Joao").
5. Confirm thinking → speaking faces transition, TTS plays the response, the host is
   notified, and a `check_in` row is appended to `visitor_log.jsonl` (with a photo under
   `visitor_photos/` if `capture_photo` is on).
6. Later, say the wake word and "I'm leaving" → give your name → confirm a `check_out`
   row is appended and the host gets an exit notification.

Pass criteria: every face state renders, TTS audible, visitor log row written with the
expected fields (visitor name, host, timestamp, notification status).

## 7. Vision check

With the agent running, prompt: "Can you see anything in front of you?" The agent should
trigger the Moondream path and describe the scene. Cover the camera and repeat — the
description should change.

Pass criteria: two distinct scene descriptions, no Moondream timeout.

## 8. Notifier smoke

Pick one notifier configured in `employees.json` (email or Teams) and run a real visit
flow targeting that host. Confirm the notification arrives within ~30s. If using a
test-mode adapter, verify the adapter logs the outbound payload.

## 9. Report back on XEB-5

Comment on XEB-5 with:

- Pass/fail per section above
- Any deviation from upstream README (apt packages, env tweaks, config edits)
- `uname -a`, `cat /etc/os-release`, `ollama --version`, `python --version`
- Timing notes: cold-boot to idle face, wake-word latency, end-to-end response time
- Photos/video of the LCD if possible

Once all sections pass, XEB-5 is `done` and XEB-9 (pilot deployment) unblocks.
