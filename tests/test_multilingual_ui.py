"""Multilingual editor behavior without downloading or invoking speech models."""
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from dubbing import VOICES
from languages import LANGUAGES


class VoiceRegistryTests(unittest.TestCase):
    def test_every_selected_language_has_voice_choices(self):
        for name in LANGUAGES.values():
            with self.subTest(language=name):
                self.assertGreaterEqual(len(VOICES[name]), 2)
                self.assertTrue(all(voice.endswith('Neural') for voice in VOICES[name].values()))


@unittest.skipUnless(importlib.util.find_spec('streamlit'), 'Streamlit is not installed')
class MultilingualUITests(unittest.TestCase):
    def setUp(self):
        probe = patch('dubbing_ui.cached_video_duration', return_value=10)
        probe.start()
        self.addCleanup(probe.stop)

    def app(self):
        from streamlit.testing.v1 import AppTest
        return AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)

    def saved_spanish_video(self):
        app = self.app()
        video = b'synthetic-multilingual-ui-video'
        next(widget for widget in app.file_uploader if widget.key == 'recording_upload').set_value(
            ('synthetic.mp4', video, 'video/mp4'))
        app.session_state['tracks'] = {
            'English': [{'start': 0, 'end': 2, 'text': 'Hello, everyone.'}],
            'Spanish': [{'start': 0, 'end': 2, 'text': 'Hola a todos.'}],
        }
        app.session_state['metadata'] = {
            'source_language': 'en', 'target_language': 'es',
            'source_sha256': hashlib.sha256(video).hexdigest(),
        }
        app.session_state['revision'] = 1
        app.run()
        return app

    def test_selectors_cover_twenty_languages_and_exclude_same_language(self):
        app = self.app()
        source = next(widget for widget in app.selectbox if widget.label == 'Recording language')
        target = next(widget for widget in app.selectbox if widget.label == 'Translate to')
        self.assertEqual(set(source.options), set(LANGUAGES.values()))
        self.assertEqual(source.value, 'Mandarin')
        self.assertEqual(target.value, 'English')
        self.assertNotIn(source.value, target.options)
        source.select('English').run()
        target = next(widget for widget in app.selectbox if widget.label == 'Translate to')
        self.assertEqual(target.value, 'Mandarin')
        self.assertNotIn('English', target.options)
        self.assertEqual(len(app.exception), 0)

    def test_saved_languages_control_tabs_preview_dubbing_and_evaluation(self):
        app = self.saved_spanish_video()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([tab.label for tab in app.tabs], ['English captions', 'Spanish captions'])
        self.assertEqual([metric.label for metric in app.metric[:2]], ['English captions', 'Spanish captions'])
        self.assertEqual([track.label for track in app.get('video')[0].proto.subtitles], ['Spanish'])
        self.assertEqual(app.text_area[0].label, 'Reference English transcript')
        self.assertEqual(next(button for button in app.button if button.key == 'create_dubbed_video').label,
                         'Create Spanish dubbed video')
        self.assertEqual(len(app.get('download_button')), 5)
        next(widget for widget in app.selectbox if widget.label == 'Recording language').select('French').run()
        next(widget for widget in app.selectbox if widget.label == 'Translate to').select('Japanese').run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([tab.label for tab in app.tabs], ['English captions', 'Spanish captions'])
        self.assertEqual(app.text_area[0].label, 'Reference English transcript')
        self.assertEqual(next(button for button in app.button if button.key == 'create_dubbed_video').label,
                         'Create Spanish dubbed video')
        self.assertTrue(any('English → Spanish' in info.value for info in app.info))

    def test_generation_passes_selected_pair_and_saves_explicit_metadata(self):
        app = self.app()
        next(widget for widget in app.selectbox if widget.label == 'Recording language').select('French').run()
        next(widget for widget in app.selectbox if widget.label == 'Translate to').select('German').run()
        next(widget for widget in app.file_uploader if widget.key == 'recording_upload').set_value(
            ('synthetic.mp3', b'synthetic-audio', 'audio/mpeg')).run()
        tracks = {'French': [{'start': 0, 'end': 1, 'text': 'Bonjour.'}],
                  'German': [{'start': 0, 'end': 1, 'text': 'Guten Tag.'}]}
        with patch('engine.decode_media', return_value=([0], 1)), \
                patch('engine.load_model', return_value='mock-model'), \
                patch('engine.load_translator', return_value='pair-translator') as translator, \
                patch('engine.generate', return_value=(tracks, .1)) as generate:
            next(button for button in app.button if button.label == 'Generate captions').click().run()
        self.assertEqual(len(app.exception), 0)
        translator.assert_called_with('fr', 'de')
        self.assertEqual(generate.call_args.kwargs['source_language'], 'fr')
        self.assertEqual(generate.call_args.kwargs['target_language'], 'de')
        self.assertEqual(generate.call_args.kwargs['duration_seconds'], 1)
        self.assertEqual(app.session_state['metadata']['source_language'], 'fr')
        self.assertEqual(app.session_state['metadata']['target_language'], 'de')
        self.assertEqual(app.session_state['metadata']['translation_model'], 'facebook/m2m100_418M')
        self.assertEqual([tab.label for tab in app.tabs], ['French captions', 'German captions'])

    def test_missing_voice_keeps_caption_exports_available(self):
        app = self.saved_spanish_video()
        with patch.dict('dubbing_ui.VOICES', {'Spanish': {}}):
            app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any('Audio dubbing is not available for Spanish' in info.value for info in app.info))
        self.assertFalse(any(button.key == 'create_dubbed_video' for button in app.button))
        self.assertEqual(len(app.get('download_button')), 5)
