"""Local caption generation for Mandarin and English recordings."""
from __future__ import annotations

import time

from captions import validate

MAX_SECONDS = 300
TRANSLATION_MODEL = 'Helsinki-NLP/opus-mt-en-zh'


def load_translator():
    """Load the English-to-Chinese text model only when that direction is requested."""
    import torch
    from transformers import MarianMTModel, MarianTokenizer

    tokenizer = MarianTokenizer.from_pretrained(TRANSLATION_MODEL)
    model = MarianMTModel.from_pretrained(TRANSLATION_MODEL).eval()

    def translate(texts):
        translated = []
        for offset in range(0, len(texts), 8):
            batch = texts[offset:offset + 8]
            inputs = tokenizer(batch, return_tensors='pt', padding=True, truncation=False)
            if inputs['input_ids'].shape[1] > model.config.max_position_embeddings:
                raise ValueError('An English caption is too long to translate. Try a shorter recording.')
            with torch.inference_mode():
                outputs = model.generate(**inputs, max_new_tokens=512, num_beams=4)
            for output in outputs:
                if model.config.eos_token_id not in output[1:].tolist():
                    raise ValueError('Translation reached its length limit. Try a shorter recording.')
            translated.extend(tokenizer.batch_decode(outputs, skip_special_tokens=True))
        return translated

    return translate


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


def generate(model, audio, progress=lambda message: None, *, source_language='zh', translator=None):
    if source_language not in {'zh', 'en'}:
        raise ValueError('Source language must be zh or en.')
    tracks = {}
    started = time.perf_counter()
    tasks = [('English', 'transcribe')] if source_language == 'en' else [('Mandarin', 'transcribe'), ('English', 'translate')]
    for name, task in tasks:
        progress(f'Generating {name} captions...')
        segments, _ = model.transcribe(audio, language=source_language, task=task, beam_size=5,
                                      vad_filter=True, condition_on_previous_text=False)
        rows = [{'start': float(s.start), 'end': float(s.end), 'text': s.text.strip()}
                for s in segments if s.text.strip() and s.end > s.start]
        tracks[name] = validate(rows)
    if source_language == 'en':
        progress('Translating English captions into Mandarin...')
        source_rows = tracks['English']
        if source_rows:
            if translator is None:
                translator = load_translator()
            translations = translator([row['text'] for row in source_rows])
            if len(translations) != len(source_rows):
                raise ValueError('The translator returned an unexpected number of captions.')
            tracks['Mandarin'] = validate([
                {**row, 'text': text} for row, text in zip(source_rows, translations)
            ])
        else:
            tracks['Mandarin'] = []
    return tracks, round(time.perf_counter() - started, 3)
