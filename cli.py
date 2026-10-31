"""Reproducible multilingual inference and source-transcription CER evaluation."""
import argparse
import json
from pathlib import Path

from captions import character_error_rate, project_json, subtitles
from dubbing import probe_video
from engine import decode_media, generate, load_model, translation_model_for
from languages import LANGUAGES, default_target, language_name, validate_pair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('media', type=Path)
    parser.add_argument('--model', choices=['tiny', 'base', 'small'], default='base')
    parser.add_argument('--source-language', choices=list(LANGUAGES), default='zh')
    parser.add_argument('--target-language', choices=list(LANGUAGES), help='Defaults to English, or Mandarin for English input')
    parser.add_argument('--reference', type=Path, help='UTF-8 manually verified transcript in the recording language')
    parser.add_argument('--output', type=Path, default=Path('local-results/output'))
    args = parser.parse_args()
    target_language = args.target_language or default_target(args.source_language)
    validate_pair(args.source_language, target_language)
    audio, duration = decode_media(str(args.media))
    caption_duration = duration
    if args.media.suffix.lower() in {'.mp4', '.webm'}:
        video_duration, _ = probe_video(args.media)
        caption_duration = min(duration, video_duration)
    print('Loading model (first run downloads model files)...', flush=True)
    model = load_model(args.model)
    tracks, elapsed = generate(model, audio, progress=lambda message: print(message, flush=True),
                               source_language=args.source_language, target_language=target_language,
                               duration_seconds=caption_duration)
    metrics = {'model': args.model, 'device': 'cpu', 'compute_type': 'int8',
               'duration_seconds': duration, 'caption_duration_seconds': caption_duration,
               'inference_seconds': elapsed,
               'real_time_factor': elapsed / duration,
               'source': args.media.name, 'sample': False}
    metrics['source_language'] = args.source_language
    metrics['target_language'] = target_language
    metrics['translation_model'] = translation_model_for(args.source_language, target_language)
    if args.reference:
        source_name = language_name(args.source_language)
        metrics[f'{source_name.lower()}_cer'] = character_error_rate(args.reference.read_text(encoding='utf-8'),
                                                      ''.join(row['text'] for row in tracks[source_name]))
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in tracks.items():
        for format in ['srt', 'vtt']:
            (args.output / f'{name.lower()}.{format}').write_text(subtitles(rows, format), encoding='utf-8')
    (args.output / 'project.json').write_text(project_json(tracks, metrics), encoding='utf-8')
    (args.output / 'metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    print(json.dumps(metrics, ensure_ascii=True, indent=2))
    print(f'Wrote captions and project to {args.output.resolve()}')


if __name__ == '__main__':
    main()
