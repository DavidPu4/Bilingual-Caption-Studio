"""Render pauses at source-video timestamps while replacement speech finishes."""
from __future__ import annotations

import math
from pathlib import Path
import tempfile


OUTPUT_FPS = 30


def _finite_seconds(value):
    if isinstance(value, bool):
        raise ValueError
    seconds = float(value)
    if not math.isfinite(seconds):
        raise ValueError
    return seconds


def pause_filter(source_duration, pauses, output_duration):
    """Build a filter from numeric, bounded pause locations; no user text enters it."""
    from dubbing import DubbingError

    try:
        source_duration = _finite_seconds(source_duration)
        output_duration = _finite_seconds(output_duration)
        if not 0 < source_duration <= 300 or not source_duration <= output_duration <= 600:
            raise ValueError
        if not isinstance(pauses, (list, tuple)) or len(pauses) > 200:
            raise ValueError
        clean = []
        previous = 0.
        for pause in pauses:
            at = _finite_seconds(pause['at'])
            duration = _finite_seconds(pause['duration'])
            if not previous < at <= source_duration or duration <= 0:
                raise ValueError
            clean.append((at, duration))
            previous = at
        added = sum(duration for _, duration in clean)
        # The caller may round the final audio timeline up to a video frame.
        if not -.001 <= output_duration - source_duration - added <= 1 / OUTPUT_FPS + .001:
            raise ValueError
    except (ValueError, TypeError, KeyError, OverflowError):
        raise DubbingError('The translated video pause timing is invalid. Create the dubbed video again.') from None

    shifts = ''.join(f'+if(gte(T-STARTT,{at:.9f}),{duration:.9f}/TB,0)'
                     for at, duration in clean if at < source_duration)
    # A timestamp jump makes fps repeat the preceding frame until motion resumes.
    # Pad the end as well: no source frame exists at the final video-end boundary.
    # The output -t cap removes any unused padding after interior pauses.
    padding = max(added + 1 / OUTPUT_FPS, 1 / OUTPUT_FPS)
    return (f"setpts='PTS-STARTPTS{shifts}',fps={OUTPUT_FPS},"
            f'tpad=stop_mode=clone:stop_duration={padding:.9f},'
            'scale=trunc(iw/2)*2:trunc(ih/2)*2')


def mux_paused_video(source, audio, output, source_duration, pauses, output_duration):
    """Export one H.264 video stream and one replacement AAC stream locally."""
    from dubbing import run_ffmpeg

    filters = pause_filter(source_duration, pauses, output_duration)
    # A file keeps the command below Windows' argument-length limit at 200 cues.
    with tempfile.TemporaryDirectory(prefix='caption-studio-video-pauses-') as folder:
        script = Path(folder) / 'video-filter.txt'
        script.write_text(filters, encoding='utf-8')
        run_ffmpeg(['-y', '-i', source, '-i', audio,
                    '-map', '0:v:0', '-map', '1:a:0', '-filter_script:v', script,
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k',
                    '-t', f'{output_duration:.9f}', '-movflags', '+faststart', output])
