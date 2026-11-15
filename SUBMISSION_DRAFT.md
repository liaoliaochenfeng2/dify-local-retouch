# Plugin Submission

## Plugin information

- **Author**: liaoliaochenfeng2
- **Plugin name**: local_retouch
- **Version**: 0.1.1
- **Source repository**: https://github.com/liaoliaochenfeng2/dify-local-retouch
- **Contact**: REPLACE_WITH_CONTACT_EMAIL

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

- [ ] I have read and followed the [Marketplace submission requirements](https://github.com/langgenius/dify-plugins/blob/main/docs/plugin-submission-requirements.md).
- [ ] I have read and comply with the [Plugin Development Guidelines](https://docs.dify.ai/en/develop-plugin/publishing/standards/contributor-covenant-code-of-conduct).
- [ ] I tested this plugin on Dify Community Edition and Dify Cloud, or documented any limitation below.
- [ ] The package contains only files needed at runtime.
- [ ] The package does not contain secrets, local credentials, `.env` files, `.git` directories, virtual environments, caches, logs, or IDE files.
- [ ] The package does not contain executables or bundled binaries, or I explained why they are required below.
- [ ] The plugin README includes setup steps, usage instructions, required APIs or credentials, connection requirements, and the source repository link.
- [ ] The plugin includes `PRIVACY.md` or a hosted privacy policy, and `manifest.yaml` references it.
- [ ] All user-facing text is primarily in English, with any localized README files following the [i18n guidance](https://docs.dify.ai/en/develop-plugin/features-and-specs/plugin-types/multilingual-readme).

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
registry; SDK transport can communicate with the host's file/storage services.

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

See VALIDATION.md in the public source repository for completed local checks: 67 core tests and real SDK source/package tests passed. The official package validator reports one blocker, the missing public contact email. It found no known vulnerabilities in the four pinned direct dependencies at check time. OpenCV is disclosed as a large runtime dependency requiring reviewer approval.
Update this section with the final package hash and validation commands after
binding the confirmed publisher and rebuilding. Do not copy the candidate hash
into a different package's submission.

## Reviewer notes

**DRAFT - NOT SUBMITTED.** Publisher identity and source repository are confirmed; the public contact
email is still pending. Required-check boxes intentionally remain unchecked until the final
release and publisher have verified each item.

Local SDK tests substitute only the platform file/storage transport and deny
network access. They do not establish successful installation in a live Dify
Community or Cloud instance. Live installation and end-to-end verification are
still pending and must not be represented as complete.

The plugin is conventional image processing, not a generative image model.
Large object reconstruction, clothing/pose replacement and text-generated
backgrounds are unsupported. Automatic skin protection uses a color heuristic.
English editing plans are supported, but explicit long-term preference opt-in
currently recognizes Chinese cue wording only, as disclosed in the README.
