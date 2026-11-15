# Publication status and remaining requirements

The public source repository is https://github.com/liaoliaochenfeng2/dify-local-retouch, under the confirmed publisher `liaoliaochenfeng2`. Version 0.1.1 is a release candidate. No Marketplace pull request has been submitted, and the plugin is not yet an approved Marketplace listing. A monitored public contact email and live Dify verification remain pending.

The local core suite (67 tests) and SDK checks pass with the updated Pillow 13.0.0 dependency. These checks do not establish successful installation or end-to-end operation in a running Dify instance.

Before submitting:

1. Replace `REPLACE_WITH_CONTACT_EMAIL` with a real monitored email address approved for public disclosure. Keep the confirmed publisher and repository consistent throughout the release, and retain the issue tracker as a support channel. Do not infer or expose a private profile email.
2. Confirm the manifest, provider metadata, documentation and source release agree on author, plugin name and version.
3. Run the local tests, package with the official Dify CLI, inspect the archive and run the current Marketplace validator. Record actual commands and results, including warnings and whether vulnerability lookups ran.
4. Test an installed plugin in a supported live Dify environment. Confirm both tools appear, file outputs render, identity binding works, cache loss is reported, and A/B selection and reset behave correctly. Local SDK tests do not establish this.
5. Use the current PR template from `langgenius/dify-plugins`. Submit exactly one `.difypkg` in the appropriate author/plugin directory. The public source repository remains separate.
6. Disclose uploaded-photo processing, SDK file retrieval and storage, fixed package-file reads, cache retention, host-enforced timeouts, and the absence of arbitrary code or URL execution. Use High risk conservatively because original photo bytes can retain sensitive EXIF/GPS metadata, and disclose file handling and personal-photo processing under the current official rules.
7. Leave unchecked any testing or policy checkbox that has not been verified. Explain limitations in reviewer notes. Wait for maintainer approval and merge before describing the plugin as available in Marketplace.

The release must exclude private company workflows, model provider configuration, unpublished business files, credentials, source photographs, caches, virtual environments and test artifacts from the `.difypkg`. Repository development files may be public only when reviewed and relevant. Installing from the public Marketplace can still depend on a private deployment's own approval policy.
