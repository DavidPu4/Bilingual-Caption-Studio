"""Language routing, model reuse and tokenizer isolation without model downloads."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import patch

import engine
from languages import LANGUAGES, default_target, language_code, language_name, validate_pair


class SpeechModel:
    def __init__(self, silent=False):
        self.calls = []
        self.silent = silent

    def transcribe(self, audio, **options):
        self.calls.append(options)
        text = 'source text' if options['task'] == 'transcribe' else 'English text'
        rows = [] if self.silent else [SimpleNamespace(start=.5, end=2, text=text)]
        return iter(rows), None


class Row(list):
    def __getitem__(self, key):
        value = super().__getitem__(key)
        return Row(value) if isinstance(key, slice) else value

    def tolist(self):
        return list(self)


class FakeTokenizer:
    def __init__(self):
        self.src_lang = 'en'
        self.width = 3
        self.records = []

    def get_lang_id(self, code):
        return list(LANGUAGES).index(code) + 100

    def __call__(self, batch, **options):
        self.records.append(('encode', self.src_lang))
        return {'input_ids': SimpleNamespace(shape=(len(batch), self.width), source=self.src_lang)}

    def batch_decode(self, outputs, **options):
        self.records.append(('decode', self.src_lang))
        names = list(LANGUAGES)
        return [self.src_lang + '>' + names[row[1] - 100] for row in outputs]


class FakeTextModel:
    def __init__(self):
        self.config = SimpleNamespace(max_position_embeddings=32, eos_token_id=2)
        self.calls = []
        self.finish = True

    def eval(self):
        return self

    def generate(self, *, input_ids, **options):
        self.calls.append(options)
        # Let simultaneous callers overlap if the shared tokenizer isn't locked.
        time.sleep(.01)
        eos = 2 if self.finish else 3
        target = options.get('forced_bos_token_id', list(LANGUAGES).index('zh') + 100)
        return [Row([2, target, eos]) for _ in range(input_ids.shape[0])]


class MultilingualEngineTests(unittest.TestCase):
    def test_twenty_registry_codes_and_bidirectional_names(self):
        self.assertEqual(len(LANGUAGES), 20)
        self.assertEqual(len(set(LANGUAGES.values())), 20)
        for code, name in LANGUAGES.items():
            self.assertEqual(language_name(code), name)
            self.assertEqual(language_code(name), code)
            self.assertNotEqual(default_target(code), code)
        self.assertEqual(default_target('en'), 'zh')
        self.assertEqual(default_target('fr'), 'en')

    def test_invalid_codes_names_and_same_language_are_explicit_errors(self):
        for value in ['xx', '', None, [], 0]:
            with self.subTest(code=value), self.assertRaises(ValueError):
                language_name(value)
        for value in ['Unknown', None, []]:
            with self.subTest(name=value), self.assertRaises(ValueError):
                language_code(value)
        for code in LANGUAGES:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, 'different'):
                validate_pair(code, code)

    def test_every_source_to_english_uses_whisper_speech_translation(self):
        for source, name in LANGUAGES.items():
            if source == 'en':
                continue
            with self.subTest(source=source):
                model = SpeechModel()
                with patch('engine.load_translator') as loader:
                    tracks, _ = engine.generate(model, object(), source_language=source, target_language='en')
                loader.assert_not_called()
                self.assertEqual(list(tracks), [name, 'English'])
                self.assertEqual([call['task'] for call in model.calls], ['transcribe', 'translate'])
                self.assertTrue(all(call['language'] == source for call in model.calls))
                self.assertEqual(tracks['English'][0]['text'], 'English text')

    def test_all_non_english_targets_use_transcribed_text_and_preserve_timing(self):
        for source in LANGUAGES:
            for target in LANGUAGES:
                if source == target or target == 'en':
                    continue
                with self.subTest(source=source, target=target):
                    received = []
                    def translate(texts):
                        received.extend(texts)
                        return ['translated text']
                    model = SpeechModel()
                    tracks, _ = engine.generate(model, object(), source_language=source,
                                                target_language=target, translator=translate)
                    self.assertEqual(received, ['source text'])
                    self.assertEqual(len(model.calls), 1)
                    self.assertEqual(tracks[language_name(target)][0], {
                        'start': .5, 'end': 2., 'text': 'translated text'})

    def test_legacy_defaults_remain_mandarin_to_english_and_reverse(self):
        tracks, _ = engine.generate(SpeechModel(), object())
        self.assertEqual(set(tracks), {'Mandarin', 'English'})
        tracks, _ = engine.generate(SpeechModel(), object(), source_language='en', translator=lambda _: ['你好'])
        self.assertEqual(tracks['Mandarin'][0]['text'], '你好')
        self.assertEqual(engine.translation_model_for('zh'), 'Whisper speech translation')
        self.assertEqual(engine.translation_model_for('en'), engine.TRANSLATION_MODEL)
        self.assertEqual(engine.translation_model_for('fr', 'de'), engine.MULTILINGUAL_TRANSLATION_MODEL)

    def test_invalid_pair_is_rejected_before_transcribing(self):
        for source, target in [('xx', 'en'), ('en', 'en'), ('en', 'xx'), ('en', ''), ('en', 0)]:
            with self.subTest(source=source, target=target), self.assertRaises(ValueError):
                engine.generate(object(), object(), source_language=source, target_language=target)

    def test_silent_recording_does_not_load_text_translator(self):
        with patch('engine.load_translator') as loader:
            tracks, _ = engine.generate(SpeechModel(silent=True), object(), source_language='fr', target_language='ja')
        loader.assert_not_called()
        self.assertEqual(tracks, {'French': [], 'Japanese': []})

    def test_lazy_loader_receives_the_selected_pair_and_bad_output_fails(self):
        with patch('engine.load_translator', return_value=lambda _: ['Bonjour']) as loader:
            tracks, _ = engine.generate(SpeechModel(), object(), source_language='de', target_language='fr')
        loader.assert_called_once_with('de', 'fr')
        self.assertEqual(tracks['French'][0]['text'], 'Bonjour')
        for translator in [lambda _: [], lambda _: [''], lambda _: ['first', 'extra']]:
            with self.subTest(translator=translator), self.assertRaises(ValueError):
                engine.generate(SpeechModel(), object(), source_language='de', target_language='fr', translator=translator)

    def test_pair_translators_share_one_downloaded_model(self):
        tokenizer = FakeTokenizer()
        model = FakeTextModel()
        factory_calls = []
        def tokenizer_factory(name):
            factory_calls.append(('tokenizer', name))
            return tokenizer
        def model_factory(name):
            factory_calls.append(('model', name))
            return model
        modules = {'torch': SimpleNamespace(inference_mode=nullcontext), 'transformers': SimpleNamespace(
            M2M100Tokenizer=SimpleNamespace(from_pretrained=tokenizer_factory),
            M2M100ForConditionalGeneration=SimpleNamespace(from_pretrained=model_factory))}
        with patch.dict('sys.modules', modules), patch.dict(engine._TRANSLATION_RESOURCES, {}, clear=True):
            first = engine.load_translator('en', 'es')
            second = engine.load_translator('fr', 'de')
            self.assertEqual(first(['hello']), ['en>es'])
            self.assertEqual(second(['bonjour']), ['fr>de'])
        self.assertEqual(len(factory_calls), 2)
        self.assertTrue(all(name == engine.MULTILINGUAL_TRANSLATION_MODEL for _, name in factory_calls))

    def test_parallel_pair_calls_keep_shared_tokenizer_languages_isolated(self):
        resources = (SimpleNamespace(inference_mode=nullcontext), FakeTokenizer(), FakeTextModel(), threading.RLock())
        with patch('engine._translation_resources', return_value=resources):
            translators = [(source, target, engine.load_translator(source, target))
                           for source, target in [('en', 'es'), ('fr', 'de'), ('ja', 'ko')]]
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [(source, target, pool.submit(translator, ['caption'] * 9))
                           for source, target, translator in translators]
                for source, target, result in futures:
                    self.assertEqual(result.result(timeout=3), [source + '>' + target] * 9)

    def test_translation_rejects_overlong_inputs_and_incomplete_generation(self):
        tokenizer, model = FakeTokenizer(), FakeTextModel()
        resources = (SimpleNamespace(inference_mode=nullcontext), tokenizer, model, threading.RLock())
        with patch('engine._translation_resources', return_value=resources):
            translator = engine.load_translator('en', 'es')
            tokenizer.width = 33
            with self.assertRaisesRegex(ValueError, 'too long'):
                translator(['caption'])
            tokenizer.width = 3
            model.finish = False
            with self.assertRaisesRegex(ValueError, 'length limit'):
                translator(['caption'])
            for value in ['caption', [None], ['']]:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    translator(value)


if __name__ == '__main__':
    unittest.main()
