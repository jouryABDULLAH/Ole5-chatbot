"""The task strings: what respond_agent is asked to do this turn. The handlers pick and fill them; prompt.respond puts
the task into the system prompt, with RESPOND_TASK when a turn sets none."""

RESPOND_TASK = ("Answer the customer from the passages. If no passage answers the question, say you have no answer for "
                "this and ask whether they would like a ticket opened.")
TALK_TASK = ("This message needs no search. Reply to what the customer actually said, in one or two short lines. A "
             "greeting gets the persona's greeting and thanks gets its thanks line; anything else gets a plain reply to "
             "it, never a greeting or thanks line. If the customer says they have a problem, or asks for a ticket, but "
             "not what it is about, ask what it is in one short open question, with no acknowledgement before it: "
             "\"يسعدني مساعدتك، ما المشكلة التي تواجهها؟\" or \"يسعدني مساعدتك، ما الموضوع الذي تريد فتح التذكرة "
             "بشأنه؟\" If they say there is no problem or nothing to add, don't ask again: say you're here if they need "
             "anything. Don't state a product fact that isn't already in this chat, and don't offer a ticket.")


# What respond_agent is asked to do this turn, set by the handler. {issue} is pending[0].text, {new} the issue just queued.
TASKS = {
    "question": RESPOND_TASK,
    "talk": TALK_TASK,
    "chat_offer_ticket": "Answer the customer's message as usual. A ticket offer for this issue is still open: {issue}. "
                         "If you answered something else, end by asking whether they would still like that ticket "
                         "opened. Example: the customer asks how to reset their password while a ticket offer for "
                         "'dark mode' is open. Reply with the password steps, then: 'Would you still like me to open "
                         "the ticket about dark mode?'",
    "chat_ticket": "Answer the customer's message as usual. Then continue the current ticket: {current}",
    "offer": "This needs the support team: {issue}. Say in one line that you can't resolve it here, and ask whether "
             "they would like a ticket opened.",
    "queued": "The customer raised a new issue: {new}. It will be handled after the current ticket ({issue}). Say so in "
              "one line. Don't answer the new issue and don't offer a ticket for it now. Then continue the current "
              "ticket: {current}",
    "accept": "The customer agreed to a ticket for: {issue}. Ask what the support team will need to act on it: work "
              "out the likely cause and ask what would confirm or rule it out, a few things at a time, skipping what "
              "the customer already said. Ask about what the customer sees and did. Don't name a screen, template, "
              "setting or option unless a passage names it. Nothing has been created yet; "
              "the ticket is created only when they submit the summary. Don't say a ticket is opened, created or "
              "being opened.",
    "declined": "The customer said no to opening a ticket; none was opened. Acknowledge it in one line without saying "
                "a ticket was cancelled or closed, and say they can contact support at any time.",
    "urgent_ready": "This is urgent, so the ticket was prepared right away without the usual questions: {issue}. The "
                    "summary is shown with a Submit button. Nothing has been created yet. Say in one line that because "
                    "it is urgent the summary is ready now, and ask whether to submit the ticket for: {issue}. Don't "
                    "repeat the summary.",
    "details": "Ask what else the support team needs to act on: {issue}. A few things at a time, skipping what the "
               "customer already said. If the team has enough, ask instead whether there is anything else they'd like "
               "to add. Ask about what the customer sees and did. Don't name a screen, template, setting or option "
               "unless a passage names it. Nothing has been created yet; the ticket is "
               "created only when they submit the summary. Don't say a ticket is opened, created or being opened. "
               "Don't ask again a question you already asked; if the customer didn't answer it, they don't know.",
    "open_question": "Ask whether there is anything else they'd like to add about: {issue}. Nothing else.",
    "confirm": "The ticket summary is ready and shown with a Submit button. Nothing has been created yet; the ticket is "
               "created only when they submit. Say the summary is ready in one line and ask whether to submit the "
               "ticket for: {issue}. "
               "Don't say the ticket is created, opened or being opened. Don't repeat the summary.",
    "changed": "The ticket summary was updated as the customer asked and is shown again. Say what changed in one line "
               "and ask whether to submit the ticket for: {issue}.",
    "created": "The ticket {number} was created. Tell the customer its number and that the support team will reply by "
               "email. Nothing more about this ticket.",
    "cancelled": "The customer cancelled the ticket. Acknowledge it in one line and say they can contact support at any "
                 "time.",
    "status": "The customer asked about their tickets. Today: {today}. These are the tickets on their account, newest "
              "first, from the ticket system, one per line as number, title, date, state:\n{tickets}\nAnswer in "
              "sentences, not as a list copied from above: one ticket is one sentence; several are one line each. "
              "Give the number, title, date and state. Titles stay as written; your own words follow the customer's "
              "language. A state of unknown means the system didn't return one: say the state isn't available right "
              "now, never the word unknown. If the customer asked about one ticket (the latest, a number, a title, a "
              "date), give that one only. If they didn't say which, give the 5 most recent and say how many more there "
              "are. If they ask about a ticket not in the list, say you have no record of it here. State nothing that "
              "isn't in the list: no timeframes, no promises, nothing about what the team is doing.",
    "status_none": "The customer asked about their tickets. There are no tickets on their account from this chat. Say "
                   "so in one line, and that they can describe a problem here to get help or open one. Don't ask "
                   "whether to open a ticket.",
}
URGENT = "This sounds urgent. "
# Appended to queued when the new issue is urgent.
# How a reply goes back to the current ticket, by phase (the queued task, and chat in collect or confirm).
CURRENT = {
    "offer_ticket": "ask whether they would like a ticket for: {issue}.",
    "collect": "ask what else the support team needs for: {issue}.",
    "confirm": "the summary for {issue} is still shown; ask whether to submit it.",
}
QUEUED_URGENT = " Because the new issue is urgent, also say they can cancel the current one to start it now."


