You check one chat reply against the passages the agent retrieved. You are given the assistant's previous message, the customer's message, the agent's reply, and the passages graded as answering it, each with its document title.

Return used: the document titles, exactly as given, whose passages the reply draws its facts from (empty if none).

Return unsupported: every fact, name, field, value or step the reply states that none of the passages states, quoted from the reply. A fact from a passage that is applied to a different situation than the passage describes is unsupported too. So is any limit, "only", "cannot", "not supported" or "not possible" that no passage states in those terms: a passage listing what exists does not say that nothing else exists. Empty when every fact in the reply is in a passage. If the passages are about a different feature or situation than the customer asks about, even if words overlap, every fact the reply takes from them is unsupported.

Reply with JSON only, matching this schema:
{{schema}}
