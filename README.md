# Local Retouch Studio for Dify

A CPU image-processing plugin for Dify, with fixed JSON editing recipes,
reference color matching, short-lived conversation assets and explicit
preference storage. It does not call external image-generation services.

See [plugin documentation](plugin/README.md),
[privacy policy](plugin/PRIVACY.md), and [publication status](PUBLICATION.md).

This public source repository is maintained under `liaoliaochenfeng2`. Version
0.1.1 is submitted in [Marketplace PR #3776](https://github.com/langgenius/dify-plugins/pull/3776); see PUBLICATION.md for the current
submission status. Live Dify installation verification remains outstanding,
and submission does not imply approval.

## Build

Use Python 3.12, install `plugin/requirements.txt`, then run:

```bash
python build_package.py --cli /path/to/official/dify
```

The package builder runs local tests with the real Dify SDK. These tests do not
replace installation and end-to-end testing in a running Dify instance.

## Publisher metadata

Publisher, repository and public contact metadata are bound for this submission.
Maintainers can bind metadata for future releases with:

```bash
python bind_publisher.py --author liaoliaochenfeng2 --repository https://github.com/liaoliaochenfeng2/dify-local-retouch --contact-email YOUR_PUBLIC_EMAIL
```

Rebuild the package after binding metadata. Never submit an unfilled contact
marker or an email address that the maintainer has not approved for publication.

Source and support: https://github.com/liaoliaochenfeng2/dify-local-retouch
