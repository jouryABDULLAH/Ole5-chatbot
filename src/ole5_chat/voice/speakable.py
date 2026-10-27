"""The reply as it is spoken. Ole5 writes for a screen: markdown, menu paths with arrows ("الإعدادات ← المستخدمون"), one
step per line and the full ticket number. The words stay the same (D13); only what does not read aloud is changed. The
ticket number is replaced by where to find it and its last four digits (D12)."""

import re

DIGITS = {
    "ar": ["صفر", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة"],
    "en": ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
}
ON_SCREEN = {
    "ar": "رقم التذكرة ظاهر على شاشتك، وينتهي بـ {digits}.",
    "en": "Your ticket number is on your screen, ending in {digits}.",
}
# An earlier ticket's number (a status reply lists the customer's tickets) is said in place as how it ends, with the
# words that only introduce it: "Ticket number {N}, Archive button" → "Ticket ending in zero zero one two, Archive button".
ENDING_IN = {"ar": "رقمها ينتهي بـ {digits}", "en": "ending in {digits}"}
ENDING_IN_FIRST = {"ar": "التذكرة التي ينتهي رقمها بـ {digits}", "en": "Ticket ending in {digits}"}  # starting a sentence
EARLIER_LEAD = {"ar": r"(?:و?ب?رقم(?:ها)?\s*[:：]?\s*)?", "en": r"(?:(?:number|no\.?)\s*[:：]?\s*|#\s*)?"}
THEN = {"ar": "، ثم ", "en": ", then "}
# How names are said, where the voice gets them wrong: the Saudi voice read "Ole5" as "ولا خمسة". The spellings below
# are the user's picks from samples of each voice (D37, 2026-10-26); the diacritics make abdullah say "oo-lee".
OLE5 = re.compile(r"\bole\s?5\b", re.IGNORECASE)
PRONOUNCE = {"ar": [(OLE5, "أُولِي فايف")], "en": [(OLE5, "Oh-lee five")]}
SEGMENT_CHARS = 220
MARK = "\u0000"  # where the on-screen sentence goes, while the text is rebuilt
SENTENCE_DONE = re.compile(r"[.!?؟\n]\s*$")

LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
URL = re.compile(r"https?://\S+")
EMPHASIS = re.compile(r"(\*\*|__|\*|`+|~~)")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
BULLET = re.compile(r"^\s*(?:[-*•+]|\d{1,2}[.)])\s+", re.MULTILINE)
ARROW = re.compile(r"\s*(?:←|→|⟵|⟶|->|=>)\s*")
QUOTES = re.compile(r"[«»“”\"]")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}.*$", re.MULTILINE)
SENTENCE_END = re.compile(r"(?<=[.!?؟؛:])\s+")
CLAUSE_END = re.compile(r"(?<=[،,])\s+")
ENDS_SENTENCE = re.compile(r"[.!?؟؛:،,]$")
CONTENT_LEFT = re.compile(r"[^\W\d_]{2,}")
QUOTED = r"[\"'«`*(\[]*{}[\"'»`*)\]]*"
# The number with the words that only introduce it ("number is", "No.", "#", "رقم", "برقم", "هو"); the noun "ticket" is
# kept: "Ticket {N} was created" → "the ticket was created".
TICKET_NOUN = re.compile(r"\b(your\s+|the\s+|a\s+)?ticket\s+(?:(?:number|no\.?)\s*(?:is\s*)?[:：]?\s*|#\s*)?{}",
                         re.IGNORECASE)
NUMBER_LEAD = {
    "ar": r"(?:(?:و?ب?رقم(?:ها)?|هو)\s*[:：]?\s*)*",
    "en": r"(?:(?:with|under)\s+)?(?:(?:the|your)\s+)?(?:(?:number|no\.?)\s*(?:is\s*)?[:：]?\s*|#\s*|is\s+)*",
}
NOT_CONTENT = {
    "ar": {"رقم", "التذكرة", "تذكرتك", "التذكره", "تذكرتكم", "هو", "الخاص", "بك", "رقمها", "برقم"},
    "en": {"your", "the", "a", "ticket", "number", "is", "no", "with", "has", "been", "here", "s"},
}


def _digits(number: str, language: str) -> str:
    last = [c for c in number if c.isdigit()][-4:]
    return " ".join(DIGITS[language][int(c)] for c in last)


