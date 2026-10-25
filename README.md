# Bilingual Caption Studio

A Mandarin ↔ English caption editor and video dubbing prototype built with Python, Streamlit, faster-whisper, and MarianMT. Choose the recording language, generate and edit captions, export subtitles, or create a new video with translated synthetic speech. Captioning runs locally; optional dubbing uses an online voice service with explicit opt-in.

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

The first inference downloads pretrained models from Hugging Face. No paid API or API key is needed. Speech recognition uses CPU/int8 and defaults to `small`, which translated the Mandarin synthetic smoke-test clip best. Tiny is faster but made meaning errors on that clip. English-to-Mandarin additionally downloads `Helsinki-NLP/opus-mt-en-zh`, a local CPU text translation model. Model loading and runtime depend on your internet connection and computer. The app does not train models from scratch. Audio stays in the local process and a temporary local file that is deleted after processing. Models are cached locally; temporary-file deletion is not secure disk erasure. The local server binds to 127.0.0.1.

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

## Translate the video's audio (dubbing)

1. Upload an MP4/WebM video, select its recording language, and generate captions.
2. Review and correct the translated caption track: Mandarin for English recordings, English for Mandarin recordings. Dubbing uses the saved project's source language even if the sidebar selection has since changed.
3. Open **Translate the video audio to Mandarin/English** below the caption editor. Choose a standard voice.
4. Read the online synthesis disclosure and check the box allowing the translated caption text to be sent to Microsoft. Click **Create Mandarin/English dubbed video**.
5. Play the result, then download the dubbed MP4 or the translated WAV audio.

No account, API key, or billing setup is needed for this prototype's `edge-tts` voice service. It requires internet and may be unavailable or rate-limited; it is not a service with a production availability guarantee. Only the translated caption text is sent for speech generation after opt-in and an explicit Create action. Original video/audio are not uploaded by the dubbing code. Caption generation and editing can still be used without online voice generation. See the [edge-tts project](https://github.com/rany2/edge-tts).

**Audio behavior:** the output replaces every original audio track with one translated AAC track. Original dialogue, music, and background sound are removed together. This is standard synthetic narration, not voice cloning, source separation, or lip synchronization. Mandarin voices are Xiaoxiao/Yunxi; English voices are Jenny/Guy. Translation mistakes remain audible unless you correct the target captions before dubbing.

Speech starts at each caption's timestamp. Shorter speech leaves silence; longer speech can use the silent gap before the next caption and is sped up with pitch-preserving FFmpeg `atempo` if necessary. It never overlaps the next line. More than 2.5x required speed, captions past the end of the video, or residual overflow fails with an editable caption-row error instead of silently cutting off speech. Current limits are five minutes, 50 MB input, 200 captions, and 30,000 caption characters. Timing is approximate, especially around long translations and pauses.

`dubbing.py` handles explicit synthesis consent, three concurrent speech requests with one retry per failed segment, a mono 24 kHz audio timeline, and local MP4 export. H.264 video is stream-copied; other codecs are transcoded to H.264/yuv420p. FFmpeg is supplied by the `imageio-ffmpeg` wheel. `dubbing_ui.py` handles voice selection, disclosure, preview, and downloads. Outputs remain in session memory; temporary work files are removed. Source-video hash, target caption text/timing, and voice fingerprint invalidate stale outputs when edited.

## Architecture and decisions

`app.py` handles uploads, model caching, editing, project restoration, and downloads. `engine.py` decodes media to 16 kHz and supports two paths:

- **Mandarin → English:** two Whisper passes: `task=transcribe, language=zh` for Mandarin and `task=translate, language=zh` for English. The English result comes directly from speech translation. Each track retains its independently generated timing; the app does not claim sentence-level alignment for this path.
- **English → Mandarin:** one Whisper transcription pass with `language=en`, then `Helsinki-NLP/opus-mt-en-zh` translates English caption text in batches. Mandarin captions inherit the source caption start/end times. This text model produces written Chinese; no translated voice is generated. The translator is loaded lazily and cached separately. Overlong inputs fail explicitly rather than silently truncating text.

Editing one language does not regenerate the other. Saved projects include the source language and translation model; older projects without source-language metadata continue to default to Mandarin. Switching the recording language preserves existing captions until you generate again. Evaluation follows the saved project's source language so translated Mandarin is not mislabeled as a transcription of English audio.

`captions.py` validates monotonic non-overlapping captions, formats subtitles, serializes projects, and computes character error rate using Levenshtein distance. It rejects empty text, nonfinite times, invalid ranges, and overlapping cues. UI edits stay in the editor during the session; save JSON to persist them. Generating, restoring, or loading a sample replaces the editor.

`playback.py` prepares native video subtitle tracks from the latest validated edits and verifies the recording identity before attaching them. The video placeholder is updated after editor validation, so the preview uses the corrected caption text and timings.

For fast delivery this uses Streamlit and synchronous inference. Authentication, queues, automatic retries, collaborative editing, and cloud deployment are out of scope. The UI has no progress percentage because the model API does not provide reliable overall progress. A cached model is shared within the process; deploy a worker queue before supporting concurrent inference requests.

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

Dubbing tests cover opt-in gating, source/caption/voice fingerprinting, timeline placement and silence, tempo speed limits, rejection of overlong speech, and stale-result invalidation. Media integration tests verify a newly exported video has exactly one replacement AAC audio track, with no original test tone retained, and verify WebM-to-H.264 conversion. CI skips media/UI tests when their optional runtime packages are absent. The ordinary test suite does not contact the online speech service.

Separate manual smoke checks on October 25, 2026 synthesized both Mandarin and English, created downloadable MP4/WAV files from a synthetic nine-second video, and transcribed the produced speech back with Whisper small. Both recovered the expected text after punctuation/whitespace normalization. This is a short synthetic check, not a dubbing-quality or timing benchmark on real videos. Ignored `local-results/` files contain the local test outputs.

## Resume wording after you test and understand it

> Built a local bidirectional Mandarin-English captioning application using Python, Streamlit, Whisper, and MarianMT, with timestamp editing, SRT/WebVTT export, and saveable projects.

After real evaluation, add the actual number of clips, model comparison, CER, and hardware. Do not claim model training, production scale, users, or accuracy improvements unless you did and measured those things. If using AI assistance, review the implementation and be ready to explain validation, the two inference passes, and CER.

## References

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [English-to-Chinese MarianMT model](https://huggingface.co/Helsinki-NLP/opus-mt-en-zh)
- [Streamlit data editor](https://docs.streamlit.io/develop/api-reference/data/st.data_editor)
- [Streamlit file uploader](https://docs.streamlit.io/develop/api-reference/widgets/st.file_uploader)
- [Streamlit video subtitles](https://docs.streamlit.io/develop/api-reference/media/st.video)
- [edge-tts speech synthesis](https://github.com/rany2/edge-tts)
- [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg)
- [FFmpeg audio tempo filter](https://ffmpeg.org/ffmpeg-filters.html#atempo)
