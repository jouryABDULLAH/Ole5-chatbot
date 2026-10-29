"""The task strings: what respond_agent is asked to do this turn. The handlers pick and fill them; prompt.respond puts
the task into the system prompt, with RESPOND_TASK when a turn sets none."""

RESPOND_TASK = ("Answer the customer from the passages. If no passage answers the question, say you have no answer for "
                "this and ask whether they would like a ticket opened.")
TALK_TASK = ("This message needs no search. Reply to what the customer actually said, in one or two short lines. A "
             "greeting gets the persona's greeting and thanks gets its thanks line; anything else gets a plain reply to "
             "it, never a greeting or thanks line. If the customer says they have a problem, or asks for a ticket, but "
             "not what it is about, ask what it is in one short open question, with no acknowledgement before it: "
             "\"يسعدني مساعدتك، ما المشكلة التي تواجهها؟\" or \"يسعدني مساعدتك، ما الموضوع الذي تريد فتح التذكرة "
             "بشأنه؟\" If the message is unintelligible, say you didn't understand and ask them to say it again, in one "
             "line. If they ask for a person or a call, say that technical support is through tickets only, and ask what "
             "the problem is so you can open one. If the message is about something outside the product or service, "
             "do not search, offer a ticket, or repeat the full introduction. Say that the question is outside what you "
             "can help with, then say that you can help with questions about the product, using the product name exactly "
             "as written in the persona. Keep this to one or two sentences. If they say there is "
             "no problem or nothing to add, don't ask again: say you're here if they need anything. If they ask who you "
             "are, how you can help, what you offer, or what services you provide, answer directly from the persona's "
             "capabilities: you answer questions about the product from its documented knowledge and can open a support "
             "ticket when needed. Do this before asking what they need, and if the message also contains a greeting, "
             "greet briefly first. Don't state a "
             "product fact that isn't already in this chat, and don't offer a ticket.")


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
    "accept": "The customer agreed to a ticket for: {issue}. Acknowledge in one line. Then, in one sentence, ask "
              "whether they would like to add anything that would help the support team, and name inside that "
              "sentence at most two things that would help with this particular problem, skipping anything they "
              "already said. They are named as what would help, not asked as questions. No list, no numbering, no "
              "second question. Don't name a screen, template, setting or option unless a passage names it. Nothing "
              "has been created yet; the ticket is created only when they submit the summary. Don't say a ticket is "
              "opened, created or being opened.",
    "declined": "The customer said no to opening a ticket; none was opened. Acknowledge it in one line without saying "
                "a ticket was cancelled or closed.{channel}",
    "urgent_ready": "This is urgent, so the ticket was prepared right away without the usual questions: {issue}. The "
                    "summary is shown with a Submit button. Nothing has been created yet. Say in one line that because "
                    "it is urgent the summary is ready now, and ask whether to submit the ticket for: {issue}. Don't "
                    "repeat the summary.",
    "details": "The customer added details about: {issue}. Acknowledge what they gave in a few words, then ask only "
               "whether there is anything else they'd like to add. One sentence, one question. Don't ask for anything "
               "specific. Nothing has been created yet; the ticket is created only when they submit the summary. "
               "Don't say a ticket is opened, created or being opened.",
    "retry_summary": "The ticket summary could not be prepared this time. Say so in one line and ask them to send their "
                     "last message again. Don't ask for any details. Nothing has been created yet.",
    # Collect asks for the required items one at a time: {item} is the next one not given yet.
    "ask_first": "The customer agreed to a ticket for: {issue}. Say in one line that you need some information from "
                 "them to open the ticket, without saying how many items or listing them. Then on a new line ask for "
                 "this one thing: {item}. One question. Don't explain why. Nothing has been created yet; don't say a "
                 "ticket is opened, created or being opened.",
    "ask_direct": "The customer agreed to a ticket for: {issue}. Say in one sentence that to open the ticket you need "
                  "this one thing, and ask for it: {item}. Nothing else. Nothing has been created yet; don't say a "
                  "ticket is opened.",
    "ask_next": "Ask for this one thing: {item}. One short sentence, one question, no explanation, at most a few words "
                "of acknowledgement first.",
    "confirm": "The ticket summary is ready and shown with a Submit button. Nothing has been created yet; the ticket is "
               "created only when they submit. Say the summary is ready in one line and ask whether to submit the "
               "ticket for: {issue}. "
               "Don't say the ticket is created, opened or being opened. Don't repeat the summary.",
    "changed": "The ticket summary was updated as the customer asked and is shown again. Say what changed in one line "
               "and ask whether to submit the ticket for: {issue}.",
    "created": "The ticket {number} was created on {created_at}. Tell the customer its number and the date and time it "
               "was created, and that the support team will reply by email{hours}. Nothing more about this ticket.",
    "cancelled": "The customer cancelled the ticket. Acknowledge it in one line.{channel}",
    "status": "The customer asked about their tickets. Today: {today}. These are the tickets on their account, newest "
              "first, from the ticket system, one per line as number, title, date, state:\n{tickets}\nAnswer what "
              "they asked first, in one line, in sentences, not as a list copied from above. Give the number, title "
              "and date only when the customer hasn't seen them in this chat already, or asked for them. If you "
              "already gave this ticket's details in this chat, don't repeat them. How tickets are handled, for "
              "questions about the process:\n{process}\nAnswer process questions from this block only. Say a state's "
              "meaning, and its name once if it helps. Titles stay as written; your own words follow the customer's language. A state of unknown means the system didn't "
              "return one: say the state isn't available right now, never the word unknown. If the customer asked "
              "about one ticket (the latest, a number, a title, a date), give that one only. If they didn't say which, "
              "give the 5 most recent, one line each, and say how many more there are. If they ask about a ticket not "
              "in the list, say you have no record of it here. State nothing that isn't in the list or the process "
              "block: no estimates, nothing about what the team is doing.",
    "status_none": "The customer asked about their tickets. There are no tickets on their account from this chat. Say "
                   "so in one line, and that they can describe a problem here to get help or open one.{channel} Don't "
                   "ask whether to open a ticket.",
}

# The product's support hours, channel and reply channel (chat.yaml), filled into the tasks above; left out when a
# product has none.
HOURS = {"created": " during the support hours: {}",
         "status": "- Support hours, when the team replies: {}"}
CHANNEL = " Say how support works: {}."
REPLY_CHANNEL = "- Where the reply arrives: {}"

# What each ticket state means for the customer: values.xlsx says only open or closed, so the gloss is written here.
STATE_MEANINGS = {
    "new": "received, not yet picked up by the team",
    "open": "picked up by the team",
    "waiting for customer response": "the team is waiting for a reply from the customer",
    "waiting for concerned department": "waiting for another department",
    "waiting for service provider": "waiting for the service provider",
    "pending reminder": "on hold until a set date",
    "pending auto close+": "will close on its own soon unless the customer replies",
    "closed successful": "resolved and closed",
    "merged": "joined with another ticket and closed there",
}
URGENT = "This sounds urgent. "
# Appended to queued when the new issue is urgent.
# How a reply goes back to the current ticket, by phase (the queued task, and chat in collect or confirm).
CURRENT = {
    "offer_ticket": "ask whether they would like a ticket for: {issue}.",
    "collect": "ask again, in one sentence, for the one piece of information you last asked for about: {issue}.",
    "confirm": "the summary for {issue} is still shown; ask whether to submit it.",
}
QUEUED_URGENT = " Because the new issue is urgent, also say they can cancel the current one to start it now."