def _earlier_numbers(text: str, numbers: list[str], language: str) -> str:
    """Each earlier ticket's number in the reply, said in place as its last four digits (D12)."""
    for number in (n for n in numbers if n and n in text):
        lead = re.compile(EARLIER_LEAD[language] + QUOTED.format(re.escape(number)), re.IGNORECASE)
        digits = _digits(number, language)
        text = lead.sub(lambda m: (ENDING_IN_FIRST if re.search(r"(?:^|[.!?؟]\s*)$", m.string[:m.start()])
                                   else ENDING_IN)[language].format(digits=digits), text)
    return text


def _ticket_numbers(text: str, numbers: list[str], language: str) -> str:
    """The ticket number leaves the reply: in English "ticket {N}" becomes "the ticket"; elsewhere the number goes with
    the words that introduce it, and a clause left with nothing else ("Your ticket number is {N}.") is dropped. A
    sentence saying where the number is and how it ends takes its place, at the end of the number's sentence, so a
    question that follows (the next issue's offer) is still the last thing said."""
    found = [n for n in numbers if n and n in text]
    if not found:
        return text
    for number in found:
        quoted = QUOTED.format(re.escape(number))
        noun = re.compile(TICKET_NOUN.pattern.format(quoted), re.IGNORECASE)
        lead = re.compile(NUMBER_LEAD[language] + quoted, re.IGNORECASE)
        kept: list[str] = []
        place, pending = None, False  # place: where in kept the on-screen sentence goes
        for part in re.split(r"(?<=[.!?؟؛،,\n])", text):
            if number in part:
                said = noun.sub(lambda m: f"{m.group(1) or 'the '}ticket", part) if language == "en" else part
                said = lead.sub("", said)
                if {w.lower() for w in CONTENT_LEFT.findall(said)} - NOT_CONTENT[language]:
                    kept.append(said)
                pending = pending or place is None
            else:
                kept.append(part)
            if pending and SENTENCE_DONE.search(part):
                place, pending = len(kept), False
        if place is None:
            place = len(kept)
        text = "".join([*kept[:place], MARK, *kept[place:]])
    on_screen = ON_SCREEN[language].format(digits=_digits(found[-1], language))
    text = text.replace(MARK, f" {on_screen} ", 1).replace(MARK, " ")
    text = re.sub(r"\s+([.!?؟،,])", r"\1", text)
    text = re.sub(r"([،,])\s*([.!?؟])", r"\2", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ،,")
    if text and not ENDS_SENTENCE.search(text):
        text += "."
    return text


def speakable(text: str, language: str, ticket_numbers: list[str] = (), earlier: list[str] = ()) -> str:
    """The reply as plain spoken sentences. ticket_numbers: the tickets created in this session; earlier: the
    customer's other tickets, which a status reply names."""
    language = language if language in DIGITS else "en"
    text = LINK.sub(r"\1", text)
    text = URL.sub("", text)
    text = TABLE_RULE.sub("", text)
    text = HEADING.sub("", text)
    text = BULLET.sub("", text)
    text = EMPHASIS.sub("", text)
    text = QUOTES.sub("", text.replace("|", " "))
    text = ARROW.sub(THEN[language], text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = " ".join(line if ENDS_SENTENCE.search(line) else f"{line}." for line in lines)
    text = re.sub(r"([،,])\s*([.!?؟])", r"\2", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = _earlier_numbers(text, [n for n in earlier if n not in ticket_numbers], language)
    text = _ticket_numbers(text, list(ticket_numbers), language)
    for pattern, said in PRONOUNCE[language]:
        text = pattern.sub(said, text)
    return text


def segments(text: str, limit: int = SEGMENT_CHARS) -> list[str]:
    """Sentences grouped into pieces of at most about `limit` characters, each one speech request. The first piece is
    a single sentence, so the first sound comes quickly; a sentence longer than the limit is cut at commas."""
    pieces: list[str] = []
    for sentence in SENTENCE_END.split(text.strip()):
        if len(sentence) <= limit:
            pieces.append(sentence)
            continue
        part = ""
        for clause in CLAUSE_END.split(sentence):
            if part and len(part) + 1 + len(clause) > limit:
                pieces.append(part)
                part = clause
            else:
                part = f"{part} {clause}".strip()
        if part:
            pieces.append(part)
    out: list[str] = []
    for piece in filter(None, (p.strip() for p in pieces)):
        if len(out) > 1 and len(out[-1]) + 1 + len(piece) <= limit:
            out[-1] = f"{out[-1]} {piece}"
        elif len(out) == 1 and len(out[0]) < 40 and len(out[0]) + 1 + len(piece) <= limit:
            out[0] = f"{out[0]} {piece}"
        else:
            out.append(piece)
    return out
