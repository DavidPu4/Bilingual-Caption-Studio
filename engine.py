"""Local speech captions and translation for the application's language registry."""
from __future__ import annotations

import threading
import time

from captions import fit_captions_to_duration, validate
from languages import default_target, language_name, validate_pair

MAX_SECONDS = 300
TRANSLATION_MODEL = 'Helsinki-NLP/opus-mt-en-zh'
MULTILINGUAL_TRANSLATION_MODEL = 'facebook/m2m100_418M'
_RESOURCE_LOCK = threading.RLock()
_TRANSLATION_RESOURCES = {}


def translation_model_for(source_language, target_language=None):
    target_language = default_target(source_language) if target_language is None else target_language
    validate_pair(source_language, target_language)
    if target_language == 'en':
        return 'Whisper speech translation'
    if (source_language, target_language) == ('en', 'zh'):
        return TRANSLATION_MODEL
    return MULTILINGUAL_TRANSLATION_MODEL


def _translation_resources(model_name):
    """Load weights once across pairs; serialize first-use loading too."""
    with _RESOURCE_LOCK:
        cached = _TRANSLATION_RESOURCES.get(model_name)
        if cached is not None:
            return cached
        import torch
        if model_name == TRANSLATION_MODEL:
            from transformers import MarianMTModel, MarianTokenizer
            tokenizer = MarianTokenizer.from_pretrained(model_name)
            model = MarianMTModel.from_pretrained(model_name).eval()
        else:
            from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer
            tokenizer = M2M100Tokenizer.from_pretrained(model_name)
            model = M2M100ForConditionalGeneration.from_pretrained(model_name).eval()
        cached = (torch, tokenizer, model, threading.RLock())
        _TRANSLATION_RESOURCES[model_name] = cached
        return cached


def load_translator(source_language='en', target_language='zh'):
    """Return a pair-bound translator; no arguments retain the established API.

    Pair closures share one cached weight/tokenizer instance. The inference lock
    covers src_lang assignment, tokenization, generation and decoding, since the
    M2M100 tokenizer's source language is mutable.
    """
    validate_pair(source_language, target_language)
    model_name = (TRANSLATION_MODEL if (source_language, target_language) == ('en', 'zh')
                  else MULTILINGUAL_TRANSLATION_MODEL)
    torch, tokenizer, model, lock = _translation_resources(model_name)

    def translate(texts):
        if not isinstance(texts, (list, tuple)) or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError('Translate a list of nonempty caption strings.')
        translated = []
        with lock:
            options = {'max_new_tokens': 512, 'num_beams': 4}
            if model_name == MULTILINGUAL_TRANSLATION_MODEL:
                tokenizer.src_lang = source_language
                options['forced_bos_token_id'] = tokenizer.get_lang_id(target_language)
            for offset in range(0, len(texts), 8):
                batch = texts[offset:offset + 8]
                inputs = tokenizer(batch, return_tensors='pt', padding=True, truncation=False)
                if inputs['input_ids'].shape[1] > model.config.max_position_embeddings:
                    raise ValueError('A caption is too long to translate. Try a shorter recording.')
                with torch.inference_mode():
                    outputs = model.generate(**inputs, **options)
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


def generate(model, audio, progress=lambda message: None, *, source_language='zh', target_language=None,
             translator=None, duration_seconds=None):
    target_language = default_target(source_language) if target_language is None else target_language
    validate_pair(source_language, target_language)
    if duration_seconds is not None:
        # Reject invalid bounds before loading or calling any model.
        fit_captions_to_duration([], duration_seconds)
    source_name, target_name = language_name(source_language), language_name(target_language)
    tracks = {}
    started = time.perf_counter()
    tasks = [(source_name, 'transcribe')]
    if target_language == 'en':
        tasks.append((target_name, 'translate'))
    for name, task in tasks:
        progress(f'Generating {name} captions...')
        segments, _ = model.transcribe(audio, language=source_language, task=task, beam_size=5,
                                      vad_filter=True, condition_on_previous_text=False)
        rows = [{'start': float(s.start), 'end': float(s.end), 'text': s.text.strip()}
                for s in segments if s.text.strip() and s.end > s.start]
        if duration_seconds is None:
            tracks[name] = validate(rows)
        else:
            tracks[name], changed = fit_captions_to_duration(rows, duration_seconds, drop_outside=True)
            if changed:
                progress(f'Adjusted {changed} {name} caption(s) to fit the media duration.')
    if target_language != 'en':
        progress(f'Translating {source_name} captions into {target_name}...')
        source_rows = tracks[source_name]
        if source_rows:
            if translator is None:
                translator = load_translator(source_language, target_language)
            translations = translator([row['text'] for row in source_rows])
            if len(translations) != len(source_rows):
                raise ValueError('The translator returned an unexpected number of captions.')
            tracks[target_name] = validate([
                {**row, 'text': text} for row, text in zip(source_rows, translations)
            ])
        else:
            tracks[target_name] = []
    return tracks, round(time.perf_counter() - started, 3)
