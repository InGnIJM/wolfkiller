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
- Trigger: a claim that "provider X now requires header Y" arrives from web
  search. Action: read the client's own shipped code instead of the search
  results. For OpenCode, the npm package `opencode-ai` ships a compiled Bun
  binary (`bin/opencode.exe`); `grep -ao 'x-opencode-[a-z-]*'` plus a Python
  byte-offset dump of the surrounding text recovers the real implementation.
  Verified for v1.18.31: the header block is sent only when
  `providerID.startsWith("opencode")`, i.e. for `opencode` (Zen,
  `https://opencode.ai/zen/v1`) and `opencode-go` (Go,
  `https://opencode.ai/zen/go/v1`) — both `@ai-sdk/openai-compatible`. Values
  are `x-opencode-session` = the client's own session id (`ses_` + 26 chars),
  `x-opencode-request` = the OpenCode user id, `x-opencode-client` = client
  flag, `x-opencode-project` = project id. For *third-party* providers OpenCode
  sends `x-session-affinity` + `X-Session-Id` instead. The server-side
  enforcement of these headers is still unverified — only the client exists in
  hand, so treat "required" as unproven until a real endpoint returns 4xx.
- Trigger: running backend tests on this machine. Action: the repo has no
  virtualenv and system Python has no deps; `python3 -m venv` also fails here
  ("Failing command: .../bin/python3", missing ensurepip). Use
  `python3 -m pip install --target ~/.venvs/wk-deps -r requirements.txt` and run
  `PYTHONPATH=~/.venvs/wk-deps python3 -m pytest tests app --cov=app --cov-branch`.
- Trigger: a provider returns HTTP 500 on `/chat/completions` for one specific
  model while other models on the same host work. Action: check whether that
  model is served on a *different endpoint* before suspecting the request body,
  headers or key. OpenCode Zen is the worked example: the provider-level catalog
  entry is `@ai-sdk/openai-compatible` at `https://opencode.ai/zen/v1`, but
  individual models override it with `provider:{npm:"@ai-sdk/openai"}`
  (`muse-spark-1.3-contributor-free` does), and the client then dispatches with
  `if (api.npm === "@ai-sdk/openai") return client.responses(id)` — i.e. the
  **OpenAI Responses API** at `{base}/responses`. The same client also clears
  `strict` on every tool for that dialect (`strict:!1`), so strict tools must be
  off there. Recover the per-model override by finding the model id in
  `bin/opencode.exe` and dumping the bytes around it; the `provider:{npm:...}`
  key sits inside the model's catalog object, not in the provider block.
  Implementation notes: the OpenAI SDK appends `/responses` itself, so a Base
  URL copied from a model table must be stripped first or it posts to
  `/responses/responses`; LangChain normalises Responses output blocks to
  `{"type":"text","text":...}`, so an existing `_content_text()` that reads
  `type == "text"` needs no change; `finish_reason` is absent in that dialect
  (LangChain reports `status` instead), which is harmless only as long as
  nothing branches on it.
- Trigger: OpenCode Zen returns HTTP 500 on `/chat/completions` for one model.
  Action: the gateway's own `/zen/v1/models` (GET, plain OpenAI-compatible)
  lists the authoritative catalog — use it instead of guessing model ids from
  the client binary, whose catalog spans *every* provider (247 free models
  across all of them, not the 74 Zen serves). Zen's error types are
  `AuthError` (401, bad key), `ModelError` (401, id not served),
  `RegionError` (**403**, "not available in your country"); note the gateway
  reports `ModelError` with status 401, so classify on the JSON `error.type`
  and not on the status code. A 403 maps to `openai.PermissionDeniedError` in
  the SDK, so a bare `PermissionDeniedError` in a log means RegionError, not a
  key problem. Verified 2026-09-21 from a China Mobile (Nanjing, CN) egress:
  **both `muse-spark-1.3-contributor-free` and `muse-spark-1.2-contributor-free`
  are region-blocked** — the gate runs *before* auth, so even a request with no
  `Authorization` header gets 403 — while `jev-1.13-free`,
  `deepseek-v4-flash-free`, `mimo-v2.5-free`, `ling-3.0-flash-fin-free`,
  `nemotron-3-ultra-free` and `nemotron-3.5-lightning-free` return `AuthError`
  and are therefore usable. Those six also route on `/chat/completions`, so
  they need no Responses dialect. Do not burst-probe the gateway: a rapid
  concurrent batch trips a Cloudflare client-signature rule that answers
  `error code: 1010` for *every* model, which looks exactly like a blanket
  region block and will send the diagnosis the wrong way. Probe sequentially
  with ~2.5s spacing; the rule clears by itself.
