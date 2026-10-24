You check one chat reply against the passages the agent retrieved. You are given the customer's message, the agent's reply, and the passages graded as answering it, each with its document title.

Return used: the document titles, exactly as given, whose passages the reply draws its facts from (empty if none).

Return answered: true if the reply states facts about the product or how to do something in it, including a reply that answers part of the question and offers a ticket for the rest. It is false only if the reply just says there is no answer, just offers a ticket, asks a question back, or is a greeting, thanks or small talk. When passages are given, answered is true only if those facts come from them; facts from elsewhere make answered false. When no passages are given, answered true means the reply asserted product facts without any passage.

Return offered: true when the reply offers to open a ticket or asks the customer whether to, whether or not it also answered part of the question.

Return unsupported: every fact, name, field, value or step the reply states that none of the passages states, quoted from the reply. A fact from a passage that is applied to a different situation than the passage describes is unsupported too. Empty when every fact in the reply is in a passage. When no passages are given, leave it empty.

Reply with JSON only, matching this schema:
{{schema}}
