"""Offline REST contract tests. Live Firebase setup and rule enforcement are separate."""
import time
import hashlib
import unittest
from unittest.mock import Mock, patch

from firebase_client import FirebaseClient, FirebaseError, clear_cloud_session


class FirebaseTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.client = FirebaseClient({'apiKey': 'public-test-api-key', 'storageBucket': 'test-bucket', 'storageEnabled': True}, self.http)
        self.user = {'uid': 'user-a', 'email': 'test@example.com', 'id_token': 'secret-user-token',
                     'refresh_token': 'secret-refresh-token', 'expires_at': time.time() + 3600}

    def response(self, data=None, status=200, chunks=None):
        response = Mock(status_code=status)
        response.json.return_value = data or {}
        response.iter_content.return_value = chunks or []
        self.http.request.return_value = response
        return response

    def test_login_does_not_store_password(self):
        self.response({'localId': 'user-a', 'email': 'test@example.com', 'idToken': 'id',
                       'refreshToken': 'refresh', 'expiresIn': '3600'})
        user = self.client.authenticate(' test@example.com ', 'password123')
        args, kwargs = self.http.request.call_args
        self.assertIn('signInWithPassword', args[1])
        self.assertEqual(kwargs['json']['email'], 'test@example.com')
        self.assertTrue(kwargs['json']['returnSecureToken'])
        self.assertNotIn('password', user)

    def test_storage_is_off_until_owner_finishes_console_setup(self):
        self.client.config['storageEnabled'] = False
        with self.assertRaisesRegex(FirebaseError, 'awaiting setup'):
            self.client.upload_video(self.user, 'clip.mp4', b'video')
        self.http.request.assert_not_called()

    def test_signup_validation_and_endpoint(self):
        with self.assertRaises(FirebaseError):
            self.client.authenticate('test@example.com', 'short', signup=True)
        self.http.request.assert_not_called()
        self.response({'localId': 'user-a', 'email': 'test@example.com', 'idToken': 'id',
                       'refreshToken': 'refresh', 'expiresIn': '3600'})
        self.client.authenticate('test@example.com', 'password123', signup=True)
        self.assertIn('accounts:signUp', self.http.request.call_args.args[1])

    def test_provider_not_enabled_has_actionable_error(self):
        self.response({'error': {'message': 'OPERATION_NOT_ALLOWED'}}, 400)
        with self.assertRaisesRegex(FirebaseError, 'Enable Email/Password'):
            self.client.authenticate('test@example.com', 'password123')

    def test_network_errors_do_not_echo_credentials(self):
        self.http.request.side_effect = OSError('sensitive request password123 secret-user-token')
        with self.assertRaises(FirebaseError) as raised:
            self.client.authenticate('test@example.com', 'password123')
        self.assertNotIn('password123', str(raised.exception))
        self.assertNotIn('secret-user-token', str(raised.exception))

    def test_expired_tokens_refresh_before_storage(self):
        self.user['expires_at'] = 0
        refresh = Mock(status_code=200)
        refresh.json.return_value = {'user_id': 'user-a', 'id_token': 'new-token',
                                     'refresh_token': 'new-refresh', 'expires_in': '3600'}
        listing = Mock(status_code=200)
        listing.json.return_value = {'items': []}
        self.http.request.side_effect = [refresh, listing]
        self.client.list_videos(self.user)
        self.assertEqual(self.user['id_token'], 'new-token')
        self.assertEqual(self.http.request.call_args.kwargs['headers']['Authorization'], 'Firebase new-token')

    def test_refresh_cannot_change_user_identity(self):
        self.user['expires_at'] = 0
        self.response({'user_id': 'user-b', 'id_token': 'new-token', 'refresh_token': 'x', 'expires_in': '3600'})
        with self.assertRaises(FirebaseError):
            self.client.list_videos(self.user)
        self.assertEqual(self.http.request.call_count, 1)

    def test_list_only_requests_own_prefix_and_hides_download_tokens(self):
        self.response({'items': [{'name': 'users/user-a/videos/clip.mp4', 'downloadTokens': 'do-not-display',
                                  'size': '25', 'contentType': 'video/mp4'}], 'nextPageToken': 'next'})
        videos, more = self.client.list_videos(self.user)
        self.assertTrue(more)
        self.assertNotIn('downloadTokens', videos[0])
        self.assertEqual(self.http.request.call_args.kwargs['params']['prefix'], 'users/user-a/videos/')

    def test_other_users_video_paths_are_rejected(self):
        for path in ['users/user-b/videos/clip.mp4', 'users/user-a/videos/nested/clip.mp4', 'users/user-a/videos/']:
            with self.assertRaises(FirebaseError):
                self.client.download_video(self.user, path)
        self.http.request.assert_not_called()

    def test_download_uses_authenticated_request_without_public_link(self):
        response = self.response(chunks=[b'video', b'bytes'])
        result = self.client.download_video(self.user, 'users/user-a/videos/clip.mp4')
        self.assertEqual(result, b'videobytes')
        self.assertIn('users%2Fuser-a%2Fvideos%2Fclip.mp4', self.http.request.call_args.args[1])
        self.assertEqual(self.http.request.call_args.kwargs['params'], {'alt': 'media'})
        self.assertEqual(self.http.request.call_args.kwargs['headers']['Authorization'], 'Firebase secret-user-token')
        response.close.assert_called_once()

    def test_upload_private_metadata_size_and_type(self):
        self.response({'name': 'users/user-a/videos/clip.mp4', 'downloadTokens': 'hidden', 'size': '5'})
        result = self.client.upload_video(self.user, 'my clip.mp4', b'video')
        args, kwargs = self.http.request.call_args
        self.assertTrue(kwargs['params']['name'].startswith('users/user-a/videos/'))
        self.assertTrue(kwargs['params']['name'].endswith('-my_clip.mp4'))
        self.assertIn(b'private, no-store', kwargs['data'])
        self.assertEqual(kwargs['headers']['X-Goog-Upload-Protocol'], 'multipart')
        self.assertNotIn('downloadTokens', result)
        self.http.request.reset_mock()
        for filename, content in [('clip.exe', b'video'), ('clip.mp4', b'')]:
            with self.assertRaises(FirebaseError):
                self.client.upload_video(self.user, filename, content)
        with patch('firebase_client.MAX_VIDEO_BYTES', 4), self.assertRaises(FirebaseError):
            self.client.upload_video(self.user, 'clip.mp4', b'12345')
        self.http.request.assert_not_called()

    def test_download_size_limit_closes_response(self):
        response = self.response(chunks=[b'123', b'456'])
        with patch('firebase_client.MAX_VIDEO_BYTES', 4), self.assertRaises(FirebaseError):
            self.client.download_video(self.user, 'users/user-a/videos/clip.mp4')
        response.close.assert_called_once()

    def test_logout_clears_cloud_data_and_cloud_derived_edits(self):
        state = {'cloud_user': self.user, 'cloud_recording': {'content': b'private'},
                 'cloud_items': ['private'], 'tracks': {},
                 'metadata': {'source_sha256': hashlib.sha256(b'private').hexdigest()}, 'revision': 1,
                 'editor_Mandarin_1': {}, 'preview_language': 'Mandarin'}
        clear_cloud_session(state)
        self.assertEqual(state, {'preview_language': 'Mandarin'})
        local_state = {'cloud_user': self.user, 'tracks': {'Mandarin': []}}
        clear_cloud_session(local_state)
        self.assertIn('tracks', local_state)
        local_with_cloud_selection = {'cloud_recording': {'content': b'private'},
                                     'metadata': {'source_sha256': 'different-local-recording'}, 'tracks': {}}
        clear_cloud_session(local_with_cloud_selection)
        self.assertIn('tracks', local_with_cloud_selection)


if __name__ == '__main__':
    unittest.main()
