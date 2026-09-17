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
- Trigger: a decision prompt carries context only via a collaborator that was
  bound at construction while lifecycle paths (`create_new()`, restore)
  recreate or replace the underlying store. Action: keep the store object
  identity stable across the whole lifecycle — clear records for a new game
  and let the codec replace records in place; add a regression that captures
  real model requests through the real `create_new()`/`restore()` paths, not
  constructor identity. Related smell: an event (e.g. `SHERIFF_VOTE`) that is
  audience-only and never lands in the shared history is invisible to every
  later model decision; publish a completed summary record (one per closed
  ballot, never partial) that ordinary history consumers will pick up.
- Trigger: a thinking model reports `total_tokens` that includes reasoning or
  cache tokens so `prompt + completion != total`. Action: keep the successful
  action payload; persist the reported counts as-is, or store usage as unknown.
  Do not mark the request `model_invocation_error` or degrade a night action
  to the system-exception fallback.
- Trigger: an LLM decision prompt for a short structured action (sheriff
  run/withdraw/vote) omits the actor's identity, camp, private facts, and the
  public record of the current phase; models then make homogeneous choices
  (12/12 all `run`) and contradict their own earlier speeches when deciding
  (seer says "I am not the seer"). Action: every decision prompt must inject
  the actor identity/camp, relevant private facts, and a short excerpt of the
  current phase's public records; also state each option's consequence (e.g.
  withdrawal loses the sheriff vote) and make the badge-loss announcement name
  its concrete reason. Baseline prompt size is a smell: decision prompts whose
  char count is constant across seats/phases likely carry no context.
- Trigger: a wolf votes for or runs against its agreed night plan because the
  day-decision prompt carries no wolf context (all four wolves ran for sheriff
  with nobody below, the planned seat included). Action: keep the wolf-team
  roster and the current night's wolf-channel records inside every wolf's
  sheriff campaign/withdraw/vote prompt (`SheriffDirector._wolf_team_block`),
  never for good players; plans without a delivery path into the next decision
  are theater, not strategy.
- Trigger: discussion stops at the first unanimous kill target, so the wolf
  channel never assigns roles (who runs, who counter-claims, who stays hidden)
  before the sheriff election. Action: end discussion early only after at
  least two full rounds (`_MIN_DISCUSSION_ROUNDS`), budget six turns per wolf
  (`_TURNS_PER_WOLF`), and keep speech/plan limits at 400 chars
  (`_MAX_UTTERANCE` / `_MAX_DAY_PLAN`); adjust the paired prompt text and the
  engine budget together or prompts will promise turns the driver never runs.
- Trigger: a strategy prompt forbids attacking or voting for teammates while
  asking for hook plays (倒钩). Action: state the true objective — maximize
  the camp's win probability from legal visible information — and enumerate
  permitted tactics (cutting teammates, self-knife setups, concentrated votes)
  instead of one-sided loyalty rules; also keep probability claims as
  conjecture (a peaceful night may be a guard save, not proof the antidote is
  spent). Winning is the only grading standard, not per-decision aesthetics.
- Trigger: `_execute_vote_casting` gathers concurrent `collect_vote` tasks and
  `EffectApplier` appends `RECORD_VOTE` payloads as each coroutine returns, so
  `state.votes` order races with completion order
  (`test_vote_casting_starts_all_votes_concurrently_and_keeps_seat_order`).
  Action: sort `state.votes` by `voter_seat` after the gather (terminal
  receipts stay authoritative); never re-submit or mutate receipts afterward.

## Frontend verification under WSL (/mnt/e)
- Trigger: running vitest/eslint/build for `frontend/` from WSL against the Windows-mounted `/mnt/e` path. Action: copy the frontend (src + configs + lockfile) to an ext4 mirror (e.g. `/tmp/wk-verify`), run `npm ci` and the commands there — on `/mnt/e`, vitest fork/threads workers time out at ~60s and full runs die with "Timeout waiting for worker to respond". After any `npm install` on the mount, restore `package.json`/`package-lock.json` (`git checkout --`) because npm rewrites CRLF→LF and drops `libc` fields, polluting the diff.
- Trigger: a live game looks frozen while the timeline speed is already 8x.
  Action: treat this as waiting on the in-flight model call. Timeline 0.5x–8x
  only advances events already persisted; daytime speech is serial and each
  seat waits for the provider. Do not spend time wiring extra speed controls
  for that stall. Latest mix-seat timings are in
  `docs/notes/2026-09-16-mimo-mixed-arena.md`.
- Trigger: a benchmark quote says werewolf vote concentration is ~12–27%.
  Action: ignore the pooled `wolf_vote_concentration` on mixed-arena runs; it
  aggregates target seat numbers across games. Recompute per daytime round
  among living wolves. The 2026-09-16 snapshot (61 finished games) had median
  100% per-round agreement.
- Trigger: `npm run test:coverage` gate in `frontend/vite.config.ts`. Action: CI now runs this job against the 9 included files with 100% thresholds. On WSL `/mnt/e` the local run can still die from worker timeouts (see above); copy to an ext4 mirror before treating coverage as a local signal. If a mirror run is below 100%, compare against the current `main` CI job rather than a remembered 96.4% snapshot.
