import json
import unittest
from types import SimpleNamespace

from captions import character_error_rate, load_project, project_json, subtitles, timestamp, validate
from engine import generate


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'start': 0, 'end': 1.25, 'text': '你好，世界！'}]

    def test_subtitle_formats_and_unicode(self):
        self.assertEqual(subtitles(self.rows), '1\n00:00:00,000 --> 00:00:01,250\n你好，世界！\n')
        self.assertTrue(subtitles(self.rows, 'vtt').startswith('WEBVTT\n\n1\n00:00:00.000'))

    def test_timestamp_rollover(self):
        self.assertEqual(timestamp(59.9996), '00:01:00,000')
        self.assertEqual(timestamp(3600.123), '01:00:00,123')

    def test_invalid_times_and_missing_text(self):
        for start, end, text in [(2, 1, 'x'), (-1, 1, 'x'), (float('nan'), 1, 'x'),
                                 (0, float('inf'), 'x'), (0, .0001, 'x'), (0, 1, ''),
                                 (0, 1, None), (0, 1, 'a --> b')]:
            with self.subTest(start=start, end=end, text=text), self.assertRaises(ValueError):
                validate([{'start': start, 'end': end, 'text': text}])

    def test_overlap_rejected(self):
        with self.assertRaises(ValueError):
            validate(self.rows + [{'start': 1, 'end': 2, 'text': 'x'}])

    def test_blank_lines_do_not_break_subtitle_blocks(self):
        result = subtitles([{'start': 0, 'end': 1, 'text': ' a\n\nb '}])
        self.assertTrue(result.endswith('a\nb\n'))

    def test_vtt_escapes_markup(self):
        self.assertIn('&lt;test&gt; &amp;', subtitles([{'start': 0, 'end': 1, 'text': '<test> &'}], 'vtt'))

    def test_project_roundtrip(self):
        tracks = {'Mandarin': self.rows, 'English': [{'start': 0, 'end': 1, 'text': 'Hello'}]}
        raw = project_json(tracks, {'model': 'tiny'})
        restored, metadata = load_project(raw)
        self.assertEqual(restored, tracks)
        self.assertEqual(metadata['model'], 'tiny')
        self.assertIn('你好', raw)

    def test_malformed_projects(self):
        for raw in ['no', 'null', '[]', '{"version":2,"tracks":{}}',
                    json.dumps({'version': 1, 'tracks': {'Mandarin': [], 'English': []}, 'metadata': []})]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_project(raw)

    def test_character_error_rate(self):
        self.assertEqual(character_error_rate('你好，世界！', '你 好世界'), 0)
        self.assertEqual(character_error_rate('你好世界', '你好世'), .25)
        self.assertEqual(character_error_rate('你好世界', '你号世界'), .25)
        self.assertEqual(character_error_rate('你好世界', '你们好世界'), .25)
        self.assertEqual(character_error_rate('ＡＢＣ', 'abc'), 0)
        self.assertGreater(character_error_rate('你', '你你你你'), 1)
        with self.assertRaises(ValueError):
            character_error_rate('， ', '你好')

    def test_inference_runs_two_distinct_tasks(self):
        class FakeModel:
            def __init__(self):
                self.tasks = []

            def transcribe(self, audio, **kwargs):
                self.tasks.append(kwargs)
                text = '你好' if kwargs['task'] == 'transcribe' else 'Hello'
                return iter([SimpleNamespace(start=0, end=1, text=text)]), None

        model = FakeModel()
        tracks, elapsed = generate(model, object())
        self.assertEqual([task['task'] for task in model.tasks], ['transcribe', 'translate'])
        self.assertTrue(all(task['language'] == 'zh' for task in model.tasks))
        self.assertEqual(tracks['English'][0]['text'], 'Hello')
        self.assertGreaterEqual(elapsed, 0)


if __name__ == '__main__':
    unittest.main()
