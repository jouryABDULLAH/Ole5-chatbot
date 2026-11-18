from ole5_chat.agent.guards import canonical_ticket_numbers


def test_known_ticket_numbers_keep_their_literal_hyphens():
    assert canonical_ticket_numbers("تذكرتك T‑1001، وT‐1002.", ["T-1001", "T-1002"]) == \
        "تذكرتك T-1001، وT-1002."


def test_unknown_codes_and_longer_identifiers_are_not_rewritten():
    text = "Error E‑1001; T‑1002; T‑10010; XT‑1001; T‑1001‑X; T‑1001_A; non‑breaking"
    assert canonical_ticket_numbers(text, ["T-1001"]) == text


def test_literal_special_characters_in_ticket_numbers():
    assert canonical_ticket_numbers("(T.1‑2) Tz1‑2", ["T.1-2"]) == "(T.1-2) Tz1‑2"
