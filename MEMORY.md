# Project Memory

- Trigger: an OpenAI-compatible reasoning model returns empty action content with
  `finish_reason=length` under the ordinary 768-token response budget.
  Action: keep ordinary generations at 768 tokens, give structured actions a
  separate 2048-token budget, and verify the final JSON with a live probe.
- Trigger: a provider reuses its normal endpoint as the strict-action endpoint
  and repeatedly ignores forced strict tool calls.
  Action: use JSON action transport directly, with a 90-second primary attempt
  and one 120-second retry; keep the HTTP client deadline five seconds above
  the outer retry deadline so local and provider timeouts remain distinguishable.
- Trigger: simultaneous voting can overload a provider or stall a game phase.
  Action: cap voting at five concurrent requests, keep completed votes in seat
  order, and cancel unfinished work at the 500-second phase deadline so two
  five-request batches can each consume their 90+120-second attempt budget.
- Trigger: a full vote prompt times out and must be retried against a slow
  provider. Action: retry with only authoritative identity/private facts,
  legal seats, each speaker's latest current-round public statement, the two
  latest system records, and the existing vote contract; exclude old rounds,
  private thoughts, and wolf-channel logs.
- Trigger: a vote becomes a technical abstention after an invalid model payload
  or timeout. Action: use the compact retry prompt for validation failures too;
  log a stable non-secret failure code, distinguish local deadlines from
  provider timeouts, record semaphore queue wait, and persist completed versus
  missing seats if the whole vote phase expires. Convert every phase-timeout
  missing seat to an explicit technical abstention before resolution, and do
  the same for uncaught vote invocation errors so no partial result is silent.
- Trigger: an action request fails because of a provider connection error, rate
  limit, or server error. Action: retry once through the compact vote path,
  then use an explicit technical abstention and persist the provider failure
  class without storing response or reasoning text.
