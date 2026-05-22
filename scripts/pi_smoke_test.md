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
ls -1 wakeword.onnx faces/idle faces/listening faces/thinking faces/speaking
python3 -c "import json; c=json.load(open('config.json')); print(c.get('wake_word'), c.get('opening_line'))"
```

Pass criteria: `wakeword.onnx` exists, all four face folders have ≥1 PNG, and `config.json`
prints the XENON-branded wake word + opening line.

## 4. Audio loopback (mic + speaker)

```bash
# 5-second mic capture
arecord -d 5 -f cd -t wav /tmp/mic.wav
aplay /tmp/mic.wav
```

Pass criteria: playback contains the recorded audio at recognizable volume. If silent or
distorted, fix audio routing before continuing (`alsamixer`, `pactl set-default-sink`).

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
2. Say the configured wake word (currently "Hey Jarvis"; XEB-13 will swap this).
3. Confirm the listening face renders.
4. Speak a visitor greeting (e.g. "I'm here to see Joao").
5. Confirm thinking → speaking faces transition, TTS plays the response, and the
   `visitor_log.jsonl` row is appended.

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
