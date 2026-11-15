# Plugin Submission

## Plugin information

- **Author**: liaoliaochenfeng2
- **Plugin name**: local_retouch
- **Version**: 0.1.1
- **Source repository**: https://github.com/liaoliaochenfeng2/dify-local-retouch
- **Contact**: 2748836629@qq.com

## Submission type

- [x] New plugin
- [ ] Version update

## What changed

Local Retouch Studio executes bounded JSON photo-editing recipes in the Dify
plugin host using Pillow, NumPy and OpenCV. The two tools prepare explicit
source/reference/mask/background assets and render final images or A/B previews.
Supported operations include color and tonal adjustments, curves, HSL, eight
base styles, limited reference color matching, light smoothing, small-area
classical inpainting, background compositing and bounded local liquify.

Conversation assets and full recipes use bounded Dify SDK storage. Explicitly
saved preferences have separate retention. No external image-generation API,
model-weight download, GPU or separate image server is required. An application's
LLM and vision model are configured independently; this plugin does not call them.

## Risk level

- [ ] Low risk
- [ ] Medium risk
- [x] High risk

## Required checks

- [x] I have read and followed the [Marketplace submission requirements](https://github.com/langgenius/dify-plugins/blob/main/docs/plugin-submission-requirements.md).
- [ ] I have read and comply with the [Plugin Development Guidelines](https://docs.dify.ai/en/develop-plugin/publishing/standards/contributor-covenant-code-of-conduct).
- [x] I tested this plugin on Dify Community Edition and Dify Cloud, or documented any limitation below.
- [x] The package contains only files needed at runtime.
- [x] The package does not contain secrets, local credentials, `.env` files, `.git` directories, virtual environments, caches, logs, or IDE files.
- [x] The package does not contain executables or bundled binaries, or I explained why they are required below.
- [x] The plugin README includes setup steps, usage instructions, required APIs or credentials, connection requirements, and the source repository link.
- [x] The plugin includes `PRIVACY.md` or a hosted privacy policy, and `manifest.yaml` references it.
- [x] All user-facing text is primarily in English, with any localized README files following the [i18n guidance](https://docs.dify.ai/en/develop-plugin/features-and-specs/plugin-types/multilingual-readme).

## Security and privacy notes

This plugin processes user-uploaded photographs, potentially including portraits
and sensitive metadata. Cached and returned original bytes can retain EXIF/GPS.
It does not identify people or produce biometric identity templates. High risk
is selected conservatively because user photographs and retained metadata may
contain sensitive personal information.

The plugin reads a fixed bundled style guide and uses Dify SDK file/storage
interfaces. It accepts uploaded file inputs, not standalone user-controlled
network endpoints, filesystem paths or executable programs. It has no command
execution, caller-supplied Python execution, SQL, SSH/SFTP, browser automation or
proxy endpoint. Dependency installation can contact the host-configured package
registry; SDK transport can communicate with the host's file/storage services. SDK file retrieval may issue HTTP requests before this plugin can apply the byte-size check. Bind only trusted Dify-managed uploads; the host must validate file URLs and enforce upload-size and egress controls.

Inputs are restricted to static JPEG/PNG/WebP, 20 MiB per file, 6000 pixels per
side, and 12.6 million pixels total. Recipes have up to 10 operations with fixed
schemas and bounded parameters. Small-area inpainting is capped at 8% per step
and 12% accumulated. The host must impose wall-clock timeouts and concurrency
limits; there is no independent hard runtime deadline inside each operation.

Conversation cache: 64 slots, 12 MiB per record, 24-hour logical TTL. Preference
cache: 4096 slots, 4 KiB per record, 90-day logical TTL. Expiry is checked on read,
not an immediate physical-deletion promise. Explicit reset actions are provided.
Platform files, logs, conversations and backups have separate host retention.

Bind user and conversation inputs directly to trusted Dify system variables.
Available SDK identities are checked; preference persistence is disabled without
trusted application identity. No service credential is requested. Error/status
outputs avoid echoing raw source bytes; user plan labels and reports remain
user content and may be logged by the hosting platform.

## Local validation

- 67 core tests passed with Python 3.12, dify-plugin 0.10.2, Pillow 13.0.0, NumPy 2.3.5 and OpenCV Headless 4.14.0.94.
- Real SDK source and unpacked-package tests passed, including 11 negative cases, original-to-A/B-to-final replay, and zero attempted network connections in the SDK smoke test.
- Official Dify CLI 0.6.3 packaged and decoded this archive successfully.
- Marketplace Toolkit `83ee707c1dde758678f94a311f15b42d74f8c402` returned exit code 0, with zero blocking findings. Online OSV lookup found no known vulnerabilities in the four pinned direct dependencies at check time.
- Official `test-plugin-install.py` passed in serverless mode, including plugin startup and localhost HTTP response. This is a startup check, not full Dify-host installation.

```bash
python tests/run_tests.py --source plugin --report validation/core_tests.json
python tests/test_plugin_package.py --source plugin
/path/to/dify plugin package plugin -o local_retouch-0.1.1.difypkg
python tests/test_plugin_package.py --package local_retouch-0.1.1.difypkg
python /path/to/dify-marketplace-toolkit/validator/validate-difypkg.py local_retouch-0.1.1.difypkg --pr-body-file SUBMISSION_DRAFT.md --output-dir validation/marketplace-final
INSTALL_METHOD=serverless SERVERLESS_HOST=127.0.0.1 python /path/to/dify-marketplace-toolkit/validator/test-plugin-install.py --directory plugin
```

- Archive SHA-256: `b00cf551cc5dcc39697acb3937c5277f697f13bcda11ae896da8c63da7581809`
- Official plugin checksum: `484124ce707a458eab9e2286aab29f456c5c14e833d1fb77429bf53b8eb0d16a`
- Source and detailed record: https://github.com/liaoliaochenfeng2/dify-local-retouch/blob/main/VALIDATION.md

## Reviewer notes

The package is submitted for review with the following limitations explicitly disclosed.

The Plugin Development Guidelines checkbox remains unchecked: remote-debugging/end-to-end verification in a live Dify host has not been completed, so production readiness is not claimed. The testing checkbox is checked under its explicit "or documented any limitation below" alternative.

OpenCV is intentionally included for bilateral filtering, classical inpainting, GrabCut, image remapping and color operations. The toolkit flags it as a large dependency requiring reviewer approval. No model weights or native executable files are bundled in this archive; its wheel is installed through the host dependency manager.

Local SDK tests substitute only the platform file/storage transport and deny
network access. They do not establish successful installation in a live Dify
Community or Cloud instance. Live installation and end-to-end verification are
still pending and must not be represented as complete.

The plugin is conventional image processing, not a generative image model.
Large object reconstruction, clothing/pose replacement and text-generated
backgrounds are unsupported. Automatic skin protection uses a color heuristic.
English editing plans are supported, but explicit long-term preference opt-in
currently recognizes Chinese cue wording only, as disclosed in the README.
