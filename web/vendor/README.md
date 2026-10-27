# web/vendor

The browser speech detector's files, served from here so voice mode does not depend on the jsdelivr CDN (VOICE_LOG.md
D48). `web/voice.js` checks these files are here and served as JavaScript where needed, and falls back to the same
versions on the CDN when they are not. Kept byte for byte (`.gitattributes`: `-text`); `tests/test_voice_vendor.py`
checks the sizes and SHA-256 below, which are the ones jsdelivr's file listing gives
(`https://data.jsdelivr.com/v1/packages/npm/<package>@<version>?structure=flat`).

| File | From | Bytes | SHA-256 (base64) |
|---|---|---|---|
| `vad-web-0.0.31/bundle.min.js` | `@ricky0123/vad-web@0.0.31/dist/` | 69,345 | `Sz9y23jcX2XAupBHqQM1geFf5XgQo6xXQqyidNFwLh8=` |
| `vad-web-0.0.31/vad.worklet.bundle.min.js` | `@ricky0123/vad-web@0.0.31/dist/` | 2,480 | `ikj9x0KZSKL949KahLsaZMH2e0uleMyqdUi3+YnwanQ=` |
| `vad-web-0.0.31/silero_vad_v5.onnx` | `@ricky0123/vad-web@0.0.31/dist/` | 2,327,524 | `JiOilT9v89LB5hdAxs23FoEzR5smff7xFKSjzFvdeI8=` |
| `onnxruntime-web-1.22.0/ort-wasm-simd-threaded.mjs` | `onnxruntime-web@1.22.0/dist/` | 20,856 | `MN2FHZwAYilAUA9x3dL/iCDFy2UnCBYIAXW5WHBThag=` |
| `onnxruntime-web-1.22.0/ort-wasm-simd-threaded.wasm` | `onnxruntime-web@1.22.0/dist/` | 11,210,254 | `ca7wSVnFwbbeRhtlOOIFjjBmEANKharSdC0Mf9RTP+Q=` |

Downloaded from `https://cdn.jsdelivr.net/npm/` on 2026-10-27. Licenses: vad-web is ISC
(https://github.com/ricky0123/vad), onnxruntime-web is MIT (https://github.com/microsoft/onnxruntime), and the Silero
VAD model is MIT (https://github.com/snakers4/silero-vad). **Not yet copied here:** their license texts, and
`bundle.min.js.LICENSE.txt` (132 bytes on jsdelivr), the notice file the bundle's first line points to. Add them before
this is distributed outside the demo (the test allows files named `*LICENSE*`).

Serving them: `voice.js` only uses this folder from the same origin as itself (or with CORS on it), and only when the
scripts come as JavaScript; `src/ole5_chat/main.py` sets the types, since Windows can map `.mjs` to `text/plain`.
On localhost they load in well under a second. For customers over the internet, the host in front of the app should
compress them (gzip/brotli; the CDN sends about 4.4 MB instead of 13.6 MB) and may cache `/vendor/` for a year (the
folder names carry the versions). A page with a Content-Security-Policy needs `'wasm-unsafe-eval'`.

To change versions: onnxruntime-web must be the version embedded in the vad-web bundle (search `bundle.min.js` for it;
the package.json range is not enough): vad-web 0.0.31 → onnxruntime-web 1.22.0. Change together: the folder names,
`VAD_SOURCES` and `VAD_FILES` in `web/voice.js` (and its header comment), `FILES` and `CDN` in
`tests/test_voice_vendor.py`, and this table; then check voice in a browser.
