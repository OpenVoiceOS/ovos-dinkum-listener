"""Measure false activations and recall through the real voice loop.

The audio runs through ``DinkumVoiceLoop`` (with the ovoscope ``MiniVoiceLoop``
harness) with a real wake-word engine, a real VAD and real verifiers. The loop's
clock is replaced by an audio clock, so the pre-wake VAD window counts audio
time, as on a device. Every listed setup is measured:

    baseline                      wake-word engine only
    pre_wake_vad                  the engine runs only after the VAD hears speech
    verifiers                     every --verifier checks each detection
    pre_wake_vad+verifiers        both

Negatives are joined into one stream; each detection in it is a false
activation. Each positive is played in a new loop after --lead-silence seconds
of white noise at --noise-floor-dbfs, once for each of --lead-phases offsets
that together span one microphone chunk; a positive counts as detected when the
loop starts a recording. The offsets matter because the VAD and the wake word
engine see a recording cut at a different place in each chunk, so a single lead
shows only one of the cuts.

Example:
    python measure_false_activations.py \
        --module ovos-ww-plugin-wakeforge \
        --ww-config '{"model": "wakehubert_hey_floyd"}' \
        --vad ovos-vad-plugin-silero --verifier ovos-ww-verifier-silero \
        --positives hey_floyd/ --negatives noise/ --out results.json
"""
import argparse
import json
import time
import types
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

import ovos_dinkum_listener.voice_loop.voice_loop as vl_mod
from ovos_plugin_manager.vad import load_vad_plugin
from ovos_plugin_manager.wakewords import (
    load_wake_word_plugin,
    load_wake_word_verifier_plugin,
)
from ovoscope.voice_loop import MiniVoiceLoop, MockFileMicrophone
from ovos_dinkum_listener.voice_loop.hotwords import HotwordState

SR = 16000
class AudioClock:
    """Stands in for the ``time`` module inside the voice loop."""

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, _):
        pass


def load_pcm(path: Path) -> np.ndarray:
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    data = data.mean(axis=1)
    if sr != SR:
        g = gcd(sr, SR)
        data = resample_poly(data, SR // g, sr // g)
    return (np.clip(data, -1, 1) * 32767).astype(np.int16)


def audio_files(folders):
    return [p for d in folders for p in sorted(Path(d).rglob("*"))
            if p.suffix in (".wav", ".flac")]


def recordings_started(args, prewake: bool, verify: bool, pcm: np.ndarray,
                       clock: AudioClock) -> int:
    engine = load_wake_word_plugin(args.module)(
        "my_wake_word", config=dict(args.ww_config))
    verifiers = [load_wake_word_verifier_plugin(v)(config={})
                 for v in args.verifier] if verify else []
    harness = MiniVoiceLoop(ww_instances={"my_wake_word": engine}, verifiers=verifiers,
                            vad_instance=load_vad_plugin(args.vad)(config={}))
    harness.voice_loop.vad_pre_wake_enabled = prewake
    # The service creates its hotword container in this state.
    harness.voice_loop.hotwords.state = HotwordState.HOTWORD

    # A microphone delivers whole chunks.
    pcm = np.pad(pcm, (0, -len(pcm) % (args.chunk_bytes // 2)))
    mic = MockFileMicrophone(pcm.astype("<i2").tobytes(),
                             chunk_size=args.chunk_bytes, silence_chunks=25)
    read_chunk = mic.read_chunk

    def timed_read_chunk():
        clock.now += args.chunk_bytes / 2 / SR
        return read_chunk()

    mic.read_chunk = timed_read_chunk
    mic.on_exhausted = harness.voice_loop.stop
    harness.voice_loop.mic = mic
    harness.voice_loop.start()
    harness.voice_loop.run()
    return sum(m.msg_type == "recognizer_loop:record_begin" for m in harness._messages)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--module", required=True,
                    help="wake-word plugin, e.g. ovos-ww-plugin-wakeforge")
    ap.add_argument("--ww-config", type=json.loads, default={},
                    help="hotword config as JSON")
    ap.add_argument("--vad", default="ovos-vad-plugin-silero")
    ap.add_argument("--verifier", action="append", default=[],
                    help="verifier plugin; repeat for more")
    ap.add_argument("--positives", required=True, help="folder of wake-word clips")
    ap.add_argument("--negatives", nargs="+", required=True,
                    help="folders of audio with no wake word")
    ap.add_argument("--lead-silence", type=float, default=3.0,
                    help="seconds of room noise before each positive")
    ap.add_argument("--lead-phases", type=int, default=8,
                    help="offsets of the lead, evenly spread over one chunk")
    ap.add_argument("--noise-floor-dbfs", type=float, default=-60.0,
                    help="level of that room noise, white noise in dBFS")
    ap.add_argument("--chunk-bytes", type=int, default=4096,
                    help="microphone chunk in bytes of 16 kHz 16-bit mono; "
                         "4096 (128 ms) is the device default")
    ap.add_argument("--setups", nargs="+",
                    choices=["baseline", "pre_wake_vad", "verifiers",
                             "pre_wake_vad+verifiers"],
                    help="measure only these setups; default is every setup")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    clock = AudioClock()
    vl_mod.time = types.SimpleNamespace(
        time=clock.time, sleep=clock.sleep, monotonic=clock.time)

    positives = audio_files([args.positives])
    negatives = np.concatenate([load_pcm(p) for p in audio_files(args.negatives)])
    hours = len(negatives) / SR / 3600
    # A microphone always hears a noise floor; digital zeros would starve the
    # verifiers of signal.
    rng = np.random.default_rng(0)
    floor = 32767 * 10 ** (args.noise_floor_dbfs / 20)
    chunk_samples = args.chunk_bytes // 2
    lead_samples = [int(args.lead_silence * SR) + k * chunk_samples // args.lead_phases
                    for k in range(args.lead_phases)]
    noise = (rng.standard_normal(max(lead_samples)) * floor).astype(np.int16)
    setups = [("baseline", False, False), ("pre_wake_vad", True, False)]
    if args.verifier:
        setups += [("verifiers", False, True), ("pre_wake_vad+verifiers", True, True)]

    if args.setups:
        setups = [s for s in setups if s[0] in args.setups]

    results = {"module": args.module, "ww_config": args.ww_config, "vad": args.vad,
               "verifiers": args.verifier, "chunk_bytes": args.chunk_bytes,
               "noise_floor_dbfs": args.noise_floor_dbfs,
               "lead_phases": args.lead_phases,
               "positives": len(positives),
               "negative_hours": round(hours, 3), "setups": {}}
    for name, prewake, verify in setups:
        start = time.time()
        false = recordings_started(args, prewake, verify, negatives, clock)
        clips = [load_pcm(p) for p in positives]
        hits = sum(
            recordings_started(args, prewake, verify,
                               np.concatenate([noise[:n], clip]), clock) > 0
            for clip in clips for n in lead_samples)
        results["setups"][name] = {
            "false_activations": false,
            "false_activations_per_hour": round(false / hours, 2),
            "recall": round(hits / (len(clips) * len(lead_samples)), 4),
            "wall_seconds": round(time.time() - start, 1),
        }
        print(name, results["setups"][name], flush=True)
    Path(args.out).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
