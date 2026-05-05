# Branding & UX Swap-In Guide

Phase 4 makes the visitor-greeter's branded surfaces config-driven. Defaults
ship with the upstream "Be More Agent" stock assets so the greeter runs out of
the box; override the `branding` block in `config.json` to skin it.

```json
"branding": {
  "agent_name": "XeBop",
  "opening_line": "Hi there! Welcome. What's your name?",
  "wake_word": {
    "model_path": "./wakeword.onnx",
    "threshold": 0.5
  },
  "faces_dir": "faces",
  "voice_model": "piper/en_GB-semaine-medium.onnx"
}
```

## Wake word

- `model_path` is a path to an OpenWakeWord `.onnx` file. The default
  `./wakeword.onnx` is the stock "Hey Jarvis" model fetched by `setup.sh`.
- To brand the wake phrase, train a custom model at
  https://github.com/dscripka/openWakeWord (needs ~50–100 voice samples of the
  phrase) and drop the resulting `.onnx` somewhere on disk, then point
  `model_path` at it.
- `threshold` raises/lowers the trigger sensitivity — tune per environment.

## Face frames

`faces_dir` should contain the following subfolders, each holding a sorted
PNG sequence (frames cycle in order; `speaking` picks a random non-zero frame
each tick to lip-sync against TTS):

```
<faces_dir>/
  idle/        # ambient
  listening/   # mic active
  thinking/    # LLM in flight
  speaking/    # TTS playing
  error/       # failures
  capturing/   # camera in use
  warmup/      # boot
```

If a folder is missing or empty, the agent renders a blue fallback. Branded
art can be commissioned per state — until art is ready, drop placeholder PNGs
in the relevant folder and the loader will pick them up on next start.

## Voice

`voice_model` is a Piper `.onnx` voice. Browse the catalog at
https://github.com/rhasspy/piper#voices and place the chosen voice under
`piper/`. The opening line and all subsequent greeter speech are spoken by
this voice.

## Opening line

`opening_line` is the first sentence the greeter speaks when a visitor is
detected. Keep it under ~12 words; the rest of the conversation is driven by
`greeter/flow.py` and the persona prompt at `prompts/greeter_persona.txt`.

## What is *not* yet branded

- The custom wake-word `.onnx` itself (training pending voice samples / phrase decision).
- Bespoke face animation art per state (placeholders ship; commission when art direction lands).
- A brand-specific TTS voice (Piper stock voice in use).

These are tracked as post-pilot polish; the configuration above lets us swap
each one in without code changes once assets exist.
