"""Firebase end-user REST client. No admin credentials or bypass of Storage Rules."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import quote
import uuid

MAX_VIDEO_BYTES = 50 * 1024 * 1024
VIDEO_TYPES = {'.mp4': 'video/mp4', '.webm': 'video/webm'}


class FirebaseError(Exception):
    """Safe user-facing failure; never include request bodies or tokens."""


def load_config():
    return json.loads((Path(__file__).parent / 'firebase_web_config.json').read_text(encoding='utf-8'))


def clear_cloud_session(state):
    recording = state.get('cloud_recording')
    cloud_project_active = (recording and state.get('metadata', {}).get('source_sha256')
                            == hashlib.sha256(recording['content']).hexdigest())
    if cloud_project_active:
        for key in list(state):
            if key in {'tracks', 'metadata', 'revision'} or key.startswith('editor_'):
                del state[key]
    for key in list(state):
        if key.startswith('cloud_'):
            del state[key]


class FirebaseClient:
    def __init__(self, config=None, transport=None):
        if transport is None:
            import requests
            transport = requests
        self.http = transport
        self.config = config or load_config()
        self.storage_url = f"https://firebasestorage.googleapis.com/v0/b/{quote(self.config['storageBucket'], safe='')}/o"

    def _request(self, method, url, **kwargs):
        try:
            response = self.http.request(method, url, timeout=(10, 90), **kwargs)
        except Exception as exc:
            # Do not echo requests exception URLs or submitted authentication values.
            raise FirebaseError('Firebase could not be reached. Check your connection and try again.') from None
        if not 200 <= response.status_code < 300:
            try:
                error = response.json().get('error', {})
                code = error.get('message', '') if isinstance(error, dict) else str(error)
            except Exception:
                code = ''
            response.close()
            messages = {
                'OPERATION_NOT_ALLOWED': 'Enable Email/Password in Firebase Console → Authentication → Sign-in method.',
                'CONFIGURATION_NOT_FOUND': 'Initialize Firebase Authentication and enable Email/Password in the Firebase console.',
                'EMAIL_EXISTS': 'An account already uses that email. Choose Log in.',
                'INVALID_LOGIN_CREDENTIALS': 'The email or password is incorrect.',
                'INVALID_PASSWORD': 'The email or password is incorrect.',
                'EMAIL_NOT_FOUND': 'The email or password is incorrect.',
                'INVALID_EMAIL': 'Enter a valid email address.',
                'USER_DISABLED': 'This account is disabled. Contact the project owner.',
                'TOO_MANY_ATTEMPTS_TRY_LATER': 'Too many attempts. Wait a little before trying again.',
                'TOKEN_EXPIRED': 'Your session expired. Log out and sign in again.',
                'INVALID_REFRESH_TOKEN': 'Your session expired. Log out and sign in again.',
            }
            code = code.split(' : ')[0]
            if code.startswith('WEAK_PASSWORD'):
                raise FirebaseError('The password does not meet the Firebase project password policy.')
            if code in messages:
                raise FirebaseError(messages[code])
            if response.status_code in {401, 403}:
                raise FirebaseError('Firebase denied access. Check your login, API restrictions, App Check settings, and the private Storage Rules.')
            if response.status_code == 404:
                raise FirebaseError('The bucket or file is unavailable. Initialize Storage and check that the project is on the Blaze plan.')
            raise FirebaseError(f'Firebase request failed (HTTP {response.status_code}). Check the Firebase console setup.')
        return response

    def authenticate(self, email, password, *, signup=False):
        if not email.strip() or not password:
            raise FirebaseError('Enter your email and password.')
        if signup and len(password) < 8:
            raise FirebaseError('Choose a password with at least 8 characters.')
        action = 'signUp' if signup else 'signInWithPassword'
        response = self._request('POST', f'https://identitytoolkit.googleapis.com/v1/accounts:{action}',
                                 params={'key': self.config['apiKey']},
                                 json={'email': email.strip(), 'password': password, 'returnSecureToken': True})
        data = response.json()
        response.close()
        return {'uid': data['localId'], 'email': data['email'], 'id_token': data['idToken'],
                'refresh_token': data['refreshToken'], 'expires_at': time.time() + int(data['expiresIn'])}

    def _headers(self, user):
        if self.config.get('storageEnabled') is not True:
            raise FirebaseError('Cloud video storage is awaiting setup. Deploy private Storage Rules before enabling it.')
        if not user or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', user.get('uid', '')):
            raise FirebaseError('Log in before using cloud storage.')
        if user.get('expires_at', 0) <= time.time() + 60:
            response = self._request('POST', 'https://securetoken.googleapis.com/v1/token',
                                     params={'key': self.config['apiKey']},
                                     data={'grant_type': 'refresh_token', 'refresh_token': user.get('refresh_token', '')})
            data = response.json()
            response.close()
            if data['user_id'] != user['uid']:
                raise FirebaseError('Your session changed. Log out and sign in again.')
            user.update(id_token=data['id_token'], refresh_token=data['refresh_token'],
                        expires_at=time.time() + int(data['expires_in']))
        return {'Authorization': f"Firebase {user['id_token']}"}

    def _own_path(self, user, path):
        prefix = f"users/{user['uid']}/videos/"
        if not path.startswith(prefix) or '/' in path[len(prefix):] or not path[len(prefix):]:
            raise FirebaseError('This video does not belong to the signed-in account.')
        return path

    @staticmethod
    def _video_info(item):
        # Never return Firebase's permanent download tokens or token-bearing links to the UI.
        return {key: item[key] for key in ('name', 'size', 'contentType', 'timeCreated') if key in item}

    def upload_video(self, user, filename, content):
        headers = self._headers(user)
        suffix = Path(filename).suffix.lower()
        if suffix not in VIDEO_TYPES:
            raise FirebaseError('Cloud video storage supports MP4 and WebM files.')
        if not content or len(content) > MAX_VIDEO_BYTES:
            raise FirebaseError('Upload a nonempty video of 50 MB or less.')
        safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', Path(filename).stem)[:80] + suffix
        path = f"users/{user['uid']}/videos/{hashlib.sha256(content).hexdigest()}-{safe_name}"
        boundary = 'caption-studio-' + uuid.uuid4().hex
        metadata = {'name': path, 'contentType': VIDEO_TYPES[suffix], 'cacheControl': 'private, no-store'}
        body = (f'--{boundary}\r\nContent-Type: application/json; charset=utf-8\r\n\r\n'.encode()
                + json.dumps(metadata).encode() + f'\r\n--{boundary}\r\nContent-Type: {VIDEO_TYPES[suffix]}\r\n\r\n'.encode()
                + content + f'\r\n--{boundary}--'.encode())
        headers.update({'Content-Type': f'multipart/related; boundary={boundary}', 'X-Goog-Upload-Protocol': 'multipart'})
        response = self._request('POST', self.storage_url, params={'name': path}, headers=headers, data=body)
        info = self._video_info(response.json())
        response.close()
        return info

    def list_videos(self, user):
        headers = self._headers(user)
        response = self._request('GET', self.storage_url, headers=headers,
                                 params={'prefix': f"users/{user['uid']}/videos/", 'maxResults': 100})
        data = response.json()
        response.close()
        items = [self._video_info(item) for item in data.get('items', [])
                 if self._own_path(user, item['name'])]
        return items, bool(data.get('nextPageToken'))

    def download_video(self, user, path):
        headers = self._headers(user)
        self._own_path(user, path)
        response = self._request('GET', self.storage_url + '/' + quote(path, safe=''),
                                 params={'alt': 'media'}, headers=headers, stream=True)
        chunks, total = [], 0
        try:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    raise FirebaseError('This saved video exceeds the 50 MB limit.')
                chunks.append(chunk)
        except FirebaseError:
            raise
        except Exception:
            raise FirebaseError('The video download was interrupted. Try opening it again.') from None
        finally:
            response.close()
        content = b''.join(chunks)
        if not content:
            raise FirebaseError('This saved video is empty.')
        return content
