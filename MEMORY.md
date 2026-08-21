# Project Memory

- Trigger: an OpenAI-compatible reasoning model returns empty action content with
  `finish_reason=length` under the ordinary 768-token response budget.
  Action: keep ordinary generations at 768 tokens, give structured actions a
  separate 2048-token budget, and verify the final JSON with a live probe.
- Trigger: a provider reuses its normal endpoint as the strict-action endpoint
  and repeatedly ignores forced strict tool calls.
  Action: use JSON action transport directly, with a 90-second primary attempt
  and one 90-second retry.
- Trigger: simultaneous voting can overload a provider or stall a game phase.
  Action: cap voting at five concurrent requests, keep completed votes in seat
  order, and cancel unfinished work at the 400-second phase deadline so two
  five-request batches can each consume their 90+90-second attempt budget.
- Trigger: a full vote prompt times out and must be retried against a slow
  provider. Action: retry with only authoritative identity/private facts,
  legal seats, each speaker's latest current-round public statement, the two
  latest system records, and the existing vote contract; exclude old rounds,
  private thoughts, and wolf-channel logs.
