"""The chat widget's script, web/t2-chat.js, checked as text (no browser runs in the tests)."""

import re

from ole5_chat.config import ROOT

WIDGET = (ROOT / "web" / "t2-chat.js").read_text(encoding="utf-8")


def test_no_field_of_the_widget_hides_one_of_its_methods():
    # A field set on the element wins over the class method of the same name: the voice bar's this.stage once
    # replaced stage(file), and picking a file threw.
    methods = set(re.findall(r"^ {4}(?:async |get |set |static )?([A-Za-z_]\w*)\s*\([^)]*\)\s*\{", WIDGET, re.M))
    fields = set(re.findall(r"\bthis\.([A-Za-z_]\w*)\s*=(?!=)", WIDGET))
    assert {"stage", "render", "voiceEvent"} <= methods and {"view", "voiceStage"} <= fields
    assert fields & methods == set()
