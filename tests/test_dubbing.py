import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dubbing import DubbingError, SAMPLE_RATE, assemble_audio, create_dubbed_video, dubbing_signature, speech_slots, tempo_filter


class DubbingTests(unittest.TestCase):
    def test_signature_tracks_text_timing_voice_and_source(self):
        rows = [{'start': 1, 'end': 2, 'text': '你好'}]
        original = dubbing_signature('source', rows, 'voice')
        for source, revised, voice in [('different', rows, 'voice'), ('source', rows, 'another-voice'),
                                       ('source', [{'start': 1, 'end': 2, 'text': '再见'}], 'voice'),
                                       ('source', [{'start': 1.1, 'end': 2, 'text': '你好'}], 'voice')]:
            self.assertNotEqual(original, dubbing_signature(source, revised, voice))

    def test_slots_allow_silent_gap_but_do_not_overlap_next_line(self):
        rows = [{'start': 1, 'end': 2, 'text': '你好'}, {'start': 3, 'end': 4, 'text': '再见'}]
        slots = speech_slots(rows, 5)
        self.assertEqual([available for _, available in slots], [2, 2])
        for bad_rows, duration in [([], 5), (rows, 301), (rows, float('nan')), (rows, 3.5)]:
            with self.assertRaises((DubbingError, ValueError)):
                speech_slots(bad_rows, duration)

    def test_tempo_chain_and_speed_limit(self):
        self.assertEqual(tempo_filter(2.5), 'atempo=2,atempo=1.25000000')
        with self.assertRaises(DubbingError):
            tempo_filter(2.6)

    def test_synthesis_requires_explicit_opt_in(self):
        with patch('dubbing.synthesize_segments') as synthesize:
            with self.assertRaises(DubbingError):
                create_dubbed_video(b'video', '.mp4', [], 'zh-CN-XiaoxiaoNeural')
        synthesize.assert_not_called()

    def test_caption_overrun_explains_actual_times_before_any_voice_request(self):
        rows = [{'start': 97.41, 'end': 105.41, 'text': 'Synthetic final caption'}]
        with patch('dubbing.probe_video', return_value=(98.059011, 'h264')), \
             patch('dubbing.synthesize_segments') as synthesize:
            with self.assertRaisesRegex(DubbingError, r'Caption 1 ends at 105\.410s.*98\.059s'):
                create_dubbed_video(b'synthetic-video', '.mp4', rows, 'ru-RU-DmitryNeural', consent=True)
        synthesize.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'NumPy is not installed')
class TimelineTests(unittest.TestCase):
    def test_speech_starts_and_silent_gaps_are_preserved(self):
        import numpy as np
        rows = [{'start': 1, 'end': 2, 'text': '你好'}, {'start': 3, 'end': 4, 'text': '再见'}]
        clip = np.ones(SAMPLE_RATE // 2, dtype=np.float32) * .25
        timeline, sped_up = assemble_audio(speech_slots(rows, 5), ['a', 'b'], 5, decoder=lambda path: clip)
        self.assertEqual(len(timeline), 5 * SAMPLE_RATE)
        self.assertEqual(sped_up, 0)
        self.assertTrue(np.all(timeline[:SAMPLE_RATE] == 0))
        self.assertTrue(np.all(timeline[SAMPLE_RATE:SAMPLE_RATE + len(clip)] == .25))
        self.assertTrue(np.all(timeline[2 * SAMPLE_RATE:3 * SAMPLE_RATE] == 0))
        self.assertTrue(np.all(timeline[3 * SAMPLE_RATE:3 * SAMPLE_RATE + len(clip)] == .25))

    def test_long_speech_uses_pitch_preserving_tempo_processing(self):
        import numpy as np
        row = {'start': 0, 'end': 1, 'text': '你好'}
        original = np.ones(SAMPLE_RATE * 2, dtype=np.float32)
        fitted = np.ones(SAMPLE_RATE - 500, dtype=np.float32) * .5
        calls = []
        def processor(arguments):
            calls.append(arguments)
            return fitted.astype('<f4').tobytes()
        timeline, sped_up = assemble_audio(speech_slots([row], 1), ['speech.mp3'], 1,
                                          decoder=lambda path: original, processor=processor)
        self.assertEqual(sped_up, 1)
        self.assertIn('atempo=2,atempo=1.04000000', calls[0])
        self.assertEqual(len(timeline), SAMPLE_RATE)

    def test_speech_is_not_silently_cut_off(self):
        import numpy as np
        row = {'start': 0, 'end': 1, 'text': '你好'}
        with self.assertRaisesRegex(DubbingError, 'Caption 1'):
            assemble_audio(speech_slots([row], 1), ['speech.mp3'], 1,
                           decoder=lambda path: np.ones(3 * SAMPLE_RATE, dtype=np.float32))


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ['numpy', 'av', 'imageio_ffmpeg']),
                     'Media dependencies are not installed')
class VideoExportTests(unittest.TestCase):
    def test_webm_video_is_transcoded_to_browser_friendly_mp4(self):
        import av
        import numpy as np
        from dubbing import mux_video, probe_video, run_ffmpeg, write_wave
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source, audio, output = directory / 'source.webm', directory / 'audio.wav', directory / 'dubbed.mp4'
            run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=green:s=160x120:r=25:d=2',
                        '-c:v', 'libvpx-vp9', '-an', source])
            duration, codec = probe_video(source)
            write_wave(audio, np.zeros(round(duration * SAMPLE_RATE), dtype=np.float32))
            mux_video(source, audio, output, duration, codec)
            with av.open(str(output)) as container:
                self.assertEqual(container.streams.video[0].codec_context.name, 'h264')
                self.assertEqual(container.streams.audio[0].codec_context.name, 'aac')
            self.assertAlmostEqual(probe_video(output)[0], duration, delta=.1)

    def test_export_has_one_new_audio_stream_and_preserves_duration(self):
        import av
        import numpy as np
        from dubbing import mux_video, probe_video, run_ffmpeg, write_wave
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source = directory / 'source.mp4'
            audio = directory / 'dub.wav'
            output = directory / 'dubbed.mp4'
            run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=blue:s=160x120:r=25:d=2',
                        '-f', 'lavfi', '-i', 'sine=frequency=1000:duration=2',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', source])
            duration, codec = probe_video(source)
            # Silent replacement makes any retained original sine wave detectable.
            write_wave(audio, np.zeros(round(duration * SAMPLE_RATE), dtype=np.float32))
            mux_video(source, audio, output, duration, codec)
            with av.open(str(output)) as container:
                self.assertEqual(len(container.streams.video), 1)
                self.assertEqual(len(container.streams.audio), 1)
                self.assertEqual(container.streams.audio[0].codec_context.name, 'aac')
                frames = list(container.decode(audio=0))
                self.assertTrue(frames)
                self.assertLess(max(float(np.max(np.abs(frame.to_ndarray()))) for frame in frames), .01)
            self.assertAlmostEqual(probe_video(output)[0], duration, delta=.1)
