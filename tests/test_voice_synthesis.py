"""Voice retry/cache checks use a fake provider and never send caption text."""
import asyncio
from collections import Counter
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dubbing import DubbingError
import voice_synthesis as speech


class NoAudioReceived(Exception):
    pass


class HTTPFailure(Exception):
    def __init__(self, status):
        super().__init__('private-provider-url-and-caption')
        self.status = status


class FakeProvider:
    def __init__(self, outcomes):
        self.outcomes = {key: list(values) for key, values in outcomes.items()}
        self.calls = []
        self.active = 0
        self.maximum = 0
        self.phase = 0
        self.phases = []
        self.delays = []
        self.instances = []
        self.real_sleep = asyncio.sleep

    async def sleep(self, seconds):
        self.delays.append(seconds)
        self.phase += 1
        await self.real_sleep(0)

    def Communicate(self, text, voice):
        instance = object()
        self.instances.append(instance)

        async def save(filename):
            self.calls.append((text, voice))
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.phases.append((self.phase, self.active))
            try:
                await self.real_sleep(0)
                options = self.outcomes[(text, voice)]
                outcome = options.pop(0) if len(options) > 1 else options[0]
                Path(filename).write_bytes(b'partial-private-data')
                if isinstance(outcome, Exception):
                    raise outcome
                Path(filename).write_bytes(outcome)
            finally:
                self.active -= 1
        return SimpleNamespace(save=save)


