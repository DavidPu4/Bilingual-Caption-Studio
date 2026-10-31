"""Bounded voice retries and complete audio retained in session memory only."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import time

MAX_CACHE_BYTES = 32 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 45
TOTAL_TIMEOUT_SECONDS = 240
RETRY_DELAYS = (1, 3)


class _EmptyAudio(Exception):
    pass


class _LocalFileError(Exception):
    pass


def speech_cache_key(voice, text):
    # Neither text nor provider URLs appear in the session cache keys.
    payload = ['edge-tts-default-settings-v1', voice, text]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode('utf-8')).hexdigest()


def _cache_put(cache, key, audio):
    cache.pop(key, None)
    if len(audio) > MAX_CACHE_BYTES:
        return
    used = sum(len(value) for value in cache.values())
    while cache and used + len(audio) > MAX_CACHE_BYTES:
        oldest = next(iter(cache))
        used -= len(cache.pop(oldest))
    cache[key] = audio


def _failure_category(exc):
    """Classify without copying private text, URLs, or server exception messages."""
    if isinstance(exc, _LocalFileError):
        return 'local', False
    if isinstance(exc, (_EmptyAudio,)) or type(exc).__name__ == 'NoAudioReceived':
        return 'no_audio', True
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return 'timeout', True
    status = getattr(exc, 'status', None)
    if status == 429:
        return 'rate_limit', True
    if status == 403:
        return 'rejected', True
    if isinstance(status, int) and status >= 500:
        return 'service', True
    if isinstance(exc, (TypeError, ValueError)):
        return 'configuration', False
    if isinstance(exc, ConnectionError) or type(exc).__module__.startswith(('aiohttp', 'socket', 'ssl')):
        return 'connection', True
    if isinstance(exc, OSError):
        return 'local', False
    return 'service', True


def _failure_message(voice, index, exc, attempts, kept):
    label = voice.rsplit('-', 1)[-1].removesuffix('Neural')
    category, _ = _failure_category(exc)
    prefix = f'Voice generation failed for {label}, caption {index + 1}'
    details = {
        'no_audio': f'Microsoft returned no audio for {label}. This can depend on the voice, text, or service availability.',
        'timeout': 'The speech request timed out.',
        'rate_limit': 'Microsoft limited speech requests (HTTP 429). Wait before trying again.',
        'rejected': 'Microsoft rejected the speech request (HTTP 403).',
        'connection': 'The speech service connection failed.',
        'configuration': 'The speech request configuration was invalid.',
        'local': 'The temporary speech audio could not be written or read on this computer.',
        'service': 'The speech service did not complete the request.',
    }
    suffix = ' Try again later or choose another voice.' if category not in {'local', 'configuration'} else ''
    retained = f' {kept} completed segment(s) are kept in this session for retry.' if kept else ''
    return f'{prefix} after {attempts} attempt(s). {details[category]}{suffix}{retained}'


async def synthesize_segments(rows, voice, directory, *, cache=None, progress=lambda message: None):
    """Return every segment in order, or fail without returning partial speech.

    The first pass uses two concurrent requests. Later passes retry only missing
    segments sequentially after longer waits. Successful audio is cached only
    after the provider finishes normally, so failed partial files never survive
    as successful results. The caller owns and clears its session-memory cache.
    """
    from dubbing import DubbingError

    if not rows:
        return []
    import edge_tts

    directory = Path(directory)
    session_cache = cache if cache is not None else {}
    # Accept only complete immutable byte entries and enforce the limit even if
    # a caller passes a cache from an older application version.
    for key, value in list(session_cache.items()):
        if not isinstance(key, str) or not isinstance(value, bytes) or not value:
            session_cache.pop(key, None)
    while session_cache and sum(map(len, session_cache.values())) > MAX_CACHE_BYTES:
        session_cache.pop(next(iter(session_cache)))
    paths = [directory / f'speech-{index:04}.mp3' for index in range(len(rows))]
    keys = [speech_cache_key(voice, row['text']) for row in rows]
    failures = {}
    attempts = [0] * len(rows)
    finished = set()
    reused = 0
    for index, key in enumerate(keys):
        audio = session_cache.get(key)
        if audio is not None:
            try:
                paths[index].write_bytes(audio)
            except OSError:
                raise DubbingError(_failure_message(voice, index, _LocalFileError(), 0, 0)) from None
            finished.add(index)
            reused += 1
    if reused:
        progress(f'Reusing {reused} completed speech segment(s) from this session.')

    deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS

    async def attempt(index):
        attempts[index] += 1
        partial = directory / f'speech-{index:04}.partial.mp3'
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            communicator = edge_tts.Communicate(rows[index]['text'], voice)
            await asyncio.wait_for(communicator.save(str(partial)),
                                   timeout=min(REQUEST_TIMEOUT_SECONDS, remaining))
            try:
                audio = partial.read_bytes() if partial.exists() else b''
                if not audio:
                    raise _EmptyAudio
                partial.replace(paths[index])
            except OSError as exc:
                raise _LocalFileError from exc
            _cache_put(session_cache, keys[index], audio)
            finished.add(index)
            failures.pop(index, None)
        except Exception as exc:
            failures[index] = exc
        finally:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass

    semaphore = asyncio.Semaphore(2)

    async def first_pass(index):
        async with semaphore:
            await attempt(index)

    # Settle every initial request before retrying or leaving the temporary folder.
    await asyncio.gather(*(first_pass(index) for index in range(len(rows)) if index not in finished),
                         return_exceptions=True)
    for delay in RETRY_DELAYS:
        missing = [index for index, exc in failures.items() if _failure_category(exc)[1]]
        if not missing or deadline - time.monotonic() <= delay:
            break
        progress(f'Retrying {len(missing)} missing speech segment(s) one at a time...')
        await asyncio.sleep(delay)
        for index in sorted(missing):
            if time.monotonic() >= deadline:
                break
            await attempt(index)
    if failures:
        index = min(failures)
        kept = sum(key in session_cache for key in keys) if cache is not None else 0
        raise DubbingError(_failure_message(voice, index, failures[index], attempts[index], kept)) from None
    if len(finished) != len(rows):
        raise DubbingError('One or more speech requests did not complete. Try again.')
    return paths
