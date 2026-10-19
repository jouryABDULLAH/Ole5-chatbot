"""Stand-in knowledge base for development: a few fixed passages matched by keyword. Every title starts with STUB so it is never mistaken for the real one."""

from ole5_chat.knowledge.contracts import Evidence, Retrieval, Verdict

PASSAGES = [
    (("password", "كلمة المرور", "كلمة السر"), Evidence(
        text="To reset your password, choose Forgot password on the sign-in page and enter your username. "
             "A reset link is sent to your registered email and stays valid for 30 minutes.",
        document="STUB-01 Signing in", section="Resetting a password",
        source_file="STUB-01-EN-signing-in.md", relation="DIRECT", score=3,
        reason="Gives the steps to reset a password")),
    (("archive", "أرشيف", "أرشفة"), Evidence(
        text="The Archive screen lists correspondence that has been closed.",
        document="STUB-02 Archive", section="Overview",
        source_file="STUB-02-EN-archive.md", relation="INFERENTIAL", score=2,
        reason="On the subject of the archive, but does not answer how to use it")),
]


def search(question: str) -> Retrieval:
    text = question.casefold()
    found = [e for words, e in PASSAGES if any(w in text for w in words)]
    if any(e.relation == "DIRECT" for e in found):
        verdict = Verdict.ANSWERABLE
    else:
        verdict = Verdict.PARTIAL if found else Verdict.NOT_IN_KB
    return Retrieval(query=question, verdict=verdict, evidence=found, queries_tried=[question])
