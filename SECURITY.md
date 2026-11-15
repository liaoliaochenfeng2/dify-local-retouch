# Security boundaries

Local Retouch Studio accepts uploaded images and a fixed JSON editing schema. It does not accept Python, shell, SQL, file paths or arbitrary URLs as instructions. The renderer operates on byte buffers. The prepare tool reads the bundled style guide from its fixed package path; Dify SDK interfaces handle platform file retrieval and plugin storage.

Uploads are bounded to 20 MiB each, 6000 pixels per side and 12,600,000 pixels in total. Static JPEG, PNG and WebP are accepted; unsupported formats and high-bit-depth input are rejected. Recipes have at most 10 bounded operations. The code rejects duplicate JSON keys, non-finite numeric values and unknown fields. Masks and image dimensions are checked before processing. Classical inpainting and liquify have additional area and strength bounds.

State uses fixed-size, identity-scoped cache slots and logical expiry. A record is checked against its owner and revision before use. SDK storage does not provide a transactional compare-and-set, so revisions are optimistic rather than a distributed locking guarantee. Applications should avoid concurrent edits to the same conversation and require a fresh upload after a continuity warning.

The plugin requests 1 GiB of process memory and 1 GiB of storage. It relies on host process limits and invocation timeouts; these controls are not a substitute for operator configuration. Processing user-supplied images involves third-party image decoders. Keep pinned dependencies reviewed and updated, and limit concurrent large-image jobs according to host capacity.

## Reporting

Source and support: https://github.com/liaoliaochenfeng2/dify-local-retouch

If the repository offers private vulnerability reporting, use it for security issues. Otherwise open a minimal issue requesting a private contact channel, without publishing exploit data, personal images, credentials or sensitive logs. No independent security audit or production isolation certification is claimed.

Contact: REPLACE_WITH_CONTACT_EMAIL
