import json
import unittest

from captions import load_project, project_json, project_languages
from languages import LANGUAGES
from playback import video_subtitles


class MultilingualProjectTests(unittest.TestCase):
    def test_all_twenty_languages_roundtrip_with_explicit_pair_metadata(self):
        for source, label in LANGUAGES.items():
            target = 'es' if source == 'en' else 'en'
            tracks = {label: [{'start': 0, 'end': 1, 'text': 'Source words'}],
                      LANGUAGES[target]: [{'start': 0, 'end': 1, 'text': 'Translated words'}]}
            metadata = {'source_language': source, 'target_language': target, 'source_sha256': 'video'}
            with self.subTest(source=source):
                raw = project_json(tracks, metadata)
                self.assertEqual(json.loads(raw)['version'], 2)
                self.assertEqual(load_project(raw), (tracks, metadata))

    def test_arabic_japanese_unicode_and_video_track_identity(self):
        tracks = {'Japanese': [{'start': .5, 'end': 2, 'text': 'こんにちは'}],
                  'Arabic': [{'start': .5, 'end': 2, 'text': 'مرحبا بالعالم'}]}
        metadata = {'source_language': 'ja', 'target_language': 'ar', 'source_sha256': 'matching'}
        raw = project_json(tracks, metadata)
        self.assertIn('مرحبا', raw)
        restored, meta = load_project(raw)
        result = video_subtitles(restored, meta, 'matching', 'Arabic')
        self.assertIn('مرحبا بالعالم', result['Arabic'])
        self.assertIsNone(video_subtitles(restored, meta, 'other', 'Arabic'))

    def test_legacy_direction_defaults_are_retained(self):
        self.assertEqual(project_languages({}), ('zh', 'en'))
        self.assertEqual(project_languages({'source_language': 'en'}), ('en', 'zh'))
        tracks = {'Mandarin': [], 'English': []}
        for meta in [{}, {'source_language': 'en'}]:
            raw = project_json(tracks, meta)
            self.assertEqual(json.loads(raw)['version'], 1)
            self.assertEqual(load_project(raw), (tracks, meta))

    def test_unknown_inconsistent_or_incomplete_v2_projects_are_rejected(self):
        valid = {'version': 2, 'tracks': {'French': [], 'English': []},
                 'metadata': {'source_language': 'fr', 'target_language': 'en'}}
        cases = [
            {**valid, 'metadata': {'source_language': 'fr'}},
            {**valid, 'metadata': {'source_language': 'xx', 'target_language': 'en'}},
            {**valid, 'metadata': {'source_language': 'en', 'target_language': 'en'}},
            {**valid, 'tracks': {'French': [], 'Mandarin': []}},
            {**valid, 'tracks': {'French': {}, 'English': []}},
            {**valid, 'version': True},
            {**valid, 'version': 3},
            {**valid, 'version': 1},
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                load_project(json.dumps(data))


if __name__ == '__main__':
    unittest.main()
