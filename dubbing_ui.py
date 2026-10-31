"""Dubbing controls are separate from the offline caption workflow."""
from pathlib import Path
import logging
import tempfile

import streamlit as st

from captions import fit_captions_to_duration, subtitles
from dubbing import VOICES, DubbingError, create_dubbed_video, dubbing_signature, probe_video
from languages import default_target, language_name


@st.cache_data(show_spinner=False, max_entries=4)
def cached_video_duration(source_hash, suffix, _video_bytes):
    """Probe locally once per recording, without caching the uploaded bytes."""
    with tempfile.TemporaryDirectory(prefix='caption-studio-probe-') as directory:
        path = Path(directory) / ('input' + suffix)
        path.write_bytes(_video_bytes)
        return probe_video(path)[0]


def clear_speech_cache():
    st.session_state.pop('dub_speech_cache', None)
    st.session_state.pop('dub_speech_cache_source', None)


def render_dubbing(uploaded, metadata, source_hash, valid_tracks, install_project=None):
    if uploaded is None or Path(uploaded.name).suffix.lower() not in {'.mp4', '.webm'}:
        clear_speech_cache()
        return
    if st.session_state.get('dub_speech_cache_source') != source_hash:
        clear_speech_cache()
    source_language = metadata.get('source_language', 'zh')
    target = language_name(metadata.get('target_language', default_target(source_language)))
    with st.expander(f'Translate the video audio to {target}', expanded=True):
        st.write(f'Create a new video with a standard synthetic {target} voice. The original audio, including music and background sound, will be replaced.')
        if metadata.get('sample') or metadata.get('source_sha256') != source_hash:
            st.info('Generate captions for this uploaded video first, then create its translated audio.')
            st.session_state.pop('dubbed_result', None)
            clear_speech_cache()
            return
        if 'dub_speech_cache' not in st.session_state:
            st.session_state.dub_speech_cache = {}
            st.session_state.dub_speech_cache_source = source_hash
        speech_cache = st.session_state.dub_speech_cache
        rows = valid_tracks.get(target)
        if not rows:
            st.info(f'Add or fix the {target} captions first. The voice will read the current edited text.')
            st.session_state.pop('dubbed_result', None)
            return
        available_voices = VOICES.get(target, {})
        if not available_voices:
            st.info(f'Audio dubbing is not available for {target}. You can still edit and export its captions.')
            st.session_state.pop('dubbed_result', None)
            return
        try:
            duration = cached_video_duration(source_hash, Path(uploaded.name).suffix.lower(), uploaded.getvalue())
        except DubbingError as exc:
            st.error(str(exc))
            st.session_state.pop('dubbed_result', None)
            return
        overlong = [(name, index, row) for name in [target, *[name for name in valid_tracks if name != target]]
                    for index, row in enumerate(valid_tracks.get(name, []), 1)
                    if row['end'] > duration + .001]
        target_overlong = any(name == target for name, _, _ in overlong)
        if not overlong and metadata.get('timing_note'):
            st.success(metadata['timing_note'])
        if overlong:
            name, index, row = overlong[0]
            st.warning(f'{name} caption {index} ends at {row["end"]:.3f}s, but this video ends at '
                       f'{duration:.3f}s. Fit the caption times to the video or edit the timing before dubbing.')
            repaired_tracks = {}
            repair_error = None
            if set(valid_tracks) != set(st.session_state.get('tracks', valid_tracks)):
                repair_error = 'Fix invalid caption rows in every track before fitting the times.'
            else:
                try:
                    for name, track in valid_tracks.items():
                        repaired_tracks[name], _ = fit_captions_to_duration(track, duration)
                except ValueError as exc:
                    repair_error = str(exc)
            if st.button('Fit caption times to video', key='fit_caption_times',
                         disabled=install_project is None or repair_error is not None):
                install_project(repaired_tracks, {**metadata, 'caption_duration_seconds': duration,
                                                 'timing_note': 'Caption ends fitted to the video duration; text and starts preserved.'})
                st.session_state.pop('dubbed_result', None)
                st.rerun()
            if repair_error:
                st.info(repair_error)
        voice_label = st.selectbox('Dubbing voice', list(available_voices), key=f'dub_voice_{target}')
        voice = available_voices[voice_label]
        timing_label = st.radio('Speech timing', ['Allow video pauses', 'Keep original timing'],
                               key='dub_timing_mode')
        timing_mode = 'pause' if timing_label == 'Allow video pauses' else 'strict'
        if timing_mode == 'pause':
            st.caption('Recommended: the picture pauses when translated speech needs more room. '
                       'The exported video may be longer, and its captions follow the new timing.')
        else:
            st.caption('Keep the original video length. Speech can speed up to 2.5×; longer translations '
                       'may need shorter text or timing edits.')
        st.caption('Voice generation uses Microsoft’s online speech service through edge-tts. Only translated caption text is sent; your video stays on this computer. No account or billing setup is required.')
        st.caption('Retries reuse any voice audio already generated for this recording.')
        consent = st.checkbox('Allow sending the translated captions to Microsoft to generate speech',
                               key='dub_online_consent')
        signature = dubbing_signature(source_hash, rows, voice, timing_mode=timing_mode)
        result = st.session_state.get('dubbed_result')
        if result and result['signature'] != signature:
            st.session_state.pop('dubbed_result', None)
            result = None
        if st.button(f'Create {target} dubbed video', type='primary', disabled=not consent or target_overlong,
                     key='create_dubbed_video'):
            result = None
            st.session_state.pop('dubbed_result', None)
            try:
                with st.status('Creating translated audio...', expanded=True) as status:
                    result = create_dubbed_video(uploaded.getvalue(), Path(uploaded.name).suffix,
                                                  rows, voice, timing_mode=timing_mode,
                                                  consent=consent, progress=st.write,
                                                  speech_cache=speech_cache)
                    result['signature'] = signature
                    st.session_state.dubbed_result = result
                    status.update(label='Dubbed video ready', state='complete', expanded=False)
            except DubbingError as exc:
                status.update(label='Dubbing failed', state='error', expanded=True)
                st.error(str(exc))
            except Exception as exc:
                # Do not log caption text or provider exception messages.
                logging.getLogger(__name__).error('Unexpected dubbing failure (%s)', type(exc).__name__)
                status.update(label='Dubbing failed', state='error', expanded=True)
                st.error('The dubbed video could not be created. Check the clip and try again. Your captions are preserved.')
        if result:
            dubbed_rows = result.get('caption_rows', rows)
            st.video(result['video'], subtitles={target: subtitles(dubbed_rows, 'vtt')})
            left, middle, right = st.columns(3)
            left.download_button('Download dubbed MP4', result['video'], file_name=f'dubbed-{target.lower()}.mp4',
                                  mime='video/mp4', key='download_dubbed_video')
            middle.download_button('Download translated audio', result['audio'], file_name=f'dubbed-{target.lower()}.wav',
                                   mime='audio/wav', key='download_dubbed_audio')
            right.download_button('Download dubbed captions (SRT)', subtitles(dubbed_rows, 'srt').encode('utf-8'),
                                  file_name=f'dubbed-{target.lower()}.srt', mime='application/x-subrip',
                                  key='download_dubbed_captions')
            if result.get('timing_mode') == 'pause':
                st.caption(f"{result.get('paused_segments', 0)} video pauses; "
                           f"{result.get('added_seconds', 0):.2f} seconds added. "
                           'Use the dubbed captions download with this video; the editor retains the original recording timing.')
            st.caption(f"{result['segments']} speech segments; {result['sped_up_segments']} adjusted to fit. Timing is approximate. This version does not synchronize lip movements or preserve the original speaker’s voice.")
