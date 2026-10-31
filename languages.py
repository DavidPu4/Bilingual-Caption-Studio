"""Twenty recording/translation languages shared by Whisper and M2M100.

Model support does not imply equal translation accuracy in every language pair.
"""
from __future__ import annotations

LANGUAGES = {
    'en': 'English', 'zh': 'Mandarin', 'es': 'Spanish', 'hi': 'Hindi',
    'ar': 'Arabic', 'fr': 'French', 'pt': 'Portuguese', 'ru': 'Russian',
    'de': 'German', 'ja': 'Japanese', 'ko': 'Korean', 'it': 'Italian',
    'bn': 'Bengali', 'id': 'Indonesian', 'ur': 'Urdu', 'tr': 'Turkish',
    'vi': 'Vietnamese', 'ta': 'Tamil', 'th': 'Thai', 'nl': 'Dutch',
}


def language_name(code: str) -> str:
    if not isinstance(code, str) or code not in LANGUAGES:
        raise ValueError('Choose a supported language code.')
    return LANGUAGES[code]


def language_code(name: str) -> str:
    if isinstance(name, str):
        for code, label in LANGUAGES.items():
            if label == name:
                return code
    raise ValueError('Choose a supported language name.')


def default_target(source: str) -> str:
    language_name(source)
    return 'zh' if source == 'en' else 'en'


def validate_pair(source: str, target: str) -> None:
    language_name(source)
    language_name(target)
    if source == target:
        raise ValueError('Choose a translation language different from the recording language.')
