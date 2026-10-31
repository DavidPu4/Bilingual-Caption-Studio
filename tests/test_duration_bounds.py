"""Media duration bounds without speech downloads or sending personal media."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from captions import fit_captions_to_duration, subtitles
import cli
import engine


class SpeechModel:
    def __init__(self, source, english=None):
        self.source = source
        self.english = english if english is not None else source
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append(options)
        rows = self.source if options['task'] == 'transcribe' else self.english
        return iter(SimpleNamespace(**row) for row in rows), None


class DurationBoundsTests(unittest.TestCase):
    def test_final_caption_is_clipped_inside_exported_video_duration(self):
        rows = [{'start': 95.41, 'end': 97.41, 'text': 'Previous caption'},
                {'start': 97.41, 'end': 105.41, 'text': 'Последняя строка'}]
        fitted, changed = fit_captions_to_duration(rows, 98.059011)
        self.assertEqual(changed, 1)
        self.assertEqual(fitted[0], rows[0])
        self.assertEqual(fitted[1], {**rows[1], 'end': 98.059})
        self.assertEqual(rows[1]['end'], 105.41)
        self.assertIn('00:01:37,410 --> 00:01:38,059', subtitles(fitted))

    def test_generated_hallucinated_tail_is_dropped_and_counted(self):
        rows = [{'start': 1., 'end': 2., 'text': 'Keep'},
                {'start': 3., 'end': 5., 'text': 'Trim'},
                {'start': 5., 'end': 6., 'text': 'Outside'}]
        fitted, changed = fit_captions_to_duration(rows, 4.0009, drop_outside=True)
        self.assertEqual(changed, 2)
        self.assertEqual(fitted, [rows[0], {**rows[1], 'end': 4.}])

    def test_manual_cues_outside_or_without_one_millisecond_room_raise(self):
        for start in [98.059, 100., 98.0585]:
            with self.subTest(start=start), self.assertRaisesRegex(ValueError, 'Caption 1.*media end'):
                fit_captions_to_duration([{'start': start, 'end': 105., 'text': 'Keep my edit'}], 98.059011)
        fitted, changed = fit_captions_to_duration(
            [{'start': 98.058, 'end': 105., 'text': 'One millisecond'}], 98.059011)
        self.assertEqual(changed, 1)
        self.assertEqual(fitted[0]['start'], 98.058)
        self.assertEqual(fitted[0]['end'], 98.059)

    def test_invalid_duration_is_rejected_before_model_or_translator(self):
        for duration in [0, -1, float('inf'), float('-inf'), float('nan'), 'bad', True]:
            with self.subTest(duration=duration):
                model = SpeechModel([])
                with self.assertRaisesRegex(ValueError, 'finite positive'):
                    fit_captions_to_duration([], duration)
                with patch('engine.load_translator') as loader:
                    with self.assertRaisesRegex(ValueError, 'finite positive'):
                        engine.generate(model, object(), source_language='en', target_language='ru',
                                        duration_seconds=duration)
                    loader.assert_not_called()
                self.assertEqual(model.calls, [])
        with self.assertRaisesRegex(ValueError, 'finite positive'):
            fit_captions_to_duration([], None)

    def test_invalid_rows_are_not_hidden_by_drop_option(self):
        with self.assertRaisesRegex(ValueError, 'Caption 1'):
            fit_captions_to_duration([{'start': 10., 'end': 9., 'text': 'Invalid'}], 2., drop_outside=True)

    def test_whisper_source_and_english_speech_translation_are_both_bounded(self):
        model = SpeechModel(
            [{'start': 97.41, 'end': 105.41, 'text': 'Исходный текст'}],
            [{'start': 97., 'end': 106., 'text': 'English text'},
             {'start': 106., 'end': 107., 'text': 'Hallucinated tail'}])
        messages = []
        with patch('engine.load_translator') as loader:
            tracks, _ = engine.generate(model, object(), messages.append, source_language='ru',
                                        target_language='en', duration_seconds=98.059011)
            loader.assert_not_called()
        self.assertEqual(tracks['Russian'], [{'start': 97.41, 'end': 98.059, 'text': 'Исходный текст'}])
        self.assertEqual(tracks['English'], [{'start': 97., 'end': 98.059, 'text': 'English text'}])
        self.assertIn('Adjusted 1 Russian caption(s) to fit the media duration.', messages)
        self.assertIn('Adjusted 2 English caption(s) to fit the media duration.', messages)

    def test_text_translation_receives_only_fitted_source_rows_and_inherits_timing(self):
        model = SpeechModel([{'start': 97.41, 'end': 105.41, 'text': 'Final caption'},
                             {'start': 105.41, 'end': 107., 'text': 'Hallucinated tail'}])
        received = []

        def translate(texts):
            received.extend(texts)
            return ['Последняя строка']

        tracks, _ = engine.generate(model, object(), source_language='en', target_language='ru',
                                    translator=translate, duration_seconds=98.059011)
        self.assertEqual(received, ['Final caption'])
        self.assertEqual(tracks['English'], [{'start': 97.41, 'end': 98.059, 'text': 'Final caption'}])
        self.assertEqual(tracks['Russian'], [{'start': 97.41, 'end': 98.059, 'text': 'Последняя строка'}])

    def test_omitting_duration_preserves_existing_generate_api(self):
        model = SpeechModel([{'start': 97.41, 'end': 105.41, 'text': 'Source'}])
        tracks, _ = engine.generate(model, object(), source_language='ru', target_language='en')
        self.assertEqual(tracks['English'][0]['end'], 105.41)

    def test_cli_uses_shorter_video_or_audio_duration_and_records_bound(self):
        for extension, audio_duration, video_duration, expected in [
                ('.MP4', 98.069333, 98.059011, 98.059011),
                ('.webm', 97., 98., 97.),
                ('.wav', 98., None, 98.)]:
            with self.subTest(extension=extension), TemporaryDirectory() as directory:
                output = Path(directory) / 'results'
                tracks = {'Mandarin': [], 'English': []}
                with patch('sys.argv', ['cli.py', 'recording' + extension, '--output', str(output)]), \
                     patch('cli.decode_media', return_value=(object(), audio_duration)), \
                     patch('cli.probe_video', return_value=(video_duration, 'h264')) as probe, \
                     patch('cli.load_model', return_value=object()), \
                     patch('cli.generate', return_value=(tracks, 1.)) as generate, \
                     patch('builtins.print'):
                    cli.main()
                self.assertEqual(generate.call_args.kwargs['duration_seconds'], expected)
                metadata = json.loads((output / 'project.json').read_text(encoding='utf-8'))['metadata']
                self.assertEqual(metadata['caption_duration_seconds'], expected)
                self.assertEqual(metadata['duration_seconds'], audio_duration)
                self.assertEqual(probe.call_count, 0 if extension == '.wav' else 1)


if __name__ == '__main__':
    unittest.main()
