"""Reproducible local inference and optional Mandarin CER evaluation."""
import argparse
import json
from pathlib import Path

from captions import character_error_rate, project_json, subtitles
from engine import TRANSLATION_MODEL, decode_media, generate, load_model, load_translator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('media', type=Path)
    parser.add_argument('--model', choices=['tiny', 'base', 'small'], default='base')
    parser.add_argument('--source-language', choices=['zh', 'en'], default='zh')
    parser.add_argument('--reference', type=Path, help='UTF-8 manually verified transcript in the recording language')
    parser.add_argument('--output', type=Path, default=Path('local-results/output'))
    args = parser.parse_args()
    audio, duration = decode_media(str(args.media))
    print('Loading model (first run downloads model files)...', flush=True)
    model = load_model(args.model)
    translator = load_translator() if args.source_language == 'en' else None
    tracks, elapsed = generate(model, audio, progress=lambda message: print(message, flush=True),
                               source_language=args.source_language, translator=translator)
    metrics = {'model': args.model, 'device': 'cpu', 'compute_type': 'int8',
               'duration_seconds': duration, 'inference_seconds': elapsed,
               'real_time_factor': elapsed / duration,
               'source': args.media.name, 'sample': False}
    metrics['source_language'] = args.source_language
    metrics['translation_model'] = TRANSLATION_MODEL if args.source_language == 'en' else 'Whisper speech translation'
    if args.reference:
        source_name = 'English' if args.source_language == 'en' else 'Mandarin'
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
