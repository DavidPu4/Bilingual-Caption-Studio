# Bilingual Caption Studio

A local Mandarin ↔ English caption editor built with Python, Streamlit, faster-whisper, and MarianMT. Choose the recording language, upload a short audio/video file, generate captions in both languages, correct timestamped captions, and export SRT or WebVTT files.

## Quick start

Install Python 3.12 and Git, then run these commands in PowerShell:

```powershell
git clone https://github.com/DavidPu4/Bilingual-Caption-Studio.git
cd Bilingual-Caption-Studio
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "torch==2.14.1" --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

After setup, Windows users can double-click **Start Studio.cmd** to launch the app. Keep the terminal open while using it; press Ctrl+C there to stop. If you already have the project and its environment installed, run only the final command from the project folder.

On macOS/Linux use `python3` for environment creation and `.venv/bin/python` in place of `.venv\Scripts\python.exe`. The separate PyTorch command selects CPU wheels for Windows/Linux; on macOS skip that command and let `requirements.txt` install PyTorch from PyPI.

The first inference downloads pretrained models from Hugging Face. No paid AI API or AI API key is needed. Speech recognition uses CPU/int8 and defaults to `small`, which translated the Mandarin synthetic smoke-test clip best. Tiny is faster but made meaning errors on that clip. English-to-Mandarin additionally downloads `Helsinki-NLP/opus-mt-en-zh`, a local CPU text translation model. Model loading and runtime depend on your internet connection and computer. The app does not train models from scratch. Caption inference stays local; media is decoded in the local process and a temporary file that is deleted after processing. Videos are uploaded to Firebase only when a signed-in user clicks **Save video to my cloud library**. Models are cached locally; temporary-file deletion is not secure disk erasure. The local server binds to 127.0.0.1.

## Firebase accounts and private video storage

The sidebar's **Account & cloud videos** panel supports email/password account creation, login, and logout using the supplied `bilingual-caption-studio` Firebase project. Local captioning remains available without an account. When Storage setup is complete, signed-in users can explicitly save MP4/WebM files (up to 50 MB), refresh their own library, and open saved videos for caption generation and playback. Captions/projects are still saved separately as JSON; cloud caption-history sync is not included.

**Current setup status:** application code and offline tests are implemented. The Firebase console required Google sign-in in this environment, so Email/Password activation, bucket/billing status, and deployed Storage Rules could not be checked or changed. `storageEnabled` is deliberately `false` until the project owner finishes the steps below. This prevents cloud writes before private rules are deployed. Live sign-up and upload have not yet been verified.

### One-time Firebase console setup

1. Open [Authentication for this project](https://console.firebase.google.com/project/bilingual-caption-studio/authentication/providers), initialize Authentication if necessary, and enable **Email/Password** as a sign-in provider. The app currently uses email/password, not Google sign-in.
2. Open [Storage for this project](https://console.firebase.google.com/project/bilingual-caption-studio/storage). Firebase Storage requires the **Blaze** billing plan; the owner must approve/link billing and initialize the bucket if it does not exist. Confirm its name is `bilingual-caption-studio.firebasestorage.app`. No billing changes were made by this project setup.
3. In **Storage → Rules**, replace the default rules with the full contents of `storage.rules` and publish them. These rules permit authenticated users to read/list only their own `users/{uid}/videos/` prefix and create/update nonempty MP4/WebM objects up to 50 MB. Other users, anonymous access, other paths, and deletes are denied. Avoid test-mode public rules.
4. Change `"storageEnabled": false` to `"storageEnabled": true` in `firebase_web_config.json` **after** the private rules are published. Rerun the app. Never enable this flag against a bucket still using public/test-mode rules.

If using the Firebase CLI instead of pasting rules into the console, run from this repository after initializing Storage:

```powershell
npx --package firebase-tools firebase login
npx --package firebase-tools firebase deploy --only storage --project bilingual-caption-studio
```

The included `.firebaserc` and `firebase.json` select the project and `storage.rules`. The web config identifies this Firebase project and is public client configuration, not an admin/service-account credential. There is no need to provide a service-account private key. [Firebase API key guidance](https://firebase.google.com/docs/projects/api-keys), [Storage billing requirements](https://firebase.google.com/docs/storage/faqs-storage-changes-announced-sept-2024).

### Use and validate the cloud library

1. Create an account in the sidebar, or log in to an existing account. The app requires at least eight characters for new passwords; Firebase's configured policy may be stricter.
2. Upload a short MP4 or WebM and click **Save video to my cloud library**. Merely selecting a file or generating captions does not upload it to Firebase.
3. Click **Refresh my videos**, select the saved file, and click **Open selected video**. Generate captions normally. **Use local upload instead** switches back to the local file input.
4. Log out and log in as a second test user. Confirm the first user's saved videos are absent. Also verify anonymous and cross-user reads/listing are rejected by the deployed Storage Rules before treating the cloud library as live. Offline client tests do not prove deployed rule enforcement.

The initial library shows at most 100 files. Re-saving the same bytes and sanitized filename reuses the same object path. There is no video-delete UI yet. Saving videos does not make inference cloud-hosted; AI models still run on the Python app's machine.

### Authentication and media handling

`firebase_client.py` calls Firebase Auth REST endpoints and the Storage endpoint using the signed-in user's ID token. Expiring ID tokens are refreshed; all Storage operations run under end-user rules, with no admin SDK bypass. Firebase manages password storage. The Streamlit server receives submitted form values transiently and forwards credentials to Firebase over HTTPS; passwords are not written to disk or global caches. Tokens and loaded cloud videos stay in that browser session's Streamlit state. Logout clears cloud tokens, library entries, loaded cloud media, and captions derived from loaded cloud media. Local-only work is retained. Sessions are not persisted across server restarts.

Cloud video downloads use authenticated requests and do not expose Firebase permanent download-token URLs. The local Streamlit player still uses its normal media-serving mechanism; this prototype binds to localhost. A public deployment needs HTTPS and a review of authenticated media serving/session isolation rather than assuming every Streamlit media URL is protected by Firebase login. App Check enforcement, email verification/recovery flows, quotas, and production abuse controls are not implemented here.

## Two-minute interface demo

1. Open **Sample captions** in the sidebar. These are hand-written examples with illustrative timestamps, clearly marked as sample data.
2. Change text or timing in either table.
3. Download SRT/WebVTT and save the project as JSON.
4. Restore the JSON to continue later. Audio is not embedded in the project.

## Real recording demo

1. Select **Mandarin → English** or **English → Mandarin** under **Recording language** in the sidebar, then record a short passage in that language.
2. Upload it; use the default `small` model and press **Generate captions**.
3. For video, leave **Captions on video** set to **Mandarin** to see timed Mandarin subtitles over the picture. Choose **English** or **Off** to change the preview. The native player also supports fullscreen and caption controls. Audio-only uploads retain the audio player and caption tables.
4. Before editing, paste a manually verified reference in the recording language into the transcription evaluation panel and calculate CER.
5. Correct errors, export captions, and save the project.

Inputs: WAV, MP3, M4A, FLAC, OGG, MP4, WebM; maximum 50 MB and five minutes. Video must contain an audio track. PyAV handles decoding; a standalone FFmpeg executable is not required. Duration is checked after decoding, so avoid files with misleadingly small compressed sizes and very long durations. This is a local, single-user prototype, not a hardened public upload service.

## Captions over video

MP4 and WebM uploads use the native Streamlit video player with generated WebVTT tracks. Mandarin is on by default; the **Captions on video** control switches to English or hides captions. Captions appear and disappear at their start/end timestamps. Correcting text or timing in the editor updates the preview on rerun; playback may restart when the player updates. There is no automatic row-to-playback seeking.

The preview uses validated current edits and only attaches them when the uploaded video's SHA-256 matches the saved project's source identity. This prevents sample captions or another recording's captions appearing on the wrong video. Older saved projects without a source hash need captions generated again for video preview. SRT/WebVTT export continues to work for them.

These are selectable playback captions, including in fullscreen. The original video file is unchanged; video downloads from the browser do not permanently embed this text. Burned-in MP4 export is not included.

## Architecture and decisions

`app.py` handles uploads, model caching, editing, project restoration, and downloads. `engine.py` decodes media to 16 kHz and supports two paths:

- **Mandarin → English:** two Whisper passes: `task=transcribe, language=zh` for Mandarin and `task=translate, language=zh` for English. The English result comes directly from speech translation. Each track retains its independently generated timing; the app does not claim sentence-level alignment for this path.
- **English → Mandarin:** one Whisper transcription pass with `language=en`, then `Helsinki-NLP/opus-mt-en-zh` translates English caption text in batches. Mandarin captions inherit the source caption start/end times. This text model produces written Chinese; no translated voice is generated. The translator is loaded lazily and cached separately. Overlong inputs fail explicitly rather than silently truncating text.

Editing one language does not regenerate the other. Saved projects include the source language and translation model; older projects without source-language metadata continue to default to Mandarin. Switching the recording language preserves existing captions until you generate again. Evaluation follows the saved project's source language so translated Mandarin is not mislabeled as a transcription of English audio.

`captions.py` validates monotonic non-overlapping captions, formats subtitles, serializes projects, and computes character error rate using Levenshtein distance. It rejects empty text, nonfinite times, invalid ranges, and overlapping cues. UI edits stay in the editor during the session; save JSON to persist them. Generating, restoring, or loading a sample replaces the editor.

`playback.py` prepares native video subtitle tracks from the latest validated edits and verifies the recording identity before attaching them. The video placeholder is updated after editor validation, so the preview uses the corrected caption text and timings.

For fast delivery this uses Streamlit and synchronous inference. Queues, automatic retries, collaborative editing, and cloud hosting of the app are out of scope. The UI has no progress percentage because the model API does not provide reliable overall progress. A cached model is shared within the process; deploy a worker queue before supporting concurrent inference requests.

## Evaluation without invented metrics

Create 5-10 short Mandarin clips with manually checked references, including quiet speech, noise, proper nouns, and code-switching. Keep a held-out subset separate from any tuning. Measure tiny and base on the same clips and record:

| Clip | Duration | Model | Inference time | Mandarin CER | English review notes |
|---|---|---|---|---|---|
| Fill with actual observations | | | | | |

CER = character edit distance / reference length. Normalization uses NFKC, lowercase, and removal of whitespace and Unicode punctuation. It does not convert simplified/traditional Chinese. CER can exceed 100%. Score unedited captions to evaluate the model, and label corrected-caption scores separately. Inference time excludes decoding and model download/load. CER is not a translation metric: manually review English for meaning, omissions, and names. No accuracy claim is established by the sample project or automated unit tests.

## Tests

Local validation on October 24, 2026: all 11 automated checks passed. A separate end-to-end inference check used one 8.28-second Mandarin clip synthesized with Google's text-to-speech endpoint, from this known text: 大家好，这是一个中文测试。今天我们学习如何制作英文字幕。

| Model (CPU/int8) | Both inference passes | Normalized Mandarin CER | English output review on this clip |
|---|---:|---:|---|
| tiny | 1.836 s | 0% | Incorrectly translated test as society and subtitles as grammar |
| base | 3.317 s | 0% | Changed test to lesson and left 字幕 untranslated |
| small | 7.948 s | 0% | Preserved the passage's meaning in English |

Small output: "Hello everyone! This is a Chinese test. Today, we will learn how to make English subtitles."

English-input validation: all 16 automated checks pass after adding the reverse direction. A separate 6.384-second synthetic English recording ("Hello everyone. This is a test. Today we will learn how to make Chinese subtitles.") ran through Whisper small and MarianMT in 4.906 seconds, excluding loading and decoding. Source transcription had 0% normalized CER on this one clip. Mandarin output was: "大家好,这是个测试 今天我们要学习如何制作中文字幕". Both exported tracks retained matching timestamps. This verifies the new path on one clean synthetic example; it does not establish accuracy on real-world recordings.

This is a smoke test on a single clean synthetic clip, not a general accuracy benchmark. Timings exclude model loading, downloading, and decoding. Results are saved under ignored `local-results/smoke-*` directories on this computer. Test your own real recordings before making resume performance claims. The shipped sample JSON remains hand-written interface data, separate from these inference results. Dependencies are pinned, including PyAV 16.1.0 to avoid the [known PyAV 19 compatibility issue in faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper/issues/1589).

For repeatable inference without opening the UI:

```powershell
.\.venv\Scripts\python.exe cli.py recording.mp3 --model tiny --reference reference.txt --output local-results/run-1
.\.venv\Scripts\python.exe cli.py english.mp3 --source-language en --model small --reference english-reference.txt --output local-results/english-run
```

This writes both subtitle formats, a restorable project, and metrics JSON. The optional reference must be UTF-8 text in the source language. The real-time factor is total generation time divided by clip duration; it excludes model loading and decoding. For English recordings, generation time includes source transcription and text translation.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Core tests cover subtitle formatting, timestamp rollover, malformed projects, overlaps, Unicode, CER, both inference directions, translated timing preservation, silence, and failed translations. Streamlit AppTests check sample loading, direction selection, editor rendering, downloads, and source-language CER interaction. CI runs core tests without installing inference dependencies; UI tests are skipped in that minimal environment.

Video-preview checks cover Mandarin/English/off selection, Unicode WebVTT timings, current edits, invalid captions, and mismatched recording identities. Browser playback was also checked with an actual uploaded MP4: Mandarin subtitles appeared over the video, including in fullscreen.

Firebase tests use a fake HTTP transport and mocked account responses: sign-up/login, errors, token refresh and identity consistency, per-user paths, authenticated downloads, upload limits, metadata privacy, storage activation gating, and logout cleanup. They make no real accounts, send no verification emails, upload no files, and do not exercise the Firebase Rules engine. UI tests verify the login/logout flow without live credentials. Live Firebase verification remains pending the console setup above.

## Resume wording after you test and understand it

> Built a local bidirectional Mandarin-English captioning application using Python, Streamlit, Whisper, and MarianMT, with timestamp editing, SRT/WebVTT export, and saveable projects.

After real evaluation, add the actual number of clips, model comparison, CER, and hardware. Do not claim model training, production scale, users, or accuracy improvements unless you did and measured those things. If using AI assistance, review the implementation and be ready to explain validation, the two inference passes, and CER.

## References

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [English-to-Chinese MarianMT model](https://huggingface.co/Helsinki-NLP/opus-mt-en-zh)
- [Streamlit data editor](https://docs.streamlit.io/develop/api-reference/data/st.data_editor)
- [Streamlit file uploader](https://docs.streamlit.io/develop/api-reference/widgets/st.file_uploader)
- [Streamlit video subtitles](https://docs.streamlit.io/develop/api-reference/media/st.video)
