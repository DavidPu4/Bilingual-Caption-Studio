"""Caption validation, portable project files, subtitle export, and CER scoring."""
from __future__ import annotations

import json
import math
import unicodedata


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


def project_json(tracks: dict[str, list[dict]], metadata: dict) -> str:
    return json.dumps({'version': 1, 'tracks': {name: validate(rows) for name, rows in tracks.items()},
                       'metadata': metadata}, ensure_ascii=False, indent=2)


def load_project(raw: str) -> tuple[dict, dict]:
    try:
        data = json.loads(raw)
        if data['version'] != 1 or not isinstance(data['tracks'], dict):
            raise ValueError('Unsupported project version or tracks.')
        if set(data['tracks']) != {'Mandarin', 'English'}:
            raise ValueError('Project must contain Mandarin and English tracks.')
        if not isinstance(data.get('metadata', {}), dict):
            raise ValueError('Project metadata must be an object.')
        tracks = {name: validate(rows) for name, rows in data['tracks'].items()}
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
