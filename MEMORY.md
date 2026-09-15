# Project Memory

- Trigger: an OpenAI-compatible reasoning model returns prose, malformed JSON,
  or `finish_reason=length` for a short game action. Action: force the action
  tool only when the resolved provider profile declares `strict_tools`;
  otherwise use JSON directly. Run action transports at temperature 0.1, take
  the action token budget from configuration (default 2048) without a global
  768-token cap, retain strict schema validation, and use one compact JSON retry
  only when native tool calling was attempted and rejected or ignored.
- Trigger: a provider reuses its normal endpoint as the strict-action endpoint.
  Action: attempt the forced tool on that endpoint only when the resolved
  provider profile declares `strict_tools`; otherwise use JSON directly. If an
  attempted strict tool is rejected or returns no native tool call, fall back
  to JSON with a 90-second primary attempt and one 120-second retry. Keep the
  HTTP client deadline five seconds above the outer retry deadline so timeout
  classes remain distinct.
- Trigger: simultaneous voting can overload a provider or stall a game phase.
  Action: cap voting at five concurrent requests, keep terminal receipts in
  seat order, and cancel unfinished work at the 500-second phase deadline so
  two five-request batches can each consume their 90+120-second attempt budget.
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
- Trigger: the phase deadline cancels a vote coroutine after its receipt was
  committed but before the coroutine returned. Action: determine completed and
  missing voters from the authoritative receipt ledger, never from task-local
  completion bookkeeping, before creating phase-timeout abstentions.
- Trigger: a strict tool endpoint returns HTTP 400 or 422 with non-standard or
  opaque compatibility wording. Action: treat that client rejection as a
  one-time JSON fallback signal; keep authentication, rate-limit, network, and
  server failures in their original error classes.

## Frontend verification under WSL (/mnt/e)
- Trigger: running vitest/eslint/build for `frontend/` from WSL against the Windows-mounted `/mnt/e` path. Action: copy the frontend (src + configs + lockfile) to an ext4 mirror (e.g. `/tmp/wk-verify`), run `npm ci` and the commands there — on `/mnt/e`, vitest fork/threads workers time out at ~60s and full runs die with "Timeout waiting for worker to respond". After any `npm install` on the mount, restore `package.json`/`package-lock.json` (`git checkout --`) because npm rewrites CRLF→LF and drops `libc` fields, polluting the diff.
- Trigger: `npm run test:coverage` gate in `frontend/vite.config.ts`. Action: CI now runs this job against the 9 included files with 100% thresholds. On WSL `/mnt/e` the local run can still die from worker timeouts (see above); copy to an ext4 mirror before treating coverage as a local signal. If a mirror run is below 100%, compare against the current `main` CI job rather than a remembered 96.4% snapshot.