- Trigger: adding a role to the backend registry. Action: the frontend keeps
  **four** independent role-id → Chinese-name tables and they drift silently —
  `components/game/SeatMap.tsx` (`ROLE_BADGES`), `components/game/DeathAnnouncement.tsx`
  (`ROLE_LABELS`), `components/game/HistoryPanel.tsx` (`ROLE_LABELS`) and
  `components/shared/RoleIcon.tsx` (`ROLE_ICONS`); `theme/tokens.ts` (`ROLE_COLORS`)
  is a fifth one, but it only supplies colours. A missing key never throws:
  SeatMap renders `{badge && ...}`, so the seat card silently loses its role line
  *and* falls back to the camp colour, while the hover card drops the role from
  its identity line; DeathAnnouncement falls back to the raw id
  (`身份：wolf-killer-wolf-beauty`). Seen 2026-09-26 on game `aea935c4`: seats 2
  (wolf beauty) and 7 (old drunkard) showed no name while the other ten did —
  the knight was missing from the table too but was not in that line-up, which
  is why the symptom looked like "two players". The `it.each` role tables in
  `test/SeatMap.test.tsx` and `test/DeathAnnouncement.test.tsx` are the only
  guard: they fail whenever a table is short. The same drift hits death causes:
  the `DeathCause` union in `store/types.ts` plus four `CAUSE_LABELS` tables
  (`DeathAnnouncement.tsx`, `ActivityCard.tsx`, `CenterDisplay.tsx`,
  `HistoryPanel.tsx`). Only DeathAnnouncement's lagged behind `knight_duel` and
  `charm`, and it degrades to the raw cause (`charm · 第2轮`) rather than
  failing. Typing the test table as `Array<[DeathCause, string]>` makes a
  missing cause break `tsc` as well as the render.
- Trigger: one death cause shared by two players whose last-words rules differ
  (the knight's duel — victim speaks, knight does not). Action:
  `is_last_words_eligible` is a pure function of `(cause, round)` and cannot
  tell them apart, and `_execute_last_words()` walks the **whole**
  `death_history` with no round filter — so just adding `knight_duel` to the
  eligible set makes the knight speak on the *next* day, and both gates read the
  same function so neither can catch it. Pass an explicit `daytime` flag
  instead: `_resolve_day_interruption()` calls
  `give_last_words(..., daytime=True)` while the night path never does, and
  `BaseRole._validate_last_words` derives the same flag from `state.phase`
  (`_DAYTIME_PHASES`), which keeps `speak()`'s own gate in agreement without a
  new public cause. The rule text decides it: the requirement document
  (`docs/notes/2026-09-26-eval-prompt-DELIVER.md:52-57`) writes 「且无遗言」for
  the knight only and leaves the victim unqualified, so the victim keeps the
  official "every daytime death gets last words" rule. Guard the flag with a
  test on `_validate_last_words` directly: the engine-level tests inject
  `_mock_role` and never reach it, so 100% line coverage does not prove the
  phase branch was actually asserted.
- Trigger: an engine step that mutates in-memory dedupe state and *then* makes a
  slow LLM call. Action: checkpoint the claim between the two —
  `give_last_words` now writes `last_words_pending:{round}:{seat}:{cause}`
  right after `_last_words_given.add()` and before `speak()`, so a crash
  mid-call loses the line on resume instead of replaying it into the timeline
  (the better failure for an audience reading events in order). The label
  deliberately does **not** start with `last_words:`, so
  `GameService._checkpoint_domain_events` falls through to the generic
  `STEP_COMMITTED` branch with empty visibility rather than re-emitting a
  `SPEECH_MADE` for words that were never spoken — keep it that way when adding
  checkpoints that only persist bookkeeping.
- Trigger: an audience event that renders in `ActivityCard` / `CenterDisplay` /
  `HistoryPanel` but never shows up on the seat map (sheriff ballots were the
  case: `SHERIFF_VOTE` had three render branches and zero seat badges).
  Action: the seat map derives its own `voteTargets` in `GameBoard` behind
  **two** gates — a phase gate and an event-type gate — so a new vote kind has
  to pass both. The election window is `phase === 'sheriff_election'`, a
  *synthesized* phase (the audience stream carries no election phase event), and
  sheriff first-round (`kind='vote'`) and PK (`kind='pk'`) ballots share one
  `round_number`, so split them by `kind` and let the PK bucket replace the
  first round wholesale instead of filtering by round number.
- Trigger: a night audience event renders nowhere on the live stage while its
  neighbours do (night wolf ballots were the case: `wolf_chat_message` had a
  card in `ActivityCard` and a chip in `CenterDisplay`, `wolf_vote` had neither
  — its `case` was dropped in the commit that moved the detail card out of
  `CenterDisplay`, and only `HistoryPanel` kept rendering it). Action: when a
  render surface is refactored, diff the **event-type switch**, not the file
  list — a card that moves house keeps compiling while its `case` disappears.
  Cover every new branch with a component test: neither `ActivityCard.tsx` nor
  `CenterDisplay.tsx` is inside the `vite.config.ts` coverage gate, so nothing
  else catches a missing `case`.
- Trigger: gating night seat-map ballots on `phase === 'night'`, or on the
  `phase: night` event's round number. Action: don't — the audience stream
  carries **no** `phase: night` for the first night (the store's derived phase
  stays `waiting`, because `deriveState` only reads `phase` events), and the
  `phase: night` event that opens night N+1 carries round number **N** while
  that night's own events (`night_thought` / `night_action` /
  `wolf_chat_message` / `wolf_vote`) carry **N+1**. Gate on the current timeline
  entry being a night event instead, and take the ballots whose `round_number`
  equals that entry's: `phase`-based gating misses the entire first night, and
  the phase round number draws the previous night's ballots for the opening
  frame of every later night.
