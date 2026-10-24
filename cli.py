"""Reproducible local inference and optional Mandarin CER evaluation."""
import argparse
import json
from pathlib import Path

from captions import character_error_rate, project_json, subtitles
from engine import decode_media, generate, load_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('media', type=Path)
    parser.add_argument('--model', choices=['tiny', 'base', 'small'], default='base')
    parser.add_argument('--reference', type=Path, help='UTF-8 manually verified Mandarin transcript')
    parser.add_argument('--output', type=Path, default=Path('local-results/output'))
    args = parser.parse_args()
    audio, duration = decode_media(str(args.media))
    print('Loading model (first run downloads model files)...', flush=True)
    model = load_model(args.model)
    tracks, elapsed = generate(model, audio, progress=lambda message: print(message, flush=True))
    metrics = {'model': args.model, 'device': 'cpu', 'compute_type': 'int8',
               'duration_seconds': duration, 'inference_seconds': elapsed,
               'real_time_factor': elapsed / duration,
               'source': args.media.name, 'sample': False}
    if args.reference:
        metrics['mandarin_cer'] = character_error_rate(args.reference.read_text(encoding='utf-8'),
                                                      ''.join(row['text'] for row in tracks['Mandarin']))
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
