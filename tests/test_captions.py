import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_english_audio_translates_text_and_preserves_timestamps(self):
        class FakeModel:
            def transcribe(self, audio, **kwargs):
                self.kwargs = kwargs
                return iter([SimpleNamespace(start=0.5, end=2, text='Hello everyone.'),
                             SimpleNamespace(start=2, end=4, text='Welcome to my demo.')]), None

        model = FakeModel()
        received = []
        def translator(texts):
            received.extend(texts)
            return ['大家好。', '欢迎观看我的演示。']

        tracks, _ = generate(model, object(), source_language='en', translator=translator)
        self.assertEqual(model.kwargs['language'], 'en')
        self.assertEqual(model.kwargs['task'], 'transcribe')
        self.assertEqual(received, ['Hello everyone.', 'Welcome to my demo.'])
        self.assertEqual(tracks['Mandarin'][0], {'start': 0.5, 'end': 2.0, 'text': '大家好。'})
        for source, target in zip(tracks['English'], tracks['Mandarin']):
            self.assertEqual((source['start'], source['end']), (target['start'], target['end']))
        restored, metadata = load_project(project_json(tracks, {'source_language': 'en'}))
        self.assertEqual(restored, tracks)
        self.assertEqual(metadata['source_language'], 'en')

    def test_english_silence_skips_translation_model(self):
        class SilentModel:
            def transcribe(self, audio, **kwargs):
                return iter([]), None
        with patch('engine.load_translator') as loader:
            tracks, _ = generate(SilentModel(), object(), source_language='en')
        loader.assert_not_called()
        self.assertEqual(tracks, {'English': [], 'Mandarin': []})

    def test_translation_failure_is_not_returned_as_partial_success(self):
        class FakeModel:
            def transcribe(self, audio, **kwargs):
                return iter([SimpleNamespace(start=0, end=1, text='Hello')]), None
        for translator in [lambda texts: [], lambda texts: ['']]:
            with self.assertRaises(ValueError):
                generate(FakeModel(), object(), source_language='en', translator=translator)

    def test_unsupported_source_language(self):
        with self.assertRaises(ValueError):
            generate(object(), object(), source_language='xx')


if __name__ == '__main__':
    unittest.main()
