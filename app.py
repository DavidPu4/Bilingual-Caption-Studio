"""Run with: python -m streamlit run app.py"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

import pandas as pd
import streamlit as st

from captions import character_error_rate, load_project, project_json, subtitles, validate
from engine import decode_media, generate, load_model, load_translator, translation_model_for
from languages import LANGUAGES, default_target, language_code, language_name
from playback import video_subtitles
from dubbing_ui import render_dubbing
from dubbing import probe_video

st.set_page_config(page_title='Bilingual Caption Studio', page_icon='🎧', layout='wide')
st.title('Bilingual Caption Studio')
st.write(f'Create translated captions and dubbed videos across {len(LANGUAGES)} languages.')
st.caption('Local captioning · Optional audio dubbing · Subtitle and video export')


@st.cache_resource
def cached_model(size):
    return load_model(size)


@st.cache_resource
def cached_translator(source_language, target_language):
    return load_translator(source_language, target_language)


def install_project(tracks, metadata):
    st.session_state.tracks = tracks
    st.session_state.metadata = metadata
    st.session_state.revision = st.session_state.get('revision', 0) + 1


with st.sidebar:
    st.header('Project')
    source_name = st.selectbox('Recording language', list(LANGUAGES.values()),
                               index=list(LANGUAGES).index('zh'), key='source_language_choice')
    source_language = language_code(source_name)
    target_options = [name for code, name in LANGUAGES.items() if code != source_language]
    if st.session_state.get('target_language_choice') not in target_options:
        st.session_state.target_language_choice = language_name(default_target(source_language))
    target_name = st.selectbox('Translate to', target_options, key='target_language_choice')
    target_language = language_code(target_name)
    size = st.selectbox('Model', ['tiny', 'base', 'small'], index=2,
                        help='Small translated our synthetic smoke-test clip best. Tiny is fastest but made meaning errors. Check quality on your recordings.')
    st.caption('The first run downloads the selected model. Later inference runs locally. CPU speed depends on your computer.')
    if target_language != 'en':
        st.caption('Translation models download on first use. The multilingual model is larger and may take more memory and CPU time. Translation quality varies by language and recording.')
    if st.button('Open sample captions', width='stretch'):
        data = json.loads((Path(__file__).parent / 'sample_project.json').read_text(encoding='utf-8'))
        install_project(data['tracks'], data['metadata'])
    restored = st.file_uploader('Restore saved project', type=['json'])
    if st.button('Restore', disabled=restored is None, width='stretch'):
        try:
            if restored.size > 2 * 1024 * 1024:
                raise ValueError('Project files must be under 2 MB.')
            tracks, metadata = load_project(restored.getvalue().decode('utf-8'))
            install_project(tracks, metadata)
        except (ValueError, UnicodeDecodeError) as exc:
            st.error(str(exc))
    st.divider()
    st.caption('Sample captions are hand-written interface examples, not model output. Restoring or generating replaces the current editor. Download your project to keep edits.')

uploaded = st.file_uploader(f'Upload a recording in {source_name} (up to 50 MB / 5 minutes)',
                            key='recording_upload',
                            type=['wav', 'mp3', 'm4a', 'flac', 'ogg', 'mp4', 'webm'])
source_hash = None
video_preview = None
video_controls = None
if uploaded is not None:
    if uploaded.size > 50 * 1024 * 1024:
        st.error('File exceeds 50 MB. Please upload a smaller recording.')
    else:
        source_hash = hashlib.sha256(uploaded.getvalue()).hexdigest()
        if Path(uploaded.name).suffix.lower() in {'.mp4', '.webm'}:
            video_controls = st.empty()
            video_preview = st.empty()
            video_preview.video(uploaded.getvalue())
        else:
            st.audio(uploaded.getvalue())

if st.button('Generate captions', type='primary', disabled=source_hash is None):
    with st.status('Preparing local model...', expanded=True) as status:
        try:
            # Close the file before decoding so Windows does not hold an exclusive lock.
            with tempfile.TemporaryDirectory(prefix='caption-studio-') as directory:
                path = Path(directory) / ('input' + Path(uploaded.name).suffix.lower())
                path.write_bytes(uploaded.getvalue())
                audio, duration = decode_media(str(path))
                caption_duration = (min(duration, probe_video(path)[0])
                                    if path.suffix in {'.mp4', '.webm'} else duration)
                model = cached_model(size)
                if target_language != 'en':
                    st.write(f'Loading {source_name}-to-{target_name} translation model...')
                translator = cached_translator(source_language, target_language) if target_language != 'en' else None
                tracks, elapsed = generate(model, audio, progress=st.write,
                                           source_language=source_language, target_language=target_language,
                                           translator=translator, duration_seconds=caption_duration)
            install_project(tracks, {'source': uploaded.name, 'source_sha256': source_hash,
                                     'source_language': source_language,
                                     'target_language': target_language,
                                     'translation_model': translation_model_for(source_language, target_language),
                                     'model': size, 'device': 'cpu', 'compute_type': 'int8',
                                     'duration_seconds': round(duration, 3),
                                     'caption_duration_seconds': caption_duration,
                                     'inference_seconds': elapsed, 'sample': False})
            status.update(label='Caption generation complete', state='complete', expanded=False)
        except Exception as exc:
            status.update(label='Generation failed', state='error')
            st.error(f'{type(exc).__name__}: {exc}')
            st.info('Check the recording has an audio track. First use requires internet to download model files. Try tiny if memory is limited. Your previous captions are preserved.')

if 'tracks' not in st.session_state:
    st.info('Upload a short clip to generate captions, or open sample captions to try editing and export immediately.')
    st.stop()

metadata = st.session_state.metadata
if metadata.get('sample'):
    st.warning('Sample project: hand-written captions with illustrative timings. This is not an inference result.')
elif source_hash and metadata.get('source_sha256') != source_hash:
    st.warning('The editor belongs to a different recording. Generate captions to replace it with the selected upload.')
project_source = metadata.get('source_language', 'zh')
project_target = metadata.get('target_language', default_target(project_source))
project_source_name = language_name(project_source)
project_target_name = language_name(project_target)
if not metadata.get('sample') and (project_source != source_language or project_target != target_language):
    st.info(f'The editor contains {project_source_name} → {project_target_name} captions. Generate again to apply the selected {source_name} → {target_name} languages.')
if project_target != 'en':
    st.caption(f'{project_target_name} translations inherit the {project_source_name} caption timings. Editing one track does not automatically update the other. Times are seconds.')
else:
    st.caption(f'{project_source_name} and {project_target_name} are generated independently and may have different segment boundaries. Times are seconds. Export each language as its own subtitle track.')

track_names = list(st.session_state.tracks)
cols = st.columns(3)
for column, name in zip(cols, track_names):
    column.metric(f'{name} captions', len(st.session_state.tracks[name]))
cols[2].metric('Inference time', f"{metadata.get('inference_seconds', '—')} s")

valid_tracks = {}
for name, tab in zip(track_names, st.tabs([f'{name} captions' for name in track_names])):
    with tab:
        frame = pd.DataFrame(st.session_state.tracks[name], columns=['start', 'end', 'text'])
        edited = st.data_editor(frame, hide_index=True, num_rows='dynamic', width='stretch',
                                key=f"editor_{name}_{st.session_state.revision}",
                                column_config={
                                    'start': st.column_config.NumberColumn('Start (s)', min_value=0.0, step=0.01, required=True),
                                    'end': st.column_config.NumberColumn('End (s)', min_value=0.0, step=0.01, required=True),
                                    'text': st.column_config.TextColumn('Caption', required=True, width='large')})
        rows = edited.to_dict('records')
        try:
            valid_tracks[name] = validate(rows)
            left, right = st.columns(2)
            left.download_button('Download SRT', subtitles(rows, 'srt').encode('utf-8'),
                                 file_name=f'captions-{name.lower()}.srt', mime='application/x-subrip', key=f'srt_{name}')
            right.download_button('Download WebVTT', subtitles(rows, 'vtt').encode('utf-8'),
                                  file_name=f'captions-{name.lower()}.vtt', mime='text/vtt', key=f'vtt_{name}')
        except ValueError as exc:
            st.error(str(exc))

if video_preview is not None:
    # Follow the saved editor's languages, even after the sidebar selection changes.
    # The control placeholder keeps it above the video while resolving it after generation.
    preview_names = (['Mandarin', 'English'] if set(track_names) == {'Mandarin', 'English'}
                     else [project_target_name, project_source_name])
    preview_options = [name for name in preview_names if name in track_names] + ['Off']
    if st.session_state.get('preview_language') not in preview_options:
        st.session_state.preview_language = preview_options[0]
    with video_controls.container():
        preview_language = st.radio('Captions on video', preview_options,
                                   horizontal=True, key='preview_language')
    preview_tracks = video_subtitles(valid_tracks, metadata, source_hash, preview_language)
    video_preview.video(uploaded.getvalue(), subtitles=preview_tracks)
    if preview_tracks:
        st.caption(f'{preview_language} captions appear over the video at their timestamps. Edit a caption or timing above to update the preview. The player’s CC menu can also hide captions.')
    elif preview_language != 'Off':
        if metadata.get('source_sha256') != source_hash or metadata.get('sample'):
            st.info('Generate captions for this uploaded video to show them during playback.')
        elif preview_language not in valid_tracks:
            st.info(f'Fix the invalid {preview_language} captions to show them on the video.')
        else:
            st.info(f'No {preview_language} captions were found in this recording.')

if len(valid_tracks) == len(track_names):
    st.download_button('Save editable project (.json)', project_json(valid_tracks, metadata).encode('utf-8'),
                       file_name='caption-project.json', mime='application/json')

render_dubbing(uploaded, metadata, source_hash, valid_tracks, install_project=install_project)

with st.expander(f'Evaluate {project_source_name} transcription'):
    st.write('Paste a manually verified transcript of the same recording. CER measures source transcription, not translation quality. Normalization uses NFKC, lowercasing, and removal of whitespace/punctuation; simplified and traditional characters remain distinct.')
    reference = st.text_area(f'Reference {project_source_name} transcript', key=f'reference_{project_source}')
    if st.button('Calculate character error rate'):
        try:
            if project_source_name not in valid_tracks:
                raise ValueError(f'Fix invalid {project_source_name} caption rows before evaluating.')
            hypothesis = ''.join(row['text'] for row in valid_tracks[project_source_name])
            score = character_error_rate(reference, hypothesis)
            st.metric('Character error rate (lower is better)', f'{score:.1%}')
            st.caption('This scores the current edited text. Evaluate before corrections to measure the model. CER can exceed 100% when there are many insertions.')
        except ValueError as exc:
            st.error(str(exc))

with st.expander('Run details'):
    st.json(metadata)
