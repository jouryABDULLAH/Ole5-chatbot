# web/vendor

The browser speech detector's files, served from here so voice mode does not depend on the jsdelivr CDN (VOICE_LOG.md
D48). `web/voice.js` checks these files are here and served as JavaScript where needed, and falls back to the same
versions on the CDN when they are not. Kept byte for byte (`.gitattributes`: `-text`); `tests/test_voice_vendor.py`
checks the sizes and SHA-256 in both tables below and that nothing else is here. For the files, they are the ones
jsdelivr's file listing gives (`https://data.jsdelivr.com/v1/packages/npm/<package>@<version>?structure=flat`).

| File | From | Bytes | SHA-256 (base64) |
|---|---|---|---|
| `vad-web-0.0.31/bundle.min.js` | `@ricky0123/vad-web@0.0.31/dist/` | 69,345 | `Sz9y23jcX2XAupBHqQM1geFf5XgQo6xXQqyidNFwLh8=` |
| `vad-web-0.0.31/vad.worklet.bundle.min.js` | `@ricky0123/vad-web@0.0.31/dist/` | 2,480 | `ikj9x0KZSKL949KahLsaZMH2e0uleMyqdUi3+YnwanQ=` |
| `vad-web-0.0.31/silero_vad_v5.onnx` | `@ricky0123/vad-web@0.0.31/dist/` | 2,327,524 | `JiOilT9v89LB5hdAxs23FoEzR5smff7xFKSjzFvdeI8=` |
| `onnxruntime-web-1.22.0/ort-wasm-simd-threaded.mjs` | `onnxruntime-web@1.22.0/dist/` | 20,856 | `MN2FHZwAYilAUA9x3dL/iCDFy2UnCBYIAXW5WHBThag=` |
| `onnxruntime-web-1.22.0/ort-wasm-simd-threaded.wasm` | `onnxruntime-web@1.22.0/dist/` | 11,210,254 | `ca7wSVnFwbbeRhtlOOIFjjBmEANKharSdC0Mf9RTP+Q=` |

Downloaded from `https://cdn.jsdelivr.net/npm/` on 2026-10-27.

The license notices, downloaded on 2026-10-27 (the GitHub ones from the tag of the vendored version; they were the
same as on each repository's default branch that day):

| File | From | Bytes | SHA-256 (base64) |
|---|---|---|---|
| `vad-web-0.0.31/LICENSE` | github.com/ricky0123/vad, tag `@ricky0123/vad-web/v0.0.31` (2e5aca9) | 1,896 | `I568pCB4A9NQcSRRfA3WMkwNXPc+HEhq3ha6wXiPSnk=` |
| `vad-web-0.0.31/bundle.min.js.LICENSE.txt` | jsdelivr, `@ricky0123/vad-web@0.0.31/dist/` | 132 | `hdgprc3uD+ClyqlcI4+eTUl1jysiKUQXH10uj5JuAbQ=` |
| `vad-web-0.0.31/silero-vad.LICENSE` | github.com/snakers4/silero-vad, tag `v5.1` (84768ce) | 1,075 | `LmPpo4tuj8DHvDfOF0ysoYYocIVsba9Wl8+3heklUgs=` |
| `onnxruntime-web-1.22.0/LICENSE` | github.com/microsoft/onnxruntime, tag `v1.22.0` (f217402) | 1,073 | `LwfHJ1Gu2ZeQuKSGnPIxHfhahgsi3tBfoigDWHpIkiw=` |

vad-web is ISC, and its LICENSE also carries the Silero model's MIT notice. `bundle.min.js.LICENSE.txt` is the notice
the bundle's first line points to: the MIT header of the onnxruntime-web copy built into it. ONNX Runtime is MIT; the
npm package ships no license file, hence GitHub (its repository's `ThirdPartyNotices.txt` is not copied; the npm
package does not ship that either). The Silero VAD model is MIT: `silero_vad_v5.onnx` is byte for byte Silero's v5.1
`src/silero_vad/data/silero_vad.onnx` (git blob b3e3a90, unchanged to v5.1.2), so its notice is from that tag.

Serving them: `voice.js` only uses this folder from the same origin as itself (or with CORS on it), and only when the
scripts come as JavaScript; `src/ole5_chat/main.py` sets the types, since Windows can map `.mjs` to `text/plain`.
On localhost they load in well under a second. For customers over the internet, the host in front of the app should
compress them (gzip/brotli; the CDN sends about 4.4 MB instead of 13.6 MB) and may cache `/vendor/` for a year (the
folder names carry the versions). A page with a Content-Security-Policy needs `'wasm-unsafe-eval'`.

To change versions: onnxruntime-web must be the version embedded in the vad-web bundle (search `bundle.min.js` for it;
the package.json range is not enough): vad-web 0.0.31 → onnxruntime-web 1.22.0. Change together: the folder names,
`VAD_SOURCES` and `VAD_FILES` in `web/voice.js` (and its header comment), `FILES`, `NOTICES` and `CDN` in
`tests/test_voice_vendor.py`, the license notices, and these tables; then check voice in a browser.
