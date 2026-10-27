"""The speech detector's files in web/vendor (D48): byte for byte the ones jsdelivr serves, with their license notices,
and the same versions and file names that web/voice.js loads, the local copy first and the CDN after it."""

import base64
import hashlib
import re

import pytest

from ole5_chat.config import ROOT

VENDOR = ROOT / "web" / "vendor"
VAD, ORT = "vad-web-0.0.31", "onnxruntime-web-1.22.0"
# Size and SHA-256 (base64) from jsdelivr's file listing, data.jsdelivr.com/v1/packages/npm/<package>@<version>.
FILES = {
    f"{VAD}/bundle.min.js": (69345, "Sz9y23jcX2XAupBHqQM1geFf5XgQo6xXQqyidNFwLh8="),
    f"{VAD}/vad.worklet.bundle.min.js": (2480, "ikj9x0KZSKL949KahLsaZMH2e0uleMyqdUi3+YnwanQ="),
    f"{VAD}/silero_vad_v5.onnx": (2327524, "JiOilT9v89LB5hdAxs23FoEzR5smff7xFKSjzFvdeI8="),
    f"{ORT}/ort-wasm-simd-threaded.mjs": (20856, "MN2FHZwAYilAUA9x3dL/iCDFy2UnCBYIAXW5WHBThag="),
    f"{ORT}/ort-wasm-simd-threaded.wasm": (11210254, "ca7wSVnFwbbeRhtlOOIFjjBmEANKharSdC0Mf9RTP+Q="),
}
# The license notices as downloaded on 2026-10-27; where each is from is in web/vendor/README.md.
NOTICES = {
    f"{VAD}/LICENSE": (1896, "I568pCB4A9NQcSRRfA3WMkwNXPc+HEhq3ha6wXiPSnk="),
    f"{VAD}/bundle.min.js.LICENSE.txt": (132, "hdgprc3uD+ClyqlcI4+eTUl1jysiKUQXH10uj5JuAbQ="),
    f"{VAD}/silero-vad.LICENSE": (1075, "LmPpo4tuj8DHvDfOF0ysoYYocIVsba9Wl8+3heklUgs="),
    f"{ORT}/LICENSE": (1073, "LwfHJ1Gu2ZeQuKSGnPIxHfhahgsi3tBfoigDWHpIkiw="),
}
CDN = {VAD: "@ricky0123/vad-web@0.0.31/dist/", ORT: "onnxruntime-web@1.22.0/dist/"}
JS = (ROOT / "web" / "voice.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("path", sorted(FILES | NOTICES))
def test_a_vendored_file_or_notice_is_byte_for_byte_the_one_downloaded(path):
    data = (VENDOR / path).read_bytes()
    assert (len(data), base64.b64encode(hashlib.sha256(data).digest()).decode()) == (FILES | NOTICES)[path]


def test_nothing_else_is_vendored_but_the_readme():
    found = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*") if p.is_file()}
    assert found == set(FILES) | set(NOTICES) | {"README.md"}


def test_the_bundle_points_to_its_notice_next_to_it():
    first = (VENDOR / VAD / "bundle.min.js").read_text(encoding="utf-8").split("\n", 1)[0]
    assert first == "/*! For license information please see bundle.min.js.LICENSE.txt */"
    assert f"{VAD}/bundle.min.js.LICENSE.txt" in NOTICES


def test_voice_js_tries_the_vendored_folders_first_then_the_same_versions_on_the_cdn():
    local = re.search(r'\{ vad: HERE \+ "vendor/([^"]+)/", ort: HERE \+ "vendor/([^"]+)/" \}', JS)
    cdn = re.search(r'\{ vad: "https://cdn\.jsdelivr\.net/npm/([^"]+)",\s*ort: "https://cdn\.jsdelivr\.net/npm/([^"]+)" \}',
                    JS)
    assert local.groups() == (VAD, ORT) and cdn.groups() == (CDN[VAD], CDN[ORT])
    assert JS.index("const VAD_SOURCES = [") < local.start() < cdn.start()
    assert "const [local, cdn] = VAD_SOURCES;" in JS and "? [local, cdn] : [cdn]" in JS


def test_voice_js_checks_and_loads_exactly_the_vendored_files():
    block = re.search(r"const VAD_FILES = \{(.*?)\};", JS, re.S).group(1)
    named = {key: set(re.findall(r'"([^"]+)"', names)) for key, names in re.findall(r"(vad|ort): \[([^\]]*)\]", block)}
    assert named == {key: {p.split("/")[1] for p in FILES if p.startswith(folder)} for key, folder in (("vad", VAD), ("ort", ORT))}
    assert 'model: "v5"' in JS   # silero_vad_v5.onnx
