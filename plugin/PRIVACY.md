# Privacy policy

Local Retouch Studio processes user-supplied photographs inside the Dify plugin environment in which it is installed. The hosting operator controls that environment, its storage, logs, backups and any separately configured language or vision models.

## Data processed

The plugin reads uploaded source photographs and optional reference photographs, masks and replacement backgrounds through the Dify SDK file interface. It also processes the current request text, fixed JSON editing plans, operation reports, user and conversation identifiers, application identity, and explicitly saved preference notes. Photographs may contain personal information. The plugin does not identify people, compute biometric identity templates, use photographs for training, or intentionally collect analytics.

Source bytes cached for continued editing may retain original EXIF metadata, including GPS location if present. Edited photo outputs omit source EXIF. Preview images and reports may still reveal the photograph or user-supplied text and should be treated as user content.

## Where processing and storage occur

Image operations run in the host's plugin process using Pillow, NumPy and OpenCV. The plugin does not call external image APIs, fetch user-supplied URLs, download model weights, or execute arbitrary Python or shell code. It uses Dify SDK interfaces to read platform files and communicate with the host's plugin storage. The SDK can retrieve the URL of a platform file over HTTP; only trusted Dify-managed uploads should be bound to tool inputs. The host is responsible for validating file URLs and restricting outbound access. The plugin applies its byte-size check after SDK retrieval. Installation of dependencies may reach the package registry or mirror configured by the administrator. The plugin does not invoke a language or vision model; any such calls belong to the surrounding Dify application and its independently configured providers.

For continued editing, original source/reference/mask/background bytes and recipe metadata may be stored in the host's Dify SDK storage. Conversation records are scoped by application, user and conversation identity; preference records are scoped by application and user. Identifiers are hashed for owner keys. Hashing is not anonymization. Available SDK identity values are checked against supplied identifiers, and cross-conversation preferences are disabled without trusted application identity.

## Retention and deletion

- Conversation state has a 24-hour logical time-to-live, 64 fixed slots and a limit of 12 MiB per record including assets and metadata.
- Preferences have a 90-day logical time-to-live, 4096 fixed slots and a 4 KiB record limit. Preference notes are limited to eight entries and 1600 characters in total. They are saved only after explicit supported opt-in wording; this release's opt-in detector recognizes Chinese wording.
- Records can be evicted before their TTL expires. Expiration is enforced at read time; it does not immediately erase storage at the expiry instant. Access, replacement and explicit reset operations reclaim records.
- The prepare tool's `reset` action clears the current conversation's plugin cache. `reset_preferences` clears the user's preference record for this application. A failed reset reports failure rather than claiming deletion.

Clearing plugin records does not delete original platform uploads, output files, conversations, logs or backups. Contact the hosting operator about those records and the applicable retention policy. Cached user content is not a permanent archive, and a failed or evicted cache may require a fresh upload.

## Logs and errors

The plugin has no analytics or telemetry service. It returns status information, validation errors and processing reports to Dify; the platform may log tool inputs, outputs and failures under its own policy. Do not place secrets in image filenames, preference notes or editing instructions. Reports include user-provided plan labels and explanations and therefore may contain personal text.

## Contact

Project source and support: https://github.com/liaoliaochenfeng2/dify-local-retouch  
Issue tracker: https://github.com/liaoliaochenfeng2/dify-local-retouch/issues

Do not post personal photographs, credentials or private logs in a public issue. The hosting operator is the appropriate contact for access to, or deletion of, data in a particular Dify deployment.

Contact: 2748836629@qq.com
