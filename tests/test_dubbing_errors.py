"""Timing repair and visible dubbing failures, without transmitting caption text."""
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from captions import subtitles
from dubbing import DubbingError


@unittest.skipUnless(importlib.util.find_spec('streamlit'), 'Streamlit is not installed')
class DubbingErrorUITests(unittest.TestCase):
    def setUp(self):
        probe = patch('dubbing_ui.cached_video_duration', return_value=98.059011)
        probe.start()
        self.addCleanup(probe.stop)

    def saved_russian_video(self, *, overrun=True):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
        video = b'synthetic-timing-repair-test'
        next(widget for widget in app.file_uploader if widget.key == 'recording_upload').set_value(
            ('synthetic.mp4', video, 'video/mp4'))
        # Same boundary as the reported failure; all text is synthetic.
        end = 105.41 if overrun else 98.059
        app.session_state['tracks'] = {
            'English': [{'start': 97.41, 'end': end, 'text': 'Edited final caption.'}],
            'Russian': [{'start': 97.41, 'end': end, 'text': 'Последняя фраза.'}],
        }
        app.session_state['metadata'] = {'source_language': 'en', 'target_language': 'ru',
                                         'source_sha256': hashlib.sha256(video).hexdigest()}
        app.session_state['revision'] = 1
        return app.run()

    def test_overrun_is_visible_and_repair_preserves_both_edited_tracks(self):
        app = self.saved_russian_video()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any('105.410s' in warning.value and '98.059s' in warning.value
                            for warning in app.warning))
        app.checkbox(key='dub_online_consent').check().run()
        self.assertTrue(app.button(key='create_dubbed_video').disabled)
        before = app.session_state['tracks']
        app.session_state['dubbed_result'] = {'signature': 'obsolete', 'video': b'old'}
        with patch('dubbing_ui.create_dubbed_video') as synthesis:
            app.button(key='fit_caption_times').click().run()
        synthesis.assert_not_called()
        self.assertEqual(len(app.exception), 0)
        after = app.session_state['tracks']
        for name in before:
            self.assertEqual(after[name][0]['text'], before[name][0]['text'])
            self.assertEqual(after[name][0]['start'], 97.41)
            self.assertEqual(after[name][0]['end'], 98.059)
        self.assertEqual(app.session_state['revision'], 2)
        self.assertNotIn('dubbed_result', app.session_state)
        self.assertFalse(app.button(key='create_dubbed_video').disabled)
        self.assertEqual(app.session_state['metadata']['caption_duration_seconds'], 98.059011)

    def test_outside_start_requires_manual_edit_instead_of_dropping_text(self):
        app = self.saved_russian_video()
        tracks = app.session_state['tracks']
        tracks['English'] = [{'start': 99, 'end': 105.41, 'text': 'Preserve this manual edit.'}]
        app.session_state['tracks'] = tracks
        app.session_state['revision'] = 2
        app.run()
        self.assertTrue(app.button(key='fit_caption_times').disabled)
        self.assertTrue(any('Move its start earlier or remove the caption' in info.value for info in app.info))
        self.assertEqual(app.session_state['tracks']['English'][0]['text'], 'Preserve this manual edit.')

    def test_dubbing_error_is_outside_status_and_expanded(self):
        app = self.saved_russian_video(overrun=False)
        app.checkbox(key='dub_online_consent').check().run()
        with patch('dubbing_ui.create_dubbed_video', side_effect=DubbingError('Voice generation failed for caption 1.')):
            app.button(key='create_dubbed_video').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any(error.value == 'Voice generation failed for caption 1.' for error in app.error))
        failed = next(status for status in app.status if status.label == 'Dubbing failed')
        self.assertTrue(failed.proto.expanded)
        self.assertEqual(len(failed.error), 0)

    def test_generation_uses_shorter_video_duration(self):
        app = self.saved_russian_video(overrun=False)
        generated = {'English': [{'start': 0, 'end': 1, 'text': 'Hello.'}],
                     'Mandarin': [{'start': 0, 'end': 1, 'text': '你好。'}]}
        with patch('engine.decode_media', return_value=([0], 98.069333)), \
                patch('dubbing.probe_video', return_value=(98.059011, 'h264')), \
                patch('engine.load_model', return_value='mock-model'), \
                patch('engine.generate', return_value=(generated, .1)) as generate:
            next(button for button in app.button if button.label == 'Generate captions').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(generate.call_args.kwargs['duration_seconds'], 98.059011)
        self.assertEqual(app.session_state['metadata']['caption_duration_seconds'], 98.059011)

    def test_pauses_are_default_and_dubbed_caption_exports_follow_output_timing(self):
        app = self.saved_russian_video(overrun=False)
        self.assertEqual(app.radio(key='dub_timing_mode').value, 'Allow video pauses')
        app.checkbox(key='dub_online_consent').check().run()
        retimed = [{'start': 97.41, 'end': 100.4, 'text': 'Последняя фраза.'}]
        result = {'video': b'synthetic-dubbed-video', 'audio': b'synthetic-dubbed-audio',
                  'segments': 1, 'sped_up_segments': 0, 'duration_seconds': 100.95,
                  'source_duration_seconds': 98.059011, 'caption_rows': retimed,
                  'timing_mode': 'pause', 'paused_segments': 1, 'added_seconds': 2.890989,
                  'voice': 'ru-RU-SvetlanaNeural'}
        with patch('dubbing_ui.create_dubbed_video', return_value=result) as dub, \
             patch('dubbing_ui.subtitles', wraps=subtitles) as export:
            app.button(key='create_dubbed_video').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(dub.call_args.kwargs['timing_mode'], 'pause')
        export.assert_any_call(retimed, 'vtt')
        export.assert_any_call(retimed, 'srt')
        self.assertTrue(any(button.label == 'Download dubbed captions (SRT)'
                            for button in app.get('download_button')))
        self.assertTrue(any('1 video pauses; 2.89 seconds added' in item.value for item in app.caption))
        self.assertEqual(app.session_state['tracks']['Russian'][0]['end'], 98.059)

        # The original-timing output has different timing and must be generated separately.
        app.radio(key='dub_timing_mode').set_value('Keep original timing').run()
        self.assertNotIn('dubbed_result', app.session_state)
        self.assertEqual(len(app.get('video')), 1)
        with patch('dubbing_ui.create_dubbed_video', side_effect=DubbingError('Synthetic timing failure.')) as strict:
            app.button(key='create_dubbed_video').click().run()
        self.assertEqual(strict.call_args.kwargs['timing_mode'], 'strict')
        self.assertTrue(any(error.value == 'Synthetic timing failure.' for error in app.error))
