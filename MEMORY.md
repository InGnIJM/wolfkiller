# Project Memory

- Trigger: an OpenAI-compatible reasoning model returns prose, malformed JSON,
  or `finish_reason=length` for a short game action. Action: force the action
  tool first, run action transports at temperature 0.1 with a 768-token cap,
  retain strict schema validation, and use one compact JSON retry only when the
  provider explicitly rejects or ignores native tool calling.
- Trigger: a provider reuses its normal endpoint as the strict-action endpoint.
  Action: still attempt the forced tool on that endpoint; if it rejects strict
  tools or returns no native tool call, fall back to JSON with a 90-second
  primary attempt and one 120-second retry. Keep the HTTP client deadline five
  seconds above the outer retry deadline so timeout classes remain distinct.
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
- Trigger: a model vote must be retried, replayed, or finalized at a phase
  deadline. Action: bind `window_id`, voter identity, round and permissions on
  the server; commit `ACCEPT_ACTION` and `RECORD_VOTE` atomically under one
  `action_key`; replay the same semantic digest, reject changed-target reuse,
  and ensure every eligible voter has an accepted, voluntary-abstain, or
  technical-abstain receipt before resolution reads the vote ledger.
