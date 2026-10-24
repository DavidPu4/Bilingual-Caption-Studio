"""UI checks run locally when Streamlit is installed; core CI needs no model download."""
import importlib.util
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('streamlit'), 'Streamlit is not installed')
class AppTests(unittest.TestCase):
    def test_cloud_video_save_refresh_and_open(self):
        from streamlit.testing.v1 import AppTest
        from firebase_client import load_config
        config = {**load_config(), 'storageEnabled': True}
        user = {'uid': 'user-a', 'email': 'test@example.com', 'id_token': 'test-token',
                'refresh_token': 'test-refresh', 'expires_at': 9999999999}
        name = 'users/user-a/videos/' + 'a' * 64 + '-clip.mp4'
        info = {'name': name, 'size': '10', 'contentType': 'video/mp4'}
        with patch('firebase_client.load_config', return_value=config):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
            app.session_state['cloud_user'] = user
            next(widget for widget in app.file_uploader if widget.key == 'recording_upload').set_value(
                ('clip.mp4', b'mock-video', 'video/mp4'))
            app.run()
            with patch('cloud_ui.FirebaseClient.upload_video', return_value=info) as upload:
                next(button for button in app.button if button.label == 'Save video to my cloud library').click().run()
            self.assertEqual(len(app.exception), 0)
            upload.assert_called_once_with(user, 'clip.mp4', b'mock-video')
            with patch('cloud_ui.FirebaseClient.list_videos', return_value=([info], False)):
                next(button for button in app.button if button.label == 'Refresh my videos').click().run()
            with patch('cloud_ui.FirebaseClient.download_video', return_value=b'mock-cloud-video') as download:
                next(button for button in app.button if button.label == 'Open selected video').click().run()
            self.assertEqual(len(app.exception), 0)
            download.assert_called_once_with(user, name)
            self.assertEqual(app.session_state['cloud_recording']['content'], b'mock-cloud-video')
            self.assertEqual(len(app.get('video')), 1)

    def test_account_login_and_logout(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
        next(widget for widget in app.text_input if widget.label == 'Email').input('test@example.com')
        next(widget for widget in app.text_input if widget.label == 'Password').input('password123')
        with patch('cloud_ui.FirebaseClient.authenticate', return_value={
                'uid': 'user-a', 'email': 'test@example.com', 'id_token': 'test-token',
                'refresh_token': 'test-refresh', 'expires_at': 9999999999}) as authenticate:
            next(button for button in app.button if button.label == 'Log in').click().run()
        self.assertEqual(len(app.exception), 0)
        authenticate.assert_called_once_with('test@example.com', 'password123', signup=False)
        self.assertEqual(app.session_state['cloud_user']['uid'], 'user-a')
        self.assertTrue(any(button.label == 'Refresh my videos' for button in app.button))
        next(button for button in app.button if button.label == 'Log out').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertNotIn('cloud_user', app.session_state)

    def test_sample_edit_and_export(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        next(button for button in app.button if button.label == 'Open sample captions').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe), 2)
        self.assertEqual(len(app.get('download_button')), 5)
        app.text_area[0].input('大家好，欢迎来到我的项目演示。这个工具可以生成中文和英文字幕。你可以修改字幕，然后导出文件。')
        next(button for button in app.button if button.label == 'Calculate character error rate').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.metric[-1].value, '0.0%')

    def test_video_captions_switch_languages_and_turn_off(self):
        from streamlit.testing.v1 import AppTest
        # Mock bytes verify player configuration; actual decoding is checked in the browser.
        video_bytes = b'mock-video-player-configuration'
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
        next(widget for widget in app.file_uploader if widget.key == 'recording_upload').set_value(
            ('mock.mp4', video_bytes, 'video/mp4'))
        app.session_state['tracks'] = {
            'Mandarin': [{'start': 0.69, 'end': 5.45, 'text': '大卫，你好吗？'}],
            'English': [{'start': 0.69, 'end': 5.45, 'text': 'David, how are you?'}]}
        app.session_state['metadata'] = {'source_sha256': hashlib.sha256(video_bytes).hexdigest()}
        app.session_state['revision'] = 1
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.get('video')), 1)
        self.assertEqual([track.label for track in app.get('video')[0].proto.subtitles], ['Mandarin'])
        app.radio[0].set_value('English').run()
        self.assertEqual([track.label for track in app.get('video')[0].proto.subtitles], ['English'])
        app.radio[0].set_value('Off').run()
        self.assertEqual(len(app.get('video')[0].proto.subtitles), 0)
        app.radio[0].set_value('Mandarin').run()
        app.session_state['metadata'] = {'source_sha256': 'a-different-recording'}
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.get('video')[0].proto.subtitles), 0)

    def test_english_direction_and_source_evaluation(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
        next(widget for widget in app.selectbox if widget.label == 'Recording language').select('English → Mandarin').run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn('English recording', next(widget for widget in app.get('file_uploader')
                                                if widget.key == 'recording_upload').label)
        # Simulate restoration of a generated English-source project, without downloading models.
        app.session_state['tracks'] = {
            'English': [{'start': 0, 'end': 1, 'text': 'Hello'}],
            'Mandarin': [{'start': 0, 'end': 1, 'text': '你好'}]}
        app.session_state['metadata'] = {'source_language': 'en'}
        app.session_state['revision'] = 1
        app.run()
        self.assertEqual(app.text_area[0].label, 'Reference English transcript')
        app.text_area[0].input('Hello')
        next(button for button in app.button if button.label == 'Calculate character error rate').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.metric[-1].value, '0.0%')
        self.assertEqual(len(app.get('download_button')), 5)
