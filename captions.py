"""Caption validation, portable project files, subtitle export, and CER scoring."""
from __future__ import annotations

import json
import math
import unicodedata

from languages import default_target, language_name, validate_pair


def validate(rows: list[dict]) -> list[dict]:
    clean = []
    previous_end = 0.0
    for index, row in enumerate(rows, 1):
        try:
            start, end = float(row['start']), float(row['end'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'Caption {index}: enter numeric start and end times.') from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f'Caption {index}: end must be greater than start; times must be finite and nonnegative.')
        if start < previous_end:
            raise ValueError(f'Caption {index}: captions must be ordered and cannot overlap.')
        # Validate at millisecond precision, which is what SRT and VTT retain.
        if round(end * 1000) <= round(start * 1000):
            raise ValueError(f'Caption {index}: duration must be at least one millisecond after rounding.')
        value = row.get('text')
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'Caption {index}: text cannot be empty.')
        text = '\n'.join(line.strip() for line in value.strip().splitlines() if line.strip())
        if '-->' in text:
            raise ValueError(f'Caption {index}: text cannot contain the subtitle timing marker -->.')
        clean.append({'start': start, 'end': end, 'text': text})
        previous_end = end
    return clean


def fit_captions_to_duration(rows: list[dict], duration: float, *, drop_outside: bool = False) -> tuple[list[dict], int]:
    """Fit cue ends to the media, preserving text and starts.

    Manual edits raise when a cue cannot fit. Generated tracks may explicitly
    discard hallucinated cues that begin outside the usable media timeline.
    """
    try:
        seconds = float(duration)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Media duration must be a finite positive number of seconds.') from exc
    if isinstance(duration, bool) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('Media duration must be a finite positive number of seconds.')
    clean = validate(rows)
    # SRT/VTT round to milliseconds. Flooring keeps their final timestamp inside
    # the video even when audio and video stream lengths differ slightly.
    limit_ms = math.floor(seconds * 1000)
    limit = limit_ms / 1000
    fitted, changed = [], 0
    for index, row in enumerate(clean, 1):
        if row['start'] >= limit or limit_ms - row['start'] * 1000 < 1 - 1e-9:
            if not drop_outside:
                raise ValueError(
                    f'Caption {index} starts too close to or after the media end ({limit:.3f}s). '
                    'Move its start earlier or remove the caption.'
                )
            changed += 1
            continue
        if row['end'] > limit:
            row = {**row, 'end': limit}
            changed += 1
        fitted.append(row)
    return fitted, changed


def timestamp(seconds: float, separator: str = ',') -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f'{hours:02}:{minutes:02}:{secs:02}{separator}{millis:03}'


def subtitles(rows: list[dict], format: str = 'srt') -> str:
    if format not in {'srt', 'vtt'}:
        raise ValueError('Format must be srt or vtt.')
    rows = validate(rows)
    separator = '.' if format == 'vtt' else ','
    blocks = []
    for number, row in enumerate(rows, 1):
        text = row['text']
        if format == 'vtt':
            # WebVTT parses markup, unlike plain text. Preserve literal characters.
            text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        blocks.append(f"{number}\n{timestamp(row['start'], separator)} --> {timestamp(row['end'], separator)}\n{text}")
    return ('WEBVTT\n\n' if format == 'vtt' else '') + '\n\n'.join(blocks) + ('\n' if blocks else '')


def project_languages(metadata: dict) -> tuple[str, str]:
    """Read a saved language pair, including the original direction-only format."""
    if not isinstance(metadata, dict):
        raise ValueError('Project metadata must be an object.')
    source = metadata.get('source_language', 'zh')
    target = metadata.get('target_language', default_target(source))
    validate_pair(source, target)
    return source, target


def _project_tracks(tracks, metadata, version):
    if not isinstance(tracks, dict) or not isinstance(metadata, dict):
        raise ValueError('Project tracks and metadata must be objects.')
    if version == 1:
        if set(tracks) != {'Mandarin', 'English'}:
            raise ValueError('Legacy projects must contain Mandarin and English tracks.')
        source, target = project_languages(metadata)
        if {source, target} != {'zh', 'en'}:
            raise ValueError('Legacy project language metadata does not match its tracks.')
    else:
        if 'source_language' not in metadata or 'target_language' not in metadata:
            raise ValueError('Multilingual projects must specify source and target language codes.')
        source, target = project_languages(metadata)
        if set(tracks) != {language_name(source), language_name(target)}:
            raise ValueError('Project tracks must match the saved source and target languages.')
    if any(not isinstance(rows, list) for rows in tracks.values()):
        raise ValueError('Each caption track must be a list of caption rows.')
    return {name: validate(rows) for name, rows in tracks.items()}


def project_json(tracks: dict[str, list[dict]], metadata: dict) -> str:
    # Keep legacy files portable to v1 readers. New explicit pairs use schema v2.
    version = 2 if isinstance(metadata, dict) and 'target_language' in metadata else 1
    clean = _project_tracks(tracks, metadata, version)
    return json.dumps({'version': version, 'tracks': clean, 'metadata': metadata},
                      ensure_ascii=False, indent=2)


def load_project(raw: str) -> tuple[dict, dict]:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] not in {1, 2}:
            raise ValueError('Unsupported project version.')
        tracks = _project_tracks(data['tracks'], data.get('metadata', {}), data['version'])
        return tracks, data.get('metadata', {})
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid project JSON.') from exc


def normalize_for_cer(text: str) -> str:
    """NFKC + lowercase; ignore whitespace and Unicode punctuation. No script conversion."""
    return ''.join(c for c in unicodedata.normalize('NFKC', text).lower()
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))


def character_error_rate(reference: str, hypothesis: str) -> float:
    reference, hypothesis = normalize_for_cer(reference), normalize_for_cer(hypothesis)
    if not reference:
        raise ValueError('Reference must contain at least one non-punctuation character.')
    previous = list(range(len(hypothesis) + 1))
    for i, actual in enumerate(reference, 1):
        current = [i]
        for j, predicted in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (actual != predicted)))
        previous = current
    return previous[-1] / len(reference)
