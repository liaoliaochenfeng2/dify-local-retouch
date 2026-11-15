# Local Retouch Studio

Local Retouch Studio processes uploaded photographs inside the hosting Dify plugin environment using Pillow, NumPy and OpenCV. A Dify text or vision model can turn a user's request into a bounded JSON editing plan; this plugin executes that plan and returns images and an operation report.

It does not call an image-generation API, download model weights, execute generated Python or shell commands, or send photographs to an external image service. The plugin itself does not invoke an LLM. Any language or vision models used by your application are configured separately in Dify.

[Chinese documentation](readme/README_zh_Hans.md) | [Privacy policy](PRIVACY.md)

**Source:** https://github.com/liaoliaochenfeng2/dify-local-retouch  
**Support:** https://github.com/liaoliaochenfeng2/dify-local-retouch/issues

## Requirements and installation

- Dify 1.16.1 or later, with Python 3.12 plugin support.
- A plugin environment that can install the exact dependencies in `requirements.txt`: the Dify plugin SDK, Pillow, NumPy and OpenCV Headless. Dependency installation may contact the package mirror configured by the host; an offline mirror may also be used.
- The manifest requests 1 GiB of process memory and 1 GiB of Dify SDK storage. Hosting limits, timeouts and concurrency remain controlled by the Dify administrator. Large images and parallel runs need capacity planning.
- No API key, external image service, separate rendering server or GPU is required by this plugin.

Once approved and listed, install the package through your Dify Marketplace interface. Until then, local package installation is possible only where your deployment policy permits it. Installing this plugin adds two tools; it does not automatically create a complete conversational editing application. Configure the tools in a Chatflow and bind trusted system variables as described below. Do not bypass a host's plugin verification policy.

This public source is a release candidate under the confirmed publisher `liaoliaochenfeng2`. Local core tests and SDK checks pass, but live Dify installation verification and a public contact email remain pending. No Marketplace pull request has been submitted. Submission and approval are separate steps.

## Tools and safe variable binding

| Tool | Purpose |
| --- | --- |
| `prepare` | Resolve uploaded asset roles; load bounded conversation state and saved preferences; return original assets, vision thumbnails and relevant style cards. |
| `retouch` | Validate and execute a complete editing plan from the original image; return a full-size result or two preview candidates; record the recipe for later edits. |

Bind `user_id` directly to `sys.user_id`, `conversation_id` to `sys.conversation_id`, and the prepare tool's `query` to `sys.query`. Never ask an LLM to invent these identifiers. Bind file inputs only to trusted Dify-managed uploads. The SDK may retrieve those file URLs; the hosting deployment must enforce upload-size and outbound-network restrictions before download. This plugin checks the 20 MiB byte limit after SDK retrieval and is not an independent SSRF or download-bandwidth boundary. Bind `images` to the uploaded files. Application identity comes from the SDK session. Available SDK user/session identities are checked against the supplied values; persistent preferences are disabled without a trusted application identity.

The prepare request is a JSON string. For one new source photograph:

```json
{
  "action": "edit",
  "asset_roles": ["source"],
  "selected_id": "",
  "preference_notes": [],
  "preferred_style": "",
  "style_hints": ["clean_natural"]
}
```

Available asset roles are `source`, `reference`, `mask` and `background`; each may occur once, in upload order. Provide at most four files. Always make roles explicit in an application. A reference photograph supplies color statistics only and is not a replacement background.

Route the original files returned by `prepare` to the matching `retouch` inputs. Bind `source_sha256` and `context_revision` directly from `prepare`. Do not substitute the vision thumbnail or a previous edited result for the source image.

Pass a JSON string such as this as `plan_json`:

```json
{
  "mode": "final",
  "explanation": "Apply a restrained natural color adjustment.",
  "variants": [{
    "id": "A",
    "label": "Natural",
    "recipe": {
      "version": 2,
      "operations": [{
        "op": "style",
        "name": "clean_natural",
        "strength": 0.3,
        "protect_skin": true
      }]
    }
  }]
}
```

`final` accepts exactly one variant. `preview` accepts exactly two variants in A, B order. Each recipe contains 1 to 10 validated operations. Unsupported keys, duplicate JSON keys, non-finite numbers and out-of-range parameters are rejected. See `tools/retouch_engine.py` for the bounded operation contract and `tools/prepare.yaml` / `tools/retouch.yaml` for tool inputs and outputs.

