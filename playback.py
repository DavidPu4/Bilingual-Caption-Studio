"""Attach validated captions only to the recording they belong to."""
from captions import subtitles
from languages import LANGUAGES


def video_subtitles(tracks, metadata, source_sha256, language='Mandarin'):
    if language not in {*LANGUAGES.values(), 'Off'}:
        raise ValueError('Choose a supported caption language or Off.')
    if (language == 'Off' or not source_sha256 or metadata.get('sample')
            or metadata.get('source_sha256') != source_sha256):
        return None
    rows = tracks.get(language)
    if not rows:
        return None
    # A dict gives the native player the correct track label. Its first track is on by default.
    return {language: subtitles(rows, 'vtt')}
