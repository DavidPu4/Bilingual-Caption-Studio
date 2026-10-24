"""Local inference: independent Mandarin transcription and English translation passes."""
from __future__ import annotations

import time

from captions import validate

MAX_SECONDS = 300


def load_model(size: str):
    from faster_whisper import WhisperModel
    if size not in {'tiny', 'base', 'small'}:
        raise ValueError('Choose tiny, base, or small.')
    return WhisperModel(size, device='cpu', compute_type='int8')


def decode_media(path: str):
    from faster_whisper.audio import decode_audio
    audio = decode_audio(path, sampling_rate=16000)
    duration = len(audio) / 16000
    if not duration:
        raise ValueError('The file contains no decodable audio.')
    if duration > MAX_SECONDS:
        raise ValueError(f'Please trim the recording to {MAX_SECONDS // 60} minutes or less.')
    return audio, duration


def generate(model, audio, progress=lambda message: None):
    tracks = {}
    started = time.perf_counter()
    for name, task in [('Mandarin', 'transcribe'), ('English', 'translate')]:
        progress(f'Generating {name} captions...')
        segments, _ = model.transcribe(audio, language='zh', task=task, beam_size=5,
                                      vad_filter=True, condition_on_previous_text=False)
        rows = [{'start': float(s.start), 'end': float(s.end), 'text': s.text.strip()}
                for s in segments if s.text.strip() and s.end > s.start]
        tracks[name] = validate(rows)
    return tracks, round(time.perf_counter() - started, 3)
