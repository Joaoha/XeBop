# Training the `Hey XeBop` wake-word model

This is the recipe for producing `wakeword.onnx` once we have office voice samples. Two paths — pick one.

## Inputs

- `wakeword_samples/<speaker>/*.wav` — 2 s, 16 kHz, mono. Collect with `scripts/collect_wakeword_samples.py`. Target: 50–100 samples across 3–5 speakers, recorded near the greeter's mic.
- A negative-data clip pool (background office audio, unrelated speech). OpenWakeWord ships with one; for office acoustics, also record ~30 min of ambient room audio and include it.

## Path A — Local training (free, slow)

Follow the OpenWakeWord training notebook: <https://github.com/dscripka/openWakeWord>.

```bash
git clone https://github.com/dscripka/openWakeWord
cd openWakeWord
pip install -r requirements.txt
# follow notebooks/training_models.ipynb, pointing at our wakeword_samples/ dir
```

Output: a `.onnx` (and a `.tflite`). Drop the `.onnx` into the XeBop repo root and point `branding.wake_word.model_path` at it. Tune `branding.wake_word.threshold` (start 0.5).

## Path B — Hosted service (paid, fast)

Picovoice Porcupine, Sensory, or similar offer turnkey custom wake-word training. Estimated cost: USD 200–500 one-time per phrase + per-device licensing, depending on vendor. Needs board approval before purchase. Output is vendor-specific; if not OpenWakeWord-compatible `.onnx`, the runtime adapter in `agent.py` will need a small pluggable backend change.

## Verifying

After dropping the new model in:

```bash
./start_agent.sh  # or python agent.py
# from across the room: "Hey XeBop"
```

Acceptance: triggers reliably (≥ 9/10) at conversational volume from 2–3 m away in the actual office, with no more than ~1 false trigger per hour during normal background chatter. If false-trigger rate is high, raise `threshold`; if miss rate is high, lower it or collect more samples and retrain.