- Trigger: a backend test passes locally but fails in CI on an attribute of a
  hand-rolled engine fake — `AttributeError: 'types.SimpleNamespace' object has
  no attribute ...` (the night announce hook was the case:
  `test_provider_persists_sanitized_model_error_in_game_log` broke when
  `b079699` added `engine.announce_actor_blocking()` for `_NIGHT_ACTOR_POINTS`;
  the two tests written alongside the feature got the hook, the older fake did
  not). Action: `GameService._command_provider` reaches the engine through a
  duck-typed seam, so every hand-built fake has to grow with it — when adding an
  `engine.<method>()` call, grep the tests for `SimpleNamespace(` and the
  `_engines[` assignments and give each fake the new hook. A fake assembled from
  `SimpleNamespace` compiles and passes on every path that never touches the new
  method, so only the one test that reaches it goes red.
- Trigger: local `pytest tests app -q` collects more tests than the CI job for
  the same commit (3453 vs 3439), or a file that exists only locally keeps
  failing or flaking. Action: don't suspect a stale checkout — read
  `.git/info/exclude` first (a **local-only** exclude list, invisible to
  `.gitignore` and to every other machine) together with `.gitignore`.
  `backend/tests/test_purge_old_games.py` (14 tests, covering the ignored
  `backend/scripts/purge_old_games.py`) is excluded that way, so CI never runs
  it: its `test_purge_apply_deletes_and_backs_up` is order-dependent locally and
  its failures there are local noise, not a regression.

## 观众流是上帝视角，对玩家保密在另外三层
- Trigger: a domain event or an audience whitelist drops a payload field "to keep
  it secret" (the wolf-beauty charm shipped without `target_seat` for exactly
  that reason, and the frontend card printed 目标保密). Action: first pin down
  **who** the secret is kept from. There is no player UI — the timeline is the
  god view (`AudienceProjector.snapshot` hands out every seat's `role`/`camp`,
  and `night_thought` already publishes the charm target via
  `WOLF_BEAUTY_REASONING`), so trimming an audience field buys nothing and only
  splits the story: the charm card hid the seat while the neighbouring thought
  entry printed 目标2号. Player-facing secrecy lives elsewhere and must stay
  there: `ContextProjector._public_facts` exposes only speeches/votes/role
  rules, the `RELATION` namespace is a deliberate no-op (`charmed_by` reaches no
  context), and `ConversationLog.visible_to` filters per seat. Fixing the charm
  meant only the emitter payload plus the `_EVENTS` whitelist; the player side
  needed no change.
- Trigger: adding a field to a published event to fix a god-view gap. Action:
  the new field is optional in the frontend type (`target_seat?: number | null`)
  and every render surface needs both branches — the already-recorded archives
  keep the old payload forever, so they must render "目标未记录" instead of
  crashing or silently implying secrecy. `HistoryPanel.tsx` is inside the
  coverage gate, so its fallback branch needs its own test.
