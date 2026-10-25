"""Dubbing controls are separate from the offline caption workflow."""
from pathlib import Path

import streamlit as st

from captions import subtitles
from dubbing import VOICES, DubbingError, create_dubbed_video, dubbing_signature


def render_dubbing(uploaded, metadata, source_hash, valid_tracks):
    if uploaded is None or Path(uploaded.name).suffix.lower() not in {'.mp4', '.webm'}:
        return
    target = 'Mandarin' if metadata.get('source_language', 'zh') == 'en' else 'English'
    with st.expander(f'Translate the video audio to {target}', expanded=True):
        st.write(f'Create a new video with a standard synthetic {target} voice. The original audio, including music and background sound, will be replaced.')
        if metadata.get('sample') or metadata.get('source_sha256') != source_hash:
            st.info('Generate captions for this uploaded video first, then create its translated audio.')
            st.session_state.pop('dubbed_result', None)
            return
        rows = valid_tracks.get(target)
        if not rows:
            st.info(f'Add or fix the {target} captions first. The voice will read the current edited text.')
            st.session_state.pop('dubbed_result', None)
            return
        voice_label = st.selectbox('Dubbing voice', list(VOICES[target]), key=f'dub_voice_{target}')
        voice = VOICES[target][voice_label]
        st.caption('Voice generation uses Microsoft’s online speech service through edge-tts. Only translated caption text is sent; your video stays on this computer. No account or billing setup is required.')
        consent = st.checkbox('Allow sending the translated captions to Microsoft to generate speech',
                               key='dub_online_consent')
        signature = dubbing_signature(source_hash, rows, voice)
        result = st.session_state.get('dubbed_result')
        if result and result['signature'] != signature:
            st.session_state.pop('dubbed_result', None)
            result = None
        if st.button(f'Create {target} dubbed video', type='primary', disabled=not consent,
                     key='create_dubbed_video'):
            with st.status('Creating translated audio...', expanded=True) as status:
                try:
                    result = create_dubbed_video(uploaded.getvalue(), Path(uploaded.name).suffix,
                                                  rows, voice, consent=consent, progress=st.write)
                    result['signature'] = signature
                    st.session_state.dubbed_result = result
                    status.update(label='Dubbed video ready', state='complete', expanded=False)
                except DubbingError as exc:
                    status.update(label='Dubbing failed', state='error')
                    st.error(str(exc))
                except Exception:
                    status.update(label='Dubbing failed', state='error')
                    st.error('The dubbed video could not be created. Check the clip and try again. Your captions are preserved.')
        if result:
            st.video(result['video'], subtitles={target: subtitles(rows, 'vtt')})
            left, right = st.columns(2)
            left.download_button('Download dubbed MP4', result['video'], file_name=f'dubbed-{target.lower()}.mp4',
                                  mime='video/mp4', key='download_dubbed_video')
            right.download_button('Download translated audio', result['audio'], file_name=f'dubbed-{target.lower()}.wav',
                                   mime='audio/wav', key='download_dubbed_audio')
            st.caption(f"{result['segments']} speech segments; {result['sped_up_segments']} adjusted to fit. Timing is approximate. This version does not synchronize lip movements or preserve the original speaker’s voice.")