class VoiceSynthesisTests(unittest.IsolatedAsyncioTestCase):
    voice = 'ru-RU-DmitryNeural'

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.directory = Path(self.folder.name)

    def fake(self, outcomes):
        provider = FakeProvider(outcomes)
        module = SimpleNamespace(Communicate=provider.Communicate)
        modules = patch.dict(sys.modules, {'edge_tts': module})
        modules.start()
        self.addCleanup(modules.stop)
        sleeping = patch('voice_synthesis.asyncio.sleep', side_effect=provider.sleep)
        sleeping.start()
        self.addCleanup(sleeping.stop)
        return provider

    def rows(self, *texts):
        return [{'start': float(index), 'end': index + .9, 'text': text}
                for index, text in enumerate(texts)]

    async def test_initial_pass_settles_and_only_missing_lines_retry_sequentially(self):
        provider = self.fake({('first', self.voice): [NoAudioReceived(), b'first-audio'],
                              ('second', self.voice): [b'second-audio'],
                              ('third', self.voice): [NoAudioReceived(), NoAudioReceived(), b'third-audio']})
        cache, messages = {}, []
        paths = await speech.synthesize_segments(self.rows('first', 'second', 'third'), self.voice,
                                                 self.directory, cache=cache, progress=messages.append)
        self.assertEqual([path.read_bytes() for path in paths], [b'first-audio', b'second-audio', b'third-audio'])
        self.assertEqual(Counter(text for text, _ in provider.calls), {'first': 2, 'second': 1, 'third': 3})
        self.assertEqual(provider.maximum, 2)
        self.assertTrue(all(active == 1 for phase, active in provider.phases if phase > 0))
        self.assertEqual(provider.delays, [1, 3])
        self.assertEqual(len(provider.instances), 6)
        self.assertEqual(len({id(item) for item in provider.instances}), 6)
        self.assertEqual(len(cache), 3)
        self.assertTrue(any('2 missing' in value for value in messages))
        self.assertEqual(provider.active, 0)

    async def test_failed_audio_is_never_cached_and_completed_lines_survive_retry(self):
        provider = self.fake({('bad', self.voice): [NoAudioReceived('private caption')],
                              ('good', self.voice): [b'complete-good-audio']})
        cache = {}
        with self.assertRaises(DubbingError) as failure:
            await speech.synthesize_segments(self.rows('bad', 'good'), self.voice, self.directory, cache=cache)
        self.assertIn('Dmitry', str(failure.exception))
        self.assertIn('caption 1', str(failure.exception))
        self.assertIn('Microsoft returned no audio', str(failure.exception))
        self.assertIn('1 completed segment', str(failure.exception))
        self.assertNotIn('private caption', str(failure.exception))
        self.assertNotIn(speech.speech_cache_key(self.voice, 'bad'), cache)
        self.assertEqual(cache[speech.speech_cache_key(self.voice, 'good')], b'complete-good-audio')
        self.assertEqual(list(self.directory.glob('*.partial.mp3')), [])
        self.assertEqual(provider.active, 0)
        provider.outcomes[('bad', self.voice)] = [b'recovered-audio']
        messages = []
        paths = await speech.synthesize_segments(self.rows('bad', 'good'), self.voice, self.directory,
                                                 cache=cache, progress=messages.append)
        self.assertEqual([path.read_bytes() for path in paths], [b'recovered-audio', b'complete-good-audio'])
        self.assertEqual(Counter(text for text, _ in provider.calls), {'bad': 4, 'good': 1})
        self.assertTrue(any('Reusing 1' in value for value in messages))

    async def test_cache_uses_voice_and_text_but_not_editor_timestamps(self):
        other = 'ru-RU-SvetlanaNeural'
        provider = self.fake({('same text', self.voice): [b'dmitry'],
                              ('changed text', self.voice): [b'changed'],
                              ('same text', other): [b'svetlana']})
        cache = {}
        await speech.synthesize_segments(self.rows('same text'), self.voice, self.directory, cache=cache)
        retimed = [{'start': 9., 'end': 12., 'text': 'same text'}]
        await speech.synthesize_segments(retimed, self.voice, self.directory, cache=cache)
        self.assertEqual(len(provider.calls), 1)
        await speech.synthesize_segments(self.rows('changed text'), self.voice, self.directory, cache=cache)
        await speech.synthesize_segments(self.rows('same text'), other, self.directory, cache=cache)
        self.assertEqual(len(provider.calls), 3)
        self.assertTrue(all(len(key) == 64 and 'same text' not in key for key in cache))

    async def test_cache_stays_bounded_and_discards_invalid_entries(self):
        self.fake({(text, self.voice): [audio] for text, audio in [('one', b'1111'), ('two', b'2222'), ('three', b'3333')]})
        cache = {'invalid': 'not bytes', 'empty': b''}
        with patch('voice_synthesis.MAX_CACHE_BYTES', 8):
            await speech.synthesize_segments(self.rows('one', 'two', 'three'), self.voice, self.directory, cache=cache)
        self.assertEqual(sum(map(len, cache.values())), 8)
        self.assertNotIn('invalid', cache)
        self.assertNotIn('empty', cache)
        self.assertNotIn(speech.speech_cache_key(self.voice, 'one'), cache)
        self.assertIn(speech.speech_cache_key(self.voice, 'three'), cache)

    async def test_http_categories_do_not_expose_provider_message_or_claim_generic_outage(self):
        for status, expected in [(429, 'HTTP 429'), (403, 'HTTP 403')]:
            with self.subTest(status=status):
                # Nesting providers is safe; tear down this iteration explicitly.
                provider = FakeProvider({('synthetic', self.voice): [HTTPFailure(status)]})
                with patch.dict(sys.modules, {'edge_tts': SimpleNamespace(Communicate=provider.Communicate)}), \
                     patch('voice_synthesis.asyncio.sleep', side_effect=provider.sleep):
                    with self.assertRaises(DubbingError) as failure:
                        await speech.synthesize_segments(self.rows('synthetic'), self.voice, self.directory)
                message = str(failure.exception)
                self.assertIn(expected, message)
                self.assertNotIn('private-provider-url-and-caption', message)
                self.assertNotIn('offline', message)
                self.assertEqual(len(provider.calls), 3)

    async def test_empty_successful_response_is_retried_and_never_cached(self):
        provider = self.fake({('empty', self.voice): [b'']})
        cache = {}
        with self.assertRaisesRegex(DubbingError, 'returned no audio'):
            await speech.synthesize_segments(self.rows('empty'), self.voice, self.directory, cache=cache)
        self.assertEqual(cache, {})
        self.assertEqual(len(provider.calls), 3)

    async def test_local_storage_failure_is_distinct_and_not_retried(self):
        provider = self.fake({('line', self.voice): [PermissionError('private path')]})
        with self.assertRaisesRegex(DubbingError, 'could not be written or read') as failure:
            await speech.synthesize_segments(self.rows('line'), self.voice, self.directory)
        self.assertNotIn('private path', str(failure.exception))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.delays, [])

    async def test_timeout_is_identified_and_retried_without_speaking_exception_details(self):
        provider = self.fake({('line', self.voice): [TimeoutError('private network address')]})
        with self.assertRaisesRegex(DubbingError, 'timed out') as failure:
            await speech.synthesize_segments(self.rows('line'), self.voice, self.directory)
        self.assertNotIn('private network address', str(failure.exception))
        self.assertEqual(len(provider.calls), 3)

    async def test_all_initial_requests_settle_even_if_first_line_fails(self):
        provider = self.fake({(text, self.voice): [NoAudioReceived() if text == 'bad' else text.encode()]
                              for text in ['bad', 'two', 'three', 'four']})
        cache = {}
        with self.assertRaises(DubbingError):
            await speech.synthesize_segments(self.rows('bad', 'two', 'three', 'four'), self.voice,
                                             self.directory, cache=cache)
        self.assertEqual(len(cache), 3)
        self.assertEqual(provider.active, 0)
        self.assertEqual(Counter(text for text, _ in provider.calls), {'bad': 3, 'two': 1, 'three': 1, 'four': 1})


if __name__ == '__main__':
    unittest.main()
