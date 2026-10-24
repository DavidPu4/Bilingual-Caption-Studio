import unittest

from playback import video_subtitles


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.tracks = {'Mandarin': [{'start': 0.69, 'end': 5.45, 'text': '大卫，你好吗？'}],
                       'English': [{'start': 0.69, 'end': 5.45, 'text': 'David, how are you?'}]}
        self.metadata = {'source_sha256': 'matching-video'}

    def test_mandarin_is_default_and_preserves_unicode_and_timing(self):
        result = video_subtitles(self.tracks, self.metadata, 'matching-video')
        self.assertEqual(list(result), ['Mandarin'])
        self.assertIn('00:00:00.690 --> 00:00:05.450', result['Mandarin'])
        self.assertIn('大卫，你好吗？', result['Mandarin'])

    def test_english_and_off(self):
        self.assertIn('David, how are you?', video_subtitles(self.tracks, self.metadata,
                                                           'matching-video', 'English')['English'])
        self.assertIsNone(video_subtitles(self.tracks, self.metadata, 'matching-video', 'Off'))

    def test_wrong_recording_sample_or_missing_identity_never_shows_captions(self):
        self.assertIsNone(video_subtitles(self.tracks, self.metadata, 'another-video'))
        self.assertIsNone(video_subtitles(self.tracks, {}, 'matching-video'))
        self.assertIsNone(video_subtitles(self.tracks, {}, None))
        self.assertIsNone(video_subtitles(self.tracks, {**self.metadata, 'sample': True}, 'matching-video'))

    def test_missing_empty_or_invalid_tracks(self):
        self.assertIsNone(video_subtitles({}, self.metadata, 'matching-video'))
        self.assertIsNone(video_subtitles({'Mandarin': []}, self.metadata, 'matching-video'))
        with self.assertRaises(ValueError):
            video_subtitles({'Mandarin': [{'start': 1, 'end': 0, 'text': '坏'}]},
                            self.metadata, 'matching-video')

    def test_caption_corrections_are_used(self):
        self.tracks['Mandarin'][0].update(text='你好，大卫！', start=1.25)
        result = video_subtitles(self.tracks, self.metadata, 'matching-video')['Mandarin']
        self.assertIn('你好，大卫！', result)
        self.assertIn('00:00:01.250 --> 00:00:05.450', result)
