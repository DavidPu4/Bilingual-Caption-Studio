"""Flexible dubbing keeps complete synthetic audio and shifts picture/captions together."""
import copy
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from dubbing import (DubbingError, SAMPLE_RATE, assemble_flexible_audio,
                     create_dubbed_video, dubbing_signature, speech_slots)


class TimingModeTests(unittest.TestCase):
    def test_unknown_mode_is_rejected_before_speech_or_media_processing(self):
        with patch('dubbing.synthesize_segments') as synthesis, patch('dubbing.probe_video') as probe:
            with self.assertRaisesRegex(DubbingError, 'Allow video pauses'):
                create_dubbed_video(b'synthetic', '.mp4', [], 'ru-RU-SvetlanaNeural',
                                    consent=True, timing_mode='unrecognized')
        synthesis.assert_not_called()
        probe.assert_not_called()

    def test_timing_mode_changes_the_result_fingerprint(self):
        rows = [{'start': 0, 'end': 1, 'text': 'Synthetic caption.'}]
        self.assertNotEqual(dubbing_signature('video', rows, 'voice', 'strict'),
                            dubbing_signature('video', rows, 'voice', 'pause'))


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'NumPy is not installed')
class FlexibleTimelineTests(unittest.TestCase):
    def test_multiple_holds_keep_all_audio_shift_later_cues_and_preserve_silence(self):
        import numpy as np
        rows = [{'start': .5, 'end': 1.25, 'text': 'First complete line.'},
                {'start': 1.5, 'end': 2.1, 'text': 'Short line.'},
                {'start': 3.5, 'end': 4., 'text': 'Final complete line.'}]
        unchanged = copy.deepcopy(rows)
        decoded = {'first': np.full(3 * SAMPLE_RATE, .2, dtype=np.float32),
                   'middle': np.full(SAMPLE_RATE // 4, .4, dtype=np.float32),
                   'last': np.full(2 * SAMPLE_RATE, .6, dtype=np.float32)}
        fitted = {'first': np.full(2 * SAMPLE_RATE, .2, dtype=np.float32),
                  'last': np.full(3 * SAMPLE_RATE // 2, .6, dtype=np.float32)}
        speeds = []

        def process(arguments):
            speeds.append(arguments[arguments.index('-af') + 1])
            return fitted[arguments[1]].astype('<f4').tobytes()

        timeline, adjusted, pauses, cues = assemble_flexible_audio(
            speech_slots(rows, 4), ['first', 'middle', 'last'], 4,
            decoder=decoded.__getitem__, processor=process)
        self.assertEqual(rows, unchanged)
        self.assertEqual(adjusted, 2)
        self.assertEqual(speeds, ['atempo=1.25000000', 'atempo=1.25000000'])
        self.assertEqual(pauses, [{'at': 1.5, 'duration': 1.}, {'at': 4., 'duration': 1.}])
        self.assertEqual(len(timeline), 6 * SAMPLE_RATE)
        np.testing.assert_array_equal(timeline[:SAMPLE_RATE // 2], 0)
        np.testing.assert_array_equal(timeline[SAMPLE_RATE // 2:5 * SAMPLE_RATE // 2], fitted['first'])
        np.testing.assert_array_equal(timeline[5 * SAMPLE_RATE // 2:11 * SAMPLE_RATE // 4], decoded['middle'])
        np.testing.assert_array_equal(timeline[11 * SAMPLE_RATE // 4:9 * SAMPLE_RATE // 2], 0)
        np.testing.assert_array_equal(timeline[9 * SAMPLE_RATE // 2:], fitted['last'])
        self.assertEqual([cue['start'] for cue in cues], [.5, 2.5, 4.5])
        self.assertEqual([cue['end'] for cue in cues], [2.5, 3.1, 6.])
        self.assertEqual([cue['text'] for cue in cues], [row['text'] for row in rows])
        self.assertTrue(all(a['end'] <= b['start'] for a, b in zip(cues, cues[1:])))

    def test_short_speech_uses_original_slots_without_any_pause_or_tempo_request(self):
        import numpy as np
        rows = [{'start': .25, 'end': .8, 'text': 'First.'},
                {'start': 1.5, 'end': 1.75, 'text': 'Second.'}]
        samples = np.ones(SAMPLE_RATE // 4, dtype=np.float32) * .25
        with patch('dubbing.run_ffmpeg') as processor:
            timeline, adjusted, pauses, cues = assemble_flexible_audio(
                speech_slots(rows, 2.5), ['a', 'b'], 2.5,
                decoder=lambda _: samples, processor=processor)
        processor.assert_not_called()
        self.assertEqual((adjusted, pauses, cues), (0, [], rows))
        self.assertEqual(len(timeline), round(2.5 * SAMPLE_RATE))
        self.assertTrue(np.all(timeline[:SAMPLE_RATE // 4] == 0))
        self.assertTrue(np.all(timeline[SAMPLE_RATE // 4:SAMPLE_RATE // 2] == .25))
        self.assertTrue(np.all(timeline[SAMPLE_RATE // 2:3 * SAMPLE_RATE // 2] == 0))
        self.assertTrue(np.all(timeline[3 * SAMPLE_RATE // 2:7 * SAMPLE_RATE // 4] == .25))
        self.assertTrue(np.all(timeline[7 * SAMPLE_RATE // 4:] == 0))

    def test_empty_adjusted_audio_and_missing_files_have_clear_failures(self):
        import numpy as np
        rows = [{'start': 0., 'end': 1., 'text': 'Synthetic.'}]
        slots = speech_slots(rows, 1)
        with self.assertRaisesRegex(DubbingError, 'Missing speech'):
            assemble_flexible_audio(slots, [], 1)
        with self.assertRaisesRegex(DubbingError, 'empty speech after timing adjustment'):
            assemble_flexible_audio(slots, ['audio'], 1,
                                    decoder=lambda _: np.ones(2 * SAMPLE_RATE, dtype=np.float32),
                                    processor=lambda _: b'')
        with self.assertRaisesRegex(DubbingError, 'empty speech'):
            assemble_flexible_audio(slots, ['audio'], 1,
                                    decoder=lambda _: np.zeros(0, dtype=np.float32))

    def test_expansion_limit_fails_before_allocating_an_unbounded_timeline(self):
        import numpy as np
        rows = [{'start': 0., 'end': 1., 'text': 'Synthetic.'}]
        # Lower the configured cap so this regression uses only a small array.
        with patch('dubbing.MAX_OUTPUT_SECONDS', 2):
            with self.assertRaisesRegex(DubbingError, 'exceed ten minutes'):
                assemble_flexible_audio(speech_slots(rows, 1), ['audio'], 1,
                                        decoder=lambda _: np.ones(3 * SAMPLE_RATE, dtype=np.float32),
                                        processor=lambda _: np.ones(5 * SAMPLE_RATE // 2, dtype='<f4').tobytes())


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in
                        ['numpy', 'av', 'faster_whisper', 'imageio_ffmpeg']),
                     'Media dependencies are not installed')
class FlexibleExportTests(unittest.TestCase):
    def test_full_export_handles_overlong_first_and_final_lines_without_online_synthesis(self):
        import av
        import numpy as np
        from dubbing import run_ffmpeg, write_wave
        rows = [{'start': .5, 'end': 1.25, 'text': 'Synthetic Russian line one.'},
                {'start': 1.5, 'end': 2.1, 'text': 'Synthetic short line.'},
                {'start': 3.5, 'end': 4., 'text': 'Synthetic final line.'}]
        original_rows = copy.deepcopy(rows)
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source = directory / 'synthetic.mp4'
            run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=blue:s=160x120:r=30:d=4',
                        '-f', 'lavfi', '-i', 'sine=frequency=1000:duration=4',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', source])

            async def synthesize(captions, voice, output):
                self.assertEqual(captions, rows)
                paths = []
                for index, seconds in enumerate([3., .25, 2.]):
                    path = output / f'synthetic-{index}.wav'
                    write_wave(path, np.zeros(round(seconds * SAMPLE_RATE), dtype=np.float32))
                    paths.append(path)
                return paths

            messages = []
            with patch('dubbing.synthesize_segments', side_effect=synthesize) as synthesis:
                result = create_dubbed_video(source.read_bytes(), '.mp4', rows, 'ru-RU-SvetlanaNeural',
                                             consent=True, timing_mode='pause', progress=messages.append)
            self.assertEqual(synthesis.call_count, 1)
            self.assertEqual(rows, original_rows)
            self.assertEqual(result['timing_mode'], 'pause')
            self.assertEqual(result['paused_segments'], 2)
            self.assertEqual(result['sped_up_segments'], 2)
            self.assertGreater(result['duration_seconds'], 5)
            self.assertAlmostEqual(result['duration_seconds'], 4 + result['added_seconds'], delta=.001)
            self.assertGreater(result['caption_rows'][1]['start'], rows[1]['start'])
            self.assertGreater(result['caption_rows'][2]['end'], 4.)
            self.assertEqual(result['caption_rows'][2]['end'], result['duration_seconds'])
            self.assertTrue(any('video pause' in message for message in messages))
            with wave.open(io.BytesIO(result['audio'])) as audio:
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getframerate(), SAMPLE_RATE)
                self.assertAlmostEqual(audio.getnframes() / SAMPLE_RATE, result['duration_seconds'], delta=.001)
            with av.open(io.BytesIO(result['video'])) as container:
                self.assertEqual(len(container.streams.video), 1)
                self.assertEqual(len(container.streams.audio), 1)
                self.assertEqual(container.streams.video[0].codec_context.name, 'h264')
                self.assertEqual(container.streams.audio[0].codec_context.name, 'aac')
                self.assertAlmostEqual(float(container.duration / av.time_base), result['duration_seconds'], delta=.06)
                decoded = list(container.decode(audio=0))
                self.assertTrue(decoded)
                self.assertLess(max(float(np.abs(frame.to_ndarray()).max()) for frame in decoded), .001)


if __name__ == '__main__':
    unittest.main()
