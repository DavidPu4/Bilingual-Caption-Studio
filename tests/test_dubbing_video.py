"""Video-pause export checks use generated colors and silence, never user media."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from dubbing import DubbingError
from dubbing_video import mux_paused_video, pause_filter


class PauseFilterTests(unittest.TestCase):
    def test_pause_validation_rejects_unsorted_outside_and_mismatched_durations(self):
        cases = [(0, [], 1), (float('nan'), [], 1), (3, [], 2),
                 (3, [{'at': 4, 'duration': 1}], 4),
                 (3, [{'at': 0, 'duration': 1}], 4),
                 (3, [{'at': 1, 'duration': -1}], 3),
                 (3, [{'at': 1, 'duration': float('inf')}], 4),
                 (3, [{'at': 2, 'duration': .5}, {'at': 1, 'duration': .5}], 4),
                 (3, [{'at': 1, 'duration': 1}], 3),
                 (3, [{'at': 1, 'duration': True}], 4)]
        for source, pauses, output in cases:
            with self.subTest(source=source, pauses=pauses, output=output), self.assertRaises(DubbingError):
                pause_filter(source, pauses, output)

    def test_filter_handles_two_hundred_pauses_and_end_hold(self):
        pauses = [{'at': number, 'duration': .5} for number in range(1, 201)]
        filters = pause_filter(200, pauses, 300)
        self.assertEqual(filters.count('if(gte('), 199)
        self.assertIn('fps=30', filters)
        self.assertIn('tpad=stop_mode=clone', filters)


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ['numpy', 'av', 'imageio_ffmpeg']),
                     'Media dependencies are not installed')
class PausedVideoExportTests(unittest.TestCase):
    def test_two_hundred_pauses_render_without_command_length_or_expression_errors(self):
        import av
        import numpy as np
        from dubbing import SAMPLE_RATE, run_ffmpeg, write_wave

        with tempfile.TemporaryDirectory(prefix='pause test with spaces ') as folder:
            directory = Path(folder)
            source, audio, output = directory / 'source.mp4', directory / 'speech.wav', directory / 'paused.mp4'
            run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=red:s=32x32:r=60:d=4',
                        '-c:v', 'libx264', '-an', source])
            write_wave(audio, np.zeros(5 * SAMPLE_RATE, dtype=np.float32))
            pauses = [{'at': number / 50, 'duration': .005} for number in range(1, 201)]
            mux_paused_video(source, audio, output, 4, pauses, 5)
            with av.open(str(output)) as container:
                video = container.streams.video[0]
                self.assertAlmostEqual(float(video.duration * video.time_base), 5, delta=1 / 30)
                self.assertTrue(next(container.decode(video=0)))

    def test_motion_resumes_after_interior_pause_and_final_frame_covers_remaining_speech(self):
        import av
        import numpy as np
        from dubbing import SAMPLE_RATE, run_ffmpeg, write_wave

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source, audio, output = directory / 'source.mp4', directory / 'speech.wav', directory / 'paused.mp4'
            # Changing colors make a wrong pause location or skipped motion visible.
            run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=red:s=160x120:r=30:d=2',
                        '-f', 'lavfi', '-i', 'color=c=green:s=160x120:r=30:d=2',
                        '-f', 'lavfi', '-i', 'color=c=blue:s=160x120:r=30:d=2',
                        '-f', 'lavfi', '-i', 'sine=frequency=1000:duration=6',
                        '-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]',
                        '-map', '[v]', '-map', '3:a', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-t', '6', source])
            write_wave(audio, np.zeros(9 * SAMPLE_RATE, dtype=np.float32))
            mux_paused_video(source, audio, output, 6,
                             [{'at': 2, 'duration': 1}, {'at': 6, 'duration': 2}], 9)
            samples = {time: None for time in [1.5, 2.5, 3.5, 4.5, 5.5, 7., 8.5]}
            with av.open(str(output)) as container:
                self.assertEqual(len(container.streams.video), 1)
                self.assertEqual(len(container.streams.audio), 1)
                self.assertEqual(container.streams.video[0].codec_context.name, 'h264')
                self.assertEqual(container.streams.audio[0].codec_context.name, 'aac')
                video = container.streams.video[0]
                self.assertAlmostEqual(float(video.duration * video.time_base), 9, delta=1 / 30)
                for frame in container.decode(video=0):
                    timestamp = float(frame.pts * frame.time_base)
                    for wanted in samples:
                        if samples[wanted] is None and timestamp >= wanted:
                            samples[wanted] = frame.to_ndarray(format='rgb24').mean(axis=(0, 1))
            for time in [1.5, 2.5]:
                self.assertEqual(int(np.argmax(samples[time])), 0, f'Expected held red frame at {time}')
            for time in [3.5, 4.5]:
                self.assertEqual(int(np.argmax(samples[time])), 1, f'Expected resumed green frame at {time}')
            for time in [5.5, 7., 8.5]:
                self.assertEqual(int(np.argmax(samples[time])), 2, f'Expected held final blue frame at {time}')
            with av.open(str(output)) as container:
                decoded = list(container.decode(audio=0))
                self.assertTrue(decoded)
                self.assertLess(max(float(np.max(np.abs(frame.to_ndarray()))) for frame in decoded), .01)
                self.assertAlmostEqual(float(container.streams.audio[0].duration * container.streams.audio[0].time_base),
                                       9, delta=.05)

    def test_webm_with_odd_dimensions_becomes_browser_friendly_mp4(self):
        import av
        import numpy as np
        from dubbing import SAMPLE_RATE, run_ffmpeg, write_wave

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source, audio, output = directory / 'source.webm', directory / 'speech.wav', directory / 'paused.mp4'
            run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'testsrc=size=161x121:rate=25:duration=2',
                        '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuv444p', '-an', source])
            write_wave(audio, np.zeros(3 * SAMPLE_RATE, dtype=np.float32))
            mux_paused_video(source, audio, output, 2, [{'at': 2, 'duration': 1}], 3)
            with av.open(str(output)) as container:
                video = container.streams.video[0]
                self.assertEqual(video.codec_context.name, 'h264')
                self.assertEqual((video.codec_context.width, video.codec_context.height), (160, 120))
                self.assertEqual(video.codec_context.format.name, 'yuv420p')
                self.assertAlmostEqual(float(video.duration * video.time_base), 3, delta=1 / 30)