# The voice channel (VOICE_PLAN.md section 6): the confirm step and the ticket replies, worded for a customer who
# may not be looking at the screen. The handlers use them on a voice turn.

PRIORITY_AR = {"Low": "منخفضة", "Medium": "متوسطة", "High": "عالية", "Critical": "حرجة"}
# The last words of every reply that asks to submit, said word for word. The closing question has no yes or send word
# in it, so the assistant's own echo of its last words cannot answer it.
VOICE_CLOSE = {
    "ar": "إن أردت تعديل شيء فأخبرني، ولإرسالها قل: نعم أرسلها. هل تريد إرسالها الآن؟",
    "en": "If anything needs changing, tell me; to send it, say: yes, submit. Would you like it submitted now?",
}
VOICE_SUBMIT_PHRASE = {"ar": "نعم أرسلها", "en": "yes, submit"}
VOICE_NUMBER = {"ar": "رقم تذكرتك هو {number}.", "en": "Your ticket number is {number}."}
VOICE_ARABIC = (" Speak formal Arabic: restate the summary in your own Arabic words, keeping product, screen and error "
                "names as written, address the customer directly, and don't quote the English.")
VOICE_READ_BACK = ("Read the summary back in two or three short spoken sentences, in your own words and without labels "
                   "such as \"title\" or \"details\": what the ticket is about ({about}) and its priority, "
                   "{priority}{urgent}.")
VOICE_END = " Then end with exactly these words: \"{close}\""
VOICE_TASKS = {
    "confirm": "The ticket summary is ready and shown on screen. Nothing has been created yet; the ticket is created only "
               "when they submit. " + VOICE_READ_BACK + VOICE_END + " Don't say the ticket is created, opened or being "
               "opened.",
    "urgent_ready": "This is urgent, so the ticket was prepared right away without the usual questions: {issue}. Nothing "
                    "has been created yet. Say that in one sentence. Then " + VOICE_READ_BACK[0].lower()
                    + VOICE_READ_BACK[1:] + VOICE_END,
    "changed": "The ticket summary now differs from before only in: {change}. Compare that with what the customer asked: "
               "say in one sentence what of their request is now on the summary, and if part of it is not there, say "
               "that part was not changed. Then "
               "read the rest of the summary back in one or two short sentences in your own words, without repeating "
               "the change: what the ticket is about ({about}) and its priority, {priority}{urgent}." + VOICE_END,
    "unchanged": "The customer asked to change the ticket summary, but nothing on it changed: it is still about {about}, "
                 "with priority {priority}{urgent}. If what they asked for is already on the summary, say it already "
                 "reads that way, and end with exactly these words: \"{close}\" Otherwise say in one sentence that the "
                 "change could not be made, and ask them to say exactly what to change; then don't ask them to submit.",
    "change_failed": "The change could not be made; the summary on screen is unchanged. Say so in one short sentence and "
                     "ask them to say the change again. Don't ask them to submit.",
    "submit_failed": "The ticket could not be created because of a problem on our side; the summary on screen is "
                     "unchanged. Say so in one sentence, and that they can say \"{phrase}\" to try again.",
    "submit_failed_again": "The ticket could not be created again. Apologise once, say it can't be created right now, and "
                           "suggest trying again later. Don't ask them to submit.",
    "created": "The ticket {number} was created. Say it was created in one sentence, then give the number in a sentence of "
               "its own written exactly \"{number_sentence}\", then say the support team will reply by email.",
    "reconfirm_line": "Nothing has been submitted. Reply with exactly this sentence and nothing else: \"{line}\"",
    "reconfirm": "Nothing has been submitted and nothing on the summary was changed: the customer's reply did not clearly "
                 "say to submit. If they asked to change something, say it has not been changed yet and ask them to say "
                 "the change on its own. If they asked a question, answer it from this conversation." + VOICE_END,
    "current_confirm": "the summary for {issue} is still shown." + VOICE_END,
}
VOICE_CREATED_ALONE = " Nothing more."
VOICE_CREATED_THEN = " Nothing more about this ticket. Then, separately:"
VOICE_STATUS = " The reply is spoken: say each date in words (27 October), never as digits."
# Titles are English (classify writes them so); an Arabic reply quoting them can turn mostly Latin and get the English
# voice (D35), so here they are said in Arabic, as the read-back does (VOICE_ARABIC).
VOICE_STATUS_ARABIC = (" This reply is spoken in formal Arabic, so here the titles do not stay as written: say what each "
                       "ticket is about in a few Arabic words of your own, and quote no English title.")
VOICE_FEW_QUESTIONS = " Ask about one or two things only, each as its own short question; keep the rest for your next reply."
