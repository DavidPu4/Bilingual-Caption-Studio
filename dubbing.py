"""Opt-in speech synthesis, timed audio assembly, and local dubbed-video export."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import wave

from captions import validate

SAMPLE_RATE = 24000
MAX_SPEED = 2.5
PAUSE_MODE_SPEED = 1.25
MAX_OUTPUT_SECONDS = 600
VOICES = {
    'Mandarin': {'Xiaoxiao': 'zh-CN-XiaoxiaoNeural', 'Yunxi': 'zh-CN-YunxiNeural'},
    'English': {'Jenny': 'en-US-JennyNeural', 'Guy': 'en-US-GuyNeural'},
    'Spanish': {'Elvira (Spain)': 'es-ES-ElviraNeural', 'Alvaro (Spain)': 'es-ES-AlvaroNeural'},
    'Hindi': {'Swara': 'hi-IN-SwaraNeural', 'Madhur': 'hi-IN-MadhurNeural'},
    'Arabic': {'Zariyah (Saudi Arabia)': 'ar-SA-ZariyahNeural', 'Hamed (Saudi Arabia)': 'ar-SA-HamedNeural'},
    'French': {'Denise (France)': 'fr-FR-DeniseNeural', 'Henri (France)': 'fr-FR-HenriNeural'},
    'Portuguese': {'Francisca (Brazil)': 'pt-BR-FranciscaNeural', 'Antonio (Brazil)': 'pt-BR-AntonioNeural'},
    'Russian': {'Svetlana': 'ru-RU-SvetlanaNeural', 'Dmitry': 'ru-RU-DmitryNeural'},
    'German': {'Katja': 'de-DE-KatjaNeural', 'Conrad': 'de-DE-ConradNeural'},
    'Japanese': {'Nanami': 'ja-JP-NanamiNeural', 'Keita': 'ja-JP-KeitaNeural'},
    'Korean': {'SunHi': 'ko-KR-SunHiNeural', 'InJoon': 'ko-KR-InJoonNeural'},
    'Italian': {'Elsa': 'it-IT-ElsaNeural', 'Diego': 'it-IT-DiegoNeural'},
    'Bengali': {'Nabanita (Bangladesh)': 'bn-BD-NabanitaNeural', 'Pradeep (Bangladesh)': 'bn-BD-PradeepNeural'},
    'Indonesian': {'Gadis': 'id-ID-GadisNeural', 'Ardi': 'id-ID-ArdiNeural'},
    'Urdu': {'Uzma (Pakistan)': 'ur-PK-UzmaNeural', 'Asad (Pakistan)': 'ur-PK-AsadNeural'},
    'Turkish': {'Emel': 'tr-TR-EmelNeural', 'Ahmet': 'tr-TR-AhmetNeural'},
    'Vietnamese': {'HoaiMy': 'vi-VN-HoaiMyNeural', 'NamMinh': 'vi-VN-NamMinhNeural'},
    'Tamil': {'Pallavi (India)': 'ta-IN-PallaviNeural', 'Valluvar (India)': 'ta-IN-ValluvarNeural'},
    'Thai': {'Premwadee': 'th-TH-PremwadeeNeural', 'Niwat': 'th-TH-NiwatNeural'},
    'Dutch': {'Colette (Netherlands)': 'nl-NL-ColetteNeural', 'Maarten (Netherlands)': 'nl-NL-MaartenNeural'},
}


class DubbingError(Exception):
    pass


def dubbing_signature(source_sha256, rows, voice, timing_mode='strict'):
    payload = json.dumps([source_sha256, validate(rows), voice, timing_mode], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def speech_slots(rows, duration):
    rows = validate(rows)
    if not rows:
        raise DubbingError('No translated captions are available to speak.')
    if not math.isfinite(duration) or not 0 < duration <= 300:
        raise DubbingError('Dubbing supports videos up to five minutes long.')
    if len(rows) > 200 or sum(len(row['text']) for row in rows) > 30000:
        raise DubbingError('Please use fewer than 201 captions and 30,000 text characters.')
    for index, row in enumerate(rows):
        if row['end'] > duration + 0.001:
            raise DubbingError(
                f"Caption {index + 1} ends at {row['end']:.3f}s, but the video ends at {duration:.3f}s. "
                'Use Fit caption times to video, or correct its timing in the editor.'
            )
    # Use a following silent gap if needed, but never overlap the next caption's start.
    return [(row, (rows[index + 1]['start'] if index + 1 < len(rows) else duration) - row['start'])
            for index, row in enumerate(rows)]


def tempo_filter(speed):
    if not math.isfinite(speed) or not 1 <= speed <= MAX_SPEED:
        raise DubbingError(f'Speech speed must be between 1 and {MAX_SPEED}.')
    filters = []
    while speed > 2:
        filters.append('atempo=2')
        speed /= 2
    filters.append(f'atempo={speed:.8f}')
    return ','.join(filters)


def run_ffmpeg(arguments, *, timeout=240):
    import imageio_ffmpeg
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, 'CREATE_NO_WINDOW') else {}
    try:
        process = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin',
                                  *map(str, arguments)], capture_output=True, timeout=timeout, **options)
    except subprocess.TimeoutExpired:
        raise DubbingError('Video processing took too long. Try a shorter clip.') from None
    except OSError:
        raise DubbingError('The bundled video processor could not start. Reinstall imageio-ffmpeg.') from None
    if process.returncode:
        detail = process.stderr.decode('utf-8', errors='replace')[-1000:]
        raise DubbingError(f'Video processing failed: {detail}')
    return process.stdout


def probe_video(path):
    import av
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise DubbingError('Upload a video file to create a dubbed MP4.')
            stream = container.streams.video[0]
            duration = (float(stream.duration * stream.time_base) if stream.duration is not None
                        else float(container.duration / av.time_base) if container.duration else 0)
            codec = stream.codec_context.name
    except DubbingError:
        raise
    except Exception:
        raise DubbingError('The uploaded video could not be read.') from None
    if not math.isfinite(duration) or not 0 < duration <= 300:
        raise DubbingError('Upload a readable video no longer than five minutes.')
    return duration, codec


async def synthesize_segments(rows, voice, directory, *, cache=None, progress=lambda message: None):
    from voice_synthesis import synthesize_segments as synthesize
    return await synthesize(rows, voice, directory, cache=cache, progress=progress)


def decode_speech(path):
    from faster_whisper.audio import decode_audio
    return decode_audio(str(path), sampling_rate=SAMPLE_RATE)


def assemble_audio(slots, files, duration, decoder=decode_speech, processor=run_ffmpeg):
    import numpy as np
    if len(slots) != len(files):
        raise DubbingError('Missing speech audio for one or more captions.')
    timeline = np.zeros(round(duration * SAMPLE_RATE), dtype=np.float32)
    sped_up = 0
    for index, ((row, available), path) in enumerate(zip(slots, files), 1):
        samples = decoder(path)
        if not len(samples):
            raise DubbingError(f'Caption {index} produced empty speech.')
        start = round(row['start'] * SAMPLE_RATE)
        # Round endpoints, rather than duration separately, to avoid a one-sample overlap.
        end = min(round((row['start'] + available) * SAMPLE_RATE), len(timeline))
        limit = end - start
        if limit <= 0:
            raise DubbingError(f'Caption {index} has no time available for speech.')
        if len(samples) > limit:
            speed = len(samples) / limit * 1.04  # atempo output length is approximate; allow headroom.
            if speed > MAX_SPEED:
                raise DubbingError(f'Caption {index} needs more than {MAX_SPEED}x speech speed. Choose Allow video pauses, shorten its text, or give it more time before the next caption.')
            raw = processor(['-i', path, '-vn', '-af', tempo_filter(speed), '-ar', SAMPLE_RATE,
                             '-ac', 1, '-f', 'f32le', 'pipe:1'])
            samples = np.frombuffer(raw, dtype='<f4')
            if len(samples) > limit:
                raise DubbingError(f'Caption {index} still does not fit. Shorten its text or extend the time before the next caption.')
            sped_up += 1
        timeline[start:start + len(samples)] = samples
    return timeline, sped_up


def assemble_flexible_audio(slots, files, duration, decoder=decode_speech, processor=run_ffmpeg):
    """Keep all speech, adding video holds at slot ends rather than truncating it.

    Each following cue shifts by the cumulative inserted silence-free holds.
    Original editor timestamps are not modified; the returned subtitle rows
    belong to the longer dubbed output only.
    """
    import numpy as np
    if len(slots) != len(files) or not slots:
        raise DubbingError('Missing speech audio for one or more captions.')
    source_samples = round(duration * SAMPLE_RATE)
    first_start = round(slots[0][0]['start'] * SAMPLE_RATE)
    pieces = [np.zeros(first_start, dtype=np.float32)]
    pauses, caption_rows = [], []
    added_samples = 0
    sped_up = 0
    for index, ((row, available), path) in enumerate(zip(slots, files), 1):
        samples = decoder(path)
        if not len(samples):
            raise DubbingError(f'Caption {index} produced empty speech.')
        start = round(row['start'] * SAMPLE_RATE)
        boundary = min(row['start'] + available, duration)
        end = min(round(boundary * SAMPLE_RATE), source_samples)
        limit = end - start
        if limit <= 0:
            raise DubbingError(f'Caption {index} has no time available for speech.')
        if len(samples) > limit:
            speed = min(PAUSE_MODE_SPEED, len(samples) / limit * 1.04)
            raw = processor(['-i', path, '-vn', '-af', tempo_filter(speed), '-ar', SAMPLE_RATE,
                             '-ac', 1, '-f', 'f32le', 'pipe:1'])
            samples = np.frombuffer(raw, dtype='<f4')
            if not len(samples):
                raise DubbingError(f'Caption {index} produced empty speech after timing adjustment.')
            sped_up += 1
        extension = max(0, len(samples) - limit)
        if (source_samples + added_samples + extension) / SAMPLE_RATE > MAX_OUTPUT_SECONDS:
            raise DubbingError('The translated video would exceed ten minutes. Use a shorter clip or shorten the translated captions.')
        caption_rows.append({
            'start': (start + added_samples) / SAMPLE_RATE,
            'end': (max(min(round(row['end'] * SAMPLE_RATE), end), start + len(samples)) + added_samples) / SAMPLE_RATE,
            'text': row['text'],
        })
        if extension:
            pauses.append({'at': boundary, 'duration': extension / SAMPLE_RATE})
        added_samples += extension
        piece = np.zeros(max(limit, len(samples)), dtype=np.float32)
        piece[:len(samples)] = samples
        pieces.append(piece)
    timeline = np.concatenate(pieces)
    return timeline, sped_up, pauses, validate(caption_rows)


def write_wave(path, samples):
    import numpy as np
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())


def mux_video(source, audio, output, duration, codec):
    video_options = ['-c:v', 'copy'] if codec == 'h264' else [
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
        '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2']
    run_ffmpeg(['-y', '-i', source, '-i', audio, '-map', '0:v:0', '-map', '1:a:0',
                *video_options, '-c:a', 'aac', '-b:a', '160k', '-t', f'{duration:.6f}',
                '-movflags', '+faststart', output])


def create_dubbed_video(video_bytes, suffix, rows, voice, *, consent=False, progress=lambda message: None,
                        timing_mode='strict', speech_cache=None):
    if timing_mode not in {'strict', 'pause'}:
        raise DubbingError('Choose Keep original timing or Allow video pauses.')
    if not consent:
        raise DubbingError('Confirm online voice generation before sending any caption text.')
    if suffix.lower() not in {'.mp4', '.webm'} or not 0 < len(video_bytes) <= 50 * 1024 * 1024:
        raise DubbingError('Upload an MP4 or WebM video of 50 MB or less.')
    if voice not in {value for group in VOICES.values() for value in group.values()}:
        raise DubbingError('Choose one of the provided language voices.')
    with tempfile.TemporaryDirectory(prefix='caption-studio-dub-') as folder:
        directory = Path(folder)
        source = directory / ('input' + suffix.lower())
        source.write_bytes(video_bytes)
        duration, codec = probe_video(source)
        slots = speech_slots(rows, duration)
        progress(f'Generating {len(slots)} translated speech segments...')
        synthesis_options = ({'cache': speech_cache, 'progress': progress} if speech_cache is not None else {})
        files = asyncio.run(synthesize_segments([row for row, _ in slots], voice, directory, **synthesis_options))
        progress('Fitting speech to the video timeline...')
        pauses = []
        caption_rows = [row for row, _ in slots]
        if timing_mode == 'pause':
            timeline, sped_up, pauses, caption_rows = assemble_flexible_audio(slots, files, duration)
        else:
            timeline, sped_up = assemble_audio(slots, files, duration)
        output_duration = len(timeline) / SAMPLE_RATE if pauses else duration
        audio = directory / 'translated-audio.wav'
        write_wave(audio, timeline)
        if pauses:
            progress(f'Adding {len(pauses)} video pause(s) so every translated line can finish...')
        progress('Creating the dubbed MP4...')
        output = directory / 'dubbed.mp4'
        if pauses:
            from dubbing_video import mux_paused_video
            mux_paused_video(source, audio, output, duration, pauses, output_duration)
        else:
            mux_video(source, audio, output, output_duration, codec)
        if output.stat().st_size > 200 * 1024 * 1024:
            raise DubbingError('The dubbed output is too large. Try a shorter video.')
        return {'video': output.read_bytes(), 'audio': audio.read_bytes(),
                'segments': len(slots), 'sped_up_segments': sped_up, 'duration_seconds': output_duration,
                'source_duration_seconds': duration, 'timing_mode': timing_mode,
                'paused_segments': len(pauses), 'added_seconds': sum(pause['duration'] for pause in pauses),
                'caption_rows': caption_rows, 'voice': voice}
