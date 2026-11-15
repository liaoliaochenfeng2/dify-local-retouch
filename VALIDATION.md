# Validation record — 0.1.1 publication candidate

This is a local validation record, not a Marketplace approval or a live Dify
installation result. See PUBLICATION.md for the current submission status.

## Completed checks

- Python 3.12 with dify-plugin 0.10.2, Pillow 13.0.0, NumPy 2.3.5 and OpenCV Headless 4.14.0.94.
- 67 core tests: passed, no failures, errors or skipped tests.
- Real Dify SDK registration and local invocation: passed for source and unpacked package, including 11 negative cases. Network attempts during SDK smoke tests: zero.
- Original image, A/B previews, and selected candidate replay to full-size output: passed with synthetic images and an in-memory storage substitute.
- Official Dify CLI 0.6.3 package creation and checksum decoding: passed.
- Official `test-plugin-install.py` in serverless mode: plugin booted, localhost HTTP responded, and the script reported success. This does not substitute for a full Dify host installation.
- Dify Marketplace Toolkit at commit `83ee707c1dde758678f94a311f15b42d74f8c402`: all package checks passed with zero blocking failures. Online OSV lookup found no known vulnerabilities in the four pinned direct dependencies at check time; this is not a complete transitive-dependency or security guarantee.

## Remaining items

- The toolkit warns that `opencv-python-headless` is a large runtime dependency requiring reviewer approval. It is used for bilateral smoothing, inpainting, GrabCut, image remapping and color operations; no model weights or executable files are bundled in the package.
- The full Dify Community/Cloud installation and conversational workflow have not been tested in a live host. Local SDK registration does not establish host permissions, file transport, cache configuration or model-driven workflow behavior.
- PR checks, Marketplace duplicate-version checks and maintainer review are tracked in https://github.com/langgenius/dify-plugins/pull/3776. Local results do not establish their outcome.

## Reproduce local tests

```bash
python tests/run_tests.py --source plugin --report validation/core_tests.json
python tests/test_plugin_package.py --source plugin --report validation/sdk_source_test.json
python build_package.py --cli /path/to/official/dify
```

The public contact is bound. The hash below identifies the exact package prepared for this submission; any change requires a rebuild and renewed validation.

- Package size: 62537 bytes.
- Archive SHA-256: `b00cf551cc5dcc39697acb3937c5277f697f13bcda11ae896da8c63da7581809`.
- Official plugin checksum: `484124ce707a458eab9e2286aab29f456c5c14e833d1fb77429bf53b8eb0d16a`.
