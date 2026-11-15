# Validation record — 0.1.1 publication candidate

This is a local validation record, not a Marketplace approval or a live Dify
installation result. No Marketplace submission PR has been opened yet.

## Completed checks

- Python 3.12 with dify-plugin 0.10.2, Pillow 13.0.0, NumPy 2.3.5 and OpenCV Headless 4.14.0.94.
- 67 core tests: passed, no failures, errors or skipped tests.
- Real Dify SDK registration and local invocation: passed for source and unpacked package, including 11 negative cases. Network attempts during SDK smoke tests: zero.
- Original image, A/B previews, and selected candidate replay to full-size output: passed with synthetic images and an in-memory storage substitute.
- Official Dify CLI 0.6.3 package creation and checksum decoding: passed.
- Dify Marketplace Toolkit at commit `83ee707c1dde758678f94a311f15b42d74f8c402`: all package checks passed except missing public contact metadata. Online OSV lookup found no known vulnerabilities in the four pinned direct dependencies at check time; this is not a complete transitive-dependency or security guarantee.

## Remaining items

- The publisher must specify a monitored email address that may be included in public plugin metadata. `manifest.contact` is deliberately absent until that address is provided.
- The toolkit warns that `opencv-python-headless` is a large runtime dependency requiring reviewer approval. It is used for bilateral smoothing, inpainting, GrabCut, image remapping and color operations; no model weights or executable files are bundled in the package.
- The full Dify Community/Cloud installation and conversational workflow have not been tested in a live host. Local SDK registration does not establish host permissions, file transport, cache configuration or model-driven workflow behavior.
- PR checks, Marketplace duplicate-version checks and maintainer review have not run.

## Reproduce local tests

```bash
python tests/run_tests.py --source plugin --report validation/core_tests.json
python tests/test_plugin_package.py --source plugin --report validation/sdk_source_test.json
python build_package.py --cli /path/to/official/dify
```

The package must be rebuilt after public contact metadata is supplied. The hash
below identifies the current pre-submission candidate only.

- Package size: 62590 bytes.
- Archive SHA-256: `cb51b36ebec1416667fb4cc567a5b2dbe1ce62b4c9a66a4960f80713b983f23a`.
- Official plugin checksum: `4af29d523ac696245720e56291800c98fd2293b9c2cafeb1baee624038984179`.
