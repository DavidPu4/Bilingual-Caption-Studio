"""Optional account and private cloud-video controls for the Streamlit app."""
from dataclasses import dataclass

import streamlit as st

from firebase_client import FirebaseClient, FirebaseError, VIDEO_TYPES, clear_cloud_session


@dataclass
class CloudRecording:
    name: str
    content: bytes

    @property
    def size(self):
        return len(self.content)

    def getvalue(self):
        return self.content


def render_account():
    client = FirebaseClient()
    with st.sidebar:
        with st.expander('Account & cloud videos', expanded=False):
            user = st.session_state.get('cloud_user')
            if not user:
                st.caption('Sign in to save videos privately. Local captioning works without an account.')
                mode = st.radio('Account action', ['Log in', 'Create account'], key='cloud_auth_mode', horizontal=True)
                with st.form('account_form', clear_on_submit=True):
                    email = st.text_input('Email', key='cloud_email')
                    password = st.text_input('Password', type='password', key='cloud_password')
                    confirmation = st.text_input('Confirm password', type='password', key='cloud_confirmation') if mode == 'Create account' else None
                    submitted = st.form_submit_button(mode)
                if submitted:
                    try:
                        if mode == 'Create account' and password != confirmation:
                            raise FirebaseError('The passwords do not match.')
                        user = client.authenticate(email, password, signup=mode == 'Create account')
                        clear_cloud_session(st.session_state)
                        st.session_state.cloud_user = user
                        st.rerun()
                    except FirebaseError as exc:
                        st.error(str(exc))
            else:
                st.write(f"Signed in as {user['email']}")
                if st.button('Log out', key='cloud_logout'):
                    clear_cloud_session(st.session_state)
                    st.rerun()
                storage_ready = client.config.get('storageEnabled') is True
                if not storage_ready:
                    st.info('Cloud video storage is awaiting setup by the project owner.')
                if st.button('Refresh my videos', key='cloud_refresh', disabled=not storage_ready):
                    try:
                        items, more = client.list_videos(user)
                        st.session_state.cloud_items = items
                        st.session_state.cloud_more = more
                    except FirebaseError as exc:
                        st.error(str(exc))
                items = st.session_state.get('cloud_items', [])
                if items:
                    path = st.selectbox('My saved videos', [item['name'] for item in items],
                                         format_func=lambda value: value.rsplit('/', 1)[-1][65:], key='cloud_selected')
                    if st.button('Open selected video', key='cloud_open'):
                        try:
                            with st.spinner('Loading your private video...'):
                                content = client.download_video(user, path)
                            st.session_state.cloud_recording = {'uid': user['uid'], 'name': path.rsplit('/', 1)[-1][65:], 'content': content}
                            st.rerun()
                        except FirebaseError as exc:
                            st.error(str(exc))
                else:
                    st.caption('Click Refresh my videos to load your library. Save an uploaded video below to add one.')
                if st.session_state.get('cloud_more'):
                    st.caption('Showing the first 100 saved videos.')
            st.caption('Videos upload only when you choose Save. Your sign-in lasts for this browser session.')
    return client, st.session_state.get('cloud_user')


def render_cloud_save(client, user, uploaded):
    from pathlib import Path
    if not user or uploaded is None or Path(uploaded.name).suffix.lower() not in VIDEO_TYPES:
        return
    if st.button('Save video to my cloud library', key='cloud_save_video',
                 disabled=client.config.get('storageEnabled') is not True):
        try:
            with st.spinner('Saving to your private cloud library...'):
                info = client.upload_video(user, uploaded.name, uploaded.getvalue())
            existing = st.session_state.get('cloud_items', [])
            st.session_state.cloud_items = [info] + [item for item in existing if item['name'] != info['name']]
            st.success('Video saved to your cloud library. Captions are saved separately with Save editable project.')
        except FirebaseError as exc:
            st.error(str(exc))
