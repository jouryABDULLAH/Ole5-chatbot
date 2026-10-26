"""The task strings: what respond_agent is asked to do this turn. The handlers pick and fill them; prompt.respond puts
the task into the system prompt, with RESPOND_TASK when a turn sets none."""

RESPOND_TASK = ("Answer the customer from the passages. If no passage answers the question, say you have no answer for "
                "this and ask whether they would like a ticket opened.")
TALK_TASK = ("This message needs no search. Reply to what the customer actually said, in one or two short lines. A "
             "greeting gets the persona's greeting and thanks gets its thanks line; anything else gets a plain reply to "
             "it, never a greeting or thanks line. If the customer says they have a problem but not what it is, ask what "
             "it is in one short open question, with no acknowledgement before it: \"يسعدني مساعدتك، ما المشكلة التي "
             "تواجهها؟\" If they say there is no problem or nothing to add, don't ask again: say you're here if they need "
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
               "email. Nothing more.",
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