## Editing capabilities

- Global brightness, contrast, saturation, sharpness and warmth; local brightness and warmth.
- Exposure, highlights, shadows, black/white points and gamma; monotonic RGB/channel curves; bounded HSL color-range adjustments.
- Eight lightweight color directions: `clean_natural`, `warm_soft`, `cool_editorial`, `film_matte`, `retro_warm`, `night_atmosphere`, `travel_fresh`, and `portrait_neutral`.
- Limited reference matching using Lab color statistics, with strength capped at 0.5. It does not transfer the reference subject, composition or lighting geometry.
- Light local bilateral smoothing, small-area classical inpainting, background compositing and bounded local liquify.
- Uploaded masks and bounded geometric selections. GrabCut can refine a suitably identified foreground, but complex hair or clutter may require a precise uploaded mask.

Style cards are interpretation aids, not a guarantee that a natural-language request will be understood correctly. The bundled guide contains English guidance and intentional Chinese phrase examples. Its 36 semantic cases are manually written expectations, not model-training data or completed model evaluations. A host application still needs its own prompt, vision step, routing, review and tests.

## Limits and output

| Item | Limit or behavior |
| --- | --- |
| Input file | Static JPEG, PNG or WebP; at most 20 MiB per file |
| Dimensions | At least 16 pixels per side; at most 6000 per side and 12,600,000 total pixels |
| Color handling | EXIF orientation applied; supported ICC input converted to sRGB; high-bit-depth and floating-point input rejected |
| Final image | Full oriented source dimensions, 8-bit PNG; source EXIF is omitted; no silent downscaling to pass input limits |
| Recipe | Up to 10 operations; style and reference matching at most once each |
| Inpainting | Selected area at most 8% per step and 12% accumulated over the recipe |
| Cache | At most 12 MiB including assets and metadata per conversation record |

For ordinary local edits, white in an uploaded mask means modify and black means preserve. For background replacement, white means preserve the foreground and black means replace the background. The mask must match the oriented source dimensions. `selection_mask.png`, when returned, represents the last relevant selection; inspect the report for its meaning.

A final run returns `xiaorun_retouched.png`, `review_preview.jpg` and `recipe.json`, optionally with `selection_mask.png`. A preview run returns `candidate_A.jpg`, `candidate_B.jpg`, `review_preview.jpg` and `recipe.json`. Candidate images are thumbnails; choosing a candidate and running its saved recipe produces a full-size PNG. Errors return no partial final image.

## Revisions, preferences and storage

Every edit is replayed from the original source using a complete recipe. Sending a new source resets the previous assets and recipe for that conversation. An app can resolve a bare A/B selection directly; a request such as "B, but less warm" must use the selected recipe as its baseline and apply the additional feedback before rendering.

Storage is a bounded cache, not a photo archive: 64 conversation slots with a 24-hour logical TTL, and 4096 preference slots with a 90-day logical TTL. Entries can be evicted earlier. The limits refer to the plugin's storage namespace, not a guarantee of capacity for every user. Large files can still be processed in the current turn even when they cannot fit the cache. On a cache warning, have the user re-upload the original and any required assets before continuing.

Cross-conversation preferences require explicit opt-in. In this release, the preference-saving cue detector recognizes Chinese opt-in wording; English editing plans work, but English-only requests to remember preferences do not activate persistence. See the Chinese documentation for the supported cues. Notes are limited to eight entries and 1600 characters in total. Ordinary feedback changes the current recipe only.

Use prepare actions `reset` and `reset_preferences` to clear the respective plugin records. TTL is checked when reading and is not a promise of immediate physical deletion. Platform uploads, chat histories, outputs, logs and backups have independent retention policies. Read the privacy policy before enabling caching for personal photos.

## Practical boundaries

This is a conventional image-processing tool. It does not generate a new photograph, replace clothing, change a pose, reconstruct complex occluded content, expand an image, or turn a text prompt into a new background. Background replacement uses a solid color or an uploaded background. Small-area inpainting cannot reliably remove large people or restore hidden buildings. Smoothing and liquify do not provide automatic professional portrait reconstruction.

Skin protection is a color heuristic that reduces some changes; it is not face recognition, semantic skin segmentation or an exact protection guarantee. Exposure and curves cannot recover detail already clipped in the source. Review outputs and masks before delivery.

Contact: REPLACE_WITH_CONTACT_EMAIL
