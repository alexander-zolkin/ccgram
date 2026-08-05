# Fork hotfixes — `alexander-zolkin/ccgram`

This fork of [`alexei-led/ccgram`](https://github.com/alexei-led/ccgram) carries a
set of behavioural patches for Alexander's N100 Telegram↔Claude-Code bridge.
**Every patch is a real commit** (no runtime `sed`/`patch` — the old
`~/.ccgram/hotfixes/reapply.sh` is now redundant) and every patched block is
tagged in-source with a grep-able marker:

```
CCGRAM-HOTFIX:<name>
```

> **Maintainer: Kara.** This file is the single source of truth for
> *what diverges from upstream and why*. Read it before every upstream merge so
> a refactor upstream doesn't silently drop one of these behaviours.

---

## How this fork is built & deployed

- **Build backend:** `hatchling` + `hatch-vcs` → version comes from git tags
  (`no-local-version`). The fork must keep the `vX.Y.Z` tags pushed or the build
  reports `0.0.0`. Current: `main` sits N commits past `v3.5.2`, so the built
  version reads e.g. `3.5.3.devN`.
- **Install (N100):** `~/.ccgram/ccgram-upgrade.sh` runs
  `uv tool install --force --reinstall "ccgram @ git+ssh://git@github.com/alexander-zolkin/ccgram.git@main"`,
  restarts the `ccgram-daemon` tmux session, and verifies all markers are
  present. Pin a different ref with `CCGRAM_FORK_REF=<tag|branch>`.
  - ⚠️ ccgram lives in uv's **isolated venv (python3.14)** — the system `python3`
    cannot `import ccgram`. Resolve the package dir via
    `find ~/.local/share/uv/tools/ccgram -path '*/site-packages/ccgram/__init__.py'`,
    never `python3 -c "import ccgram"`.

## Syncing upstream (do this, in order)

```bash
cd /home/openclaw/ccgram-fork
git fetch upstream
git merge upstream/main          # resolve conflicts — KEEP every CCGRAM-HOTFIX:* block

# Compile guard — use the interpreter that will actually RUN the code (uv venv,
# 3.14), NOT the system python3 (3.13 here).
VENV_PY=$(find ~/.local/share/uv/tools/ccgram/bin -name 'python3.*' | head -1)
"$VENV_PY" -c "import pathlib
bad=0
for d in ('src','tests'):
    for f in pathlib.Path(d).rglob('*.py'):
        try: compile(f.read_text(), str(f), 'exec')
        except SyntaxError as e: print('FAIL', f, e); bad+=1
print('syntax errors:', bad)"

grep -rho 'CCGRAM-HOTFIX:[a-z0-9-]*' src/ | sort -u | wc -l   # must equal EXPECTED_MARKERS
git push origin main
# then on N100:  ~/.ccgram/ccgram-upgrade.sh
```

> **Do NOT "fix" unparenthesized `except A, B:` on sight.** Upstream's refactor
> tooling de-parenthesizes these, and **PEP 758 made the syntax legal in Python
> 3.14** — which is what the uv venv runs, so it is not a bug there. It *is* a
> `SyntaxError` on ≤3.13 (the N100 system `python3` is 3.13.5), so keep the
> `py3-parenthesize-except` pass as hygiene, but check the interpreter before
> touching the line.

Also eyeball after every merge: `rich-tables` still applied, claude fresh
sessions still get `--effort xhigh`, and `fresh-launch-args` still appends fresh
args in `window_launch_service` — that one died silently in the v4.3.5 merge.

If a marker block can't be reconciled (upstream rewrote the function), re-derive
the behaviour, keep the marker, and update this file's entry.

---

## Fork-only features (not hotfixes)

Whole capabilities this fork adds on top of upstream. They are not marker-tagged
line patches, so a merge won't "drop" them — but upstream refactors can still
break their seams, so they belong on the post-merge checklist:

- **Grok Build provider** — `providers/grok{,_format,_discovery,_models,_private,_status}.py`,
  plus the `supports_model_picker` / `accepts_initial_prompt` capability flags on
  `providers/base.py` and the grok branches in `hooks/adapters.py`, `hook.py`,
  `model_catalog.py`, `doctor_cmd.py`, `toolbar_config.py`,
  `providers/process_detection.py`. Commit `2bcea89`.
- **Remembered model default** — `last_model.py` + the `seed_remembered_model`
  seam in `directory_browser.py`. Commit `b075a58`. (Tagged `model-picker`.)

## Known test failures (pre-existing, NOT merge regressions)

As of `v4.3.12` merge: **17 failures, 6418 passed**. All 17 are caused by this
fork's own behaviour, and reproduce identically on the pre-merge commit:

- **15 × `tests/e2e/*_lifecycle.py`** — `TimeoutError` in the shared helper
  `tests/e2e/_helpers.py:setup_bound_topic → wait_for_send`. Upstream's helper
  expects the old direct-bind `sendMessage`, but `quickstart-defaults` shows the
  "Use default settings?" prompt first, so the predicate never matches and the
  setup times out. Product behaviour is intentional and correct.
- **2 × `tests/ccgram/handlers/polling/test_status_polling.py::TestMaybeDiscoverTranscript`**
  — the tests assume a fixed provider-iteration order/count; adding `grok` to the
  registry makes discovery try two providers instead of one.

Optional cleanup: adapt the e2e helper to drive the quickstart-Yes flow, the way
the other anti-fork tests were adapted.

---

## The hotfixes

Listed by feature. "Commit" is where the marker was introduced on this fork.

### `autoresume` — zero-tap resume of hibernated sessions
- **Files:** `handlers/text/text_handler.py`, `handlers/recovery/recovery_banner.py`
- **Commit:** `b7a3133` `feat(resume): zero-tap auto-resume of hibernated sessions`
- **What:** when a message lands on an unbound topic or a dead tmux window,
  ccgram recreates/rebinds the window and resumes the Claude session
  automatically, instead of waiting for the user to tap a recovery button.
- **Why:** the bridge hibernates idle sessions; without this, every reply after
  hibernation needed a manual "Resume" tap from the phone.

### `sticky-topic-name` / `sticky-bind-name` / `sticky-create-name` — topic title is the user's
- **Files:** `handlers/status/topic_emoji.py` (stores names, keyed by
  `(chat_id, thread_id)`, persisted to survive daemon restarts),
  `handlers/topics/{directory_callbacks,window_callbacks,topic_lifecycle}.py`,
  `handlers/registry.py`, `handlers/recovery/recovery_banner.py`
- **Commit:** `7e3d20e` `feat(topics): stable topic titles and no duplicate topics`
- **What:** ccgram manages **only** the leading status emoji (🟢/🟡); the topic
  *text* is whatever Alexander named it. A new tmux window or a daemon restart
  never re-imposes the window/cwd name onto the title. Only a genuine Telegram
  rename (`FORUM_TOPIC_EDITED`) mutates the stored name.
- **Why:** topics kept reverting to `workspace` (cwd basename pushed by the bind
  path) instead of the title Alexander gave them (e.g. `Test`).

### `freeze-topic-name` — tmux renames don't touch the topic
- **Files:** `thread_router.py`
- **Commit:** `7e3d20e`
- **What:** a tmux window rename no longer auto-renames the Telegram topic.
- **Why:** complements the sticky-name set — the window name and the topic title
  are decoupled.

### `ended-banner-sticky-name` — end/restore banners read the stored title
- **Files:** `handlers/recovery/recovery_banner.py`
- **Commit:** `7e3d20e`
- **What:** "Session … ended" / restore / resume banners render the stored topic
  title, not the drifted window name.
- **Why:** otherwise an ended session banner said `workspace` instead of `Test`.

### `fresh-no-dup-topic` — race-guard against duplicate topics
- **Files:** `handlers/recovery/recovery_banner.py` (`_create_and_bind_window`)
- **Commit:** `7e3d20e`
- **What:** tags the new window as pending-creation before the `await`s that
  yield the loop, so a late `SessionMonitor` poll takes the already-bound branch
  instead of creating a SECOND topic named after the tmux window.
- **Why:** resume occasionally spawned an orphan duplicate topic.

### `no-dup-on-probe-timeout` — rebind, don't recreate, on probe timeout
- **Files:** `handlers/topics/topic_orchestration.py` (`_rebind_existing_topic_by_name`)
- **Commit:** `7e3d20e`
- **What:** when a Telegram topic probe times out, rebind the existing topic
  rather than creating a fresh one.
- **Why:** probe timeouts were misread as "topic gone" → duplicate topic.

### `rich-tables` — Telegram-safe Markdown tables & headers
- **Files:** `rich_tables.py` (**new module**),
  `handlers/messaging_pipeline/message_sender.py`
- **Commit:** `ab1f3e1` `feat(messaging): render Markdown tables/headers as Telegram-safe rich text`
- **What:** converts Markdown tables and ATX (`#`) headers into aligned monospace
  blocks before the message goes through python-telegram-bot's parser.
- **Why:** upstream's parser silently mangles tables (rows collapse) and leaks
  stray `#`; reports from Kara were unreadable on the phone.

### `no-yolo-dice` — drop the 🎲 auto-approve badge
- **Files:** `handlers/status/topic_emoji.py` (`_compose_topic_name`)
- **Commit:** `f920999` `fix(topics): drop the 🎲 yolo badge from topic titles`
- **What:** keep the 🟢/🟡 status emoji but stop appending the yolo dice to the
  title.
- **Why:** Alexander wanted cleaner topic names.

### `claude-stop-permmode` — keep approval mode across Stop
- **Files:** `hooks/adapters.py` (`detect_provider_from_payload`)
- **Commit:** `f9710e8` `fix(hooks): preserve approval mode across Claude Stop events`
- **What:** a bare Stop hook no longer resets the permission mode.
- **Why:** auto-approve was dropping back to interactive at every turn boundary.

### `no-interactive-on-idle-nudge` — idle nudge is informational
- **Files:** `handlers/hook_events.py`
- **Commit:** `bdc21c6` `fix(hooks): don't emit an interactive prompt on idle-nudge notifications`
- **What:** the idle nudge no longer reuses the Notification path that asks the
  user to reply; it's treated as informational only.
- **Why:** the nudge produced a spurious interactive prompt in the topic.

### `skip-synthetic-continue` — drop the `--continue` placeholder round
- **Files:** `synthetic_continue.py` (**new module**),
  `handlers/messaging_pipeline/message_routing.py`,
  `handlers/recovery/recovery_banner.py`
- **Commit:** `c7907da` `fix(relay): drop --continue placeholder round from autoresume topics`
- **What:** the Claude Code harness, when a session is launched with `--continue`
  and no prompt (zero-tap `autoresume`), runs a stock **"Continue from where you
  left off."** turn — a placeholder *user prompt* plus the model's no-op *reply*.
  Both used to surface in the topic as spurious 👤 bubbles. Now:
  1. `message_routing.handle_new_message` drops the placeholder user prompt
     globally (`_is_synthetic_continue`) — display-only, the model already
     processed it.
  2. `synthetic_continue.py` is a one-shot arm/disarm registry keyed by
     **window id** (known at launch, before the transcript read — beats the
     session-id relay race).
  3. `recovery_banner.auto_continue_from_message` arms the freshly-resumed window
     so the model's no-op reply is swallowed too; a real forwarded user turn or
     any tool call disarms it.
- **Why:** the placeholder is pure harness behaviour (not a ccgram string), and
  relaying it + its no-op reply spammed the topic on every wake.
- **Note:** `/continue` is untouched — it never arms a window, so its reply still
  shows. The reply is suppressed via the armed-window registry (not by passing
  `pending_text` as a CLI arg) because `_start_agent_in_pane` *types* the launch
  command via `send_keys literal`; a multiline/quoted Telegram message as an arg
  would break the command (newline = premature Enter).

### `resume-session-collision` — autoresume must not hijack another topic's session
- **Files:** `handlers/recovery/recovery_banner.py` (`auto_continue_from_message`)
- **Commit:** `<this commit>` `fix(resume): don't hijack a live topic's session on autoresume`
- **What:** `claude --continue` resumes the **most-recent session for the cwd**,
  not the specific session the topic previously owned. When several Telegram
  topics are rooted at the same cwd (e.g. multiple topics under
  `…/.openclaw/workspace`), autoresuming a stale topic would grab whatever
  session is newest — often a *different, still-live* topic's session. Result:
  two topics bound to one Claude session, one transcript, and messages typed in
  topic A surfacing in topic B (and two `claude --continue` processes appending
  to the same `.jsonl`). The guard compares the candidate session
  (`scan_sessions_for_cwd(cwd)[0]`) against every **live** window's session id
  (`tmux list_windows` ∩ `thread_router` bindings, excluding the dead window
  being recovered); on a match it logs and returns `False` so the caller falls
  back to the recovery banner instead of silently cross-wiring.
- **Why:** observed 2026-06-20 — a daemon restart left a same-cwd topic's window
  stale; its next message autoresumed onto the *active* session, so a message in
  one topic was delivered to another. Refusing → banner is the safe fallback.
- **Trade-off:** the genuine "resume my own session" case still works (the dead
  old window is excluded). Only a true collision with a *live* other window is
  refused. A deeper fix (resume by the topic's own session id via `--session`)
  is possible later; this guard stops the data-bleed now.

### `quickstart-defaults` — "Use default settings?" one-tap session start
- **Files:** `handlers/callback_data.py`, `handlers/topics/directory_browser.py`,
  `handlers/topics/directory_callbacks.py`, `handlers/text/text_handler.py`
- **Commit:** `<this commit>` `feat(topics): quick-start "Use default settings?" prompt`
- **What:** when a message lands on an **unbound** topic with **no unbound
  windows to adopt** (the create-new-session path), ccgram now shows a yes/no
  *"Use default settings?"* prompt **before** the directory browser.
  - **No** → falls through to the unchanged 4-step wizard (directory → worktree
    → provider → mode).
  - **Yes** → skips all 4 steps and launches immediately with Alexander's
    defaults: cwd `~/.openclaw/workspace`, current branch (no worktree),
    provider `claude`, approval mode `yolo`. Then binds the thread, launches the
    window, and delivers the pending message — via the **same** finalize tail as
    the wizard's mode-select step (`_finalize_session_creation` →
    `_create_window_and_bind`), so bind/launch/delivery and the duplicate-topic
    race-guard are identical.
- **Why:** every new session needed four taps from the phone even though
  Alexander's answer is almost always the same. One tap now covers the common
  case; the full wizard is one tap away for the rest.
- **Scope:** only the create-new-session path. The window-picker (adopt an
  existing unbound window) and already-bound / dead-window paths are untouched.
  A new `STATE_CONFIRMING_DEFAULTS` user-state + a `_check_ui_guards` branch keep
  a typed message (instead of a tap) from racing the prompt.

### `no-false-dead` — don't declare a live window dead on one missed snapshot
- **Files:** `handlers/polling/window_tick/__init__.py`
- **Commit:** `<this commit>` `fix(polling): confirm window death before the "ended" banner`
- **What:** the polling coordinator builds `window_lookup` from a single bulk
  `tmux_manager.list_windows()` snapshot, then `tick_window` treats a binding
  whose `wid` is missing from that snapshot as a dead window and fires the
  proactive **"⚠ Session `…` ended."** recovery banner. That snapshot can
  transiently drop a *live* window (tmux churn right at session start; load when
  many topics are bound). The fix: when the snapshot has no window for the
  binding, re-confirm with a direct per-id `find_window_by_id(window_id)` query
  before notifying. Found → snapshot blip, tick normally (no banner). Still gone
  → genuine death, banner as before.
- **Why:** observed 2026-06-20 — a freshly created session (Test5 / window @110,
  alive, bound, own session intact) got a false "Session ended" banner seconds
  after launch. Amplified by `quickstart-defaults`: every one-tap default roots
  at the *same* `~/.openclaw/workspace`, so many same-cwd windows churn the
  monitor and a dropped snapshot became routine. This guard fixes the false
  positive for **all** paths (wizard + quickstart); the deeper same-cwd
  session-keying issue (resume by session-id, see `resume-session-collision`)
  remains a separate, larger fix.

### `quiet-dead-banner` — no unsolicited "Session … ended." on hibernation
- **Files:** `config.py`, `handlers/polling/window_tick/apply.py`
  (+ `tests/ccgram/handlers/polling/test_status_polling.py`)
- **Commit:** `<this commit>` `fix(polling): stop the unsolicited "Session ended" banner`
- **What:** `_handle_dead_window_notification` (poll loop **and** herdr event
  stream) posted **"⚠ Session `…` ended."** into the topic every time a bound
  window died. New `config.dead_banner_notify` (env `CCGRAM_DEAD_BANNER`,
  **default off**) skips that send. Everything else in the transition is kept:
  dead-notified marking, status-cache eviction, the 💀 topic emoji, and the
  autoclose timer. The send also doubled as a topic-existence probe (a failed
  send triggered the unpin probe → unbind of a deleted topic), so the probe is
  factored into `_probe_topic_after_death` and runs directly on the quiet path.
  Set `CCGRAM_DEAD_BANNER=1` for upstream behaviour.
- **Why:** on this host window death is *routine and intentional* —
  `~/.ccgram/idle-hibernator.py` kills idle windows to free RAM while the topic
  stays bound, and `autoresume` (`CCGRAM_AUTORESUME_DEAD=true`) continues the
  session silently on the next message. With `AUTOCLOSE_DEAD_MINUTES=525600`
  nothing else acts on the banner either, so it was pure notification spam
  (Alexander, 2026-08-05). The *requested* banner — writing into a dead topic
  that autoresume can't recover — is untouched; it still comes from
  `text_handler._handle_dead_window` with the recovery keyboard.

### `resume-own-session` — zero-tap autoresume resumes the topic's OWN session
- **Files:** `handlers/recovery/recovery_banner.py`
  (+ `tests/ccgram/handlers/recovery/test_resume_own_session.py`)
- **Commit:** `<this commit>` `fix(resume): autoresume the topic's own session by id, not cwd-newest`
- **What:** the zero-tap autoresume path (`auto_continue_from_message`) launched
  `claude --continue`, which resumes the **newest session for the cwd** — not the
  topic's own. When several topics share one cwd (every `quickstart-defaults`
  one-tap default roots at `~/.openclaw/workspace`), "newest for cwd" can be a
  *different, still-live* topic's session → two topics on one Claude session →
  cross-topic message bleed. `resume-session-collision` only *detected* this and
  bailed to a banner; this is the deeper fix it deferred. Now: recover the dead
  window's **own** `(session_id, transcript_path)` from `events.jsonl` (its
  `window_states` row is usually pruned on death — same trick `autoresume` uses
  for cwd), confirm the transcript still exists and no live window holds that id,
  then launch `claude --resume <own_sid>`. Pure decision lives in
  `decide_launch_args(...)` (unit-tested); the collision scan is shared via
  `_session_held_by_other_live_window(...)`.
- **Fallbacks (additive, never worse than today):** no recoverable own session /
  missing transcript / malformed id → `--continue` (exactly today's behavior);
  own session genuinely held by another live window → bail to the recovery
  banner. Outer `except → return False` still backstops everything.
- **Scope:** claude only (`provider.capabilities.name == "claude"`) and only the
  zero-tap autoresume path for v1; the manual "Continue" button and other
  providers keep `--continue`. Widen later.
- **Synthetic-continue interaction:** `decide_launch_args` returns `arm_synthetic`
  — True only on the `--continue` branch (the placeholder round is a `--continue`
  behavior). `--resume <id>` is NOT armed (the existing `/resume` + recovery-PICK
  paths never arm it); arming there would swallow the real first reply.
- **Why:** observed 2026-06-20 cross-topic bleed with 8 topics sharing the
  workspace cwd; `quickstart-defaults` makes that the common shape.

### `file-first-unbound` — a photo/file as the first message opens the topic
- **Files:** `handlers/file_handler.py`
  (+ `tests/ccgram/handlers/test_file_handler.py`)
- **Commit:** `<this commit>` `fix(files): open the topic when a file is the first message`
- **What:** sending a **photo or document as the very first message** to a fresh
  unbound topic used to dead-end: `_resolve_upload_dir` returns no `window_id`
  and the handler replied **"❌ No session bound to this topic."** — the wizard
  never showed and the file was dropped. (Text-first already opens the wizard via
  `_handle_unbound_topic`; files just didn't.) Now, when the topic is unbound, the
  file handler downloads the upload to a **session-independent staging dir**
  (`$CCGRAM_DIR/pending-uploads/<thread_id>/`, default `~/.ccgram/...`), builds the
  same "I've uploaded …" notify message but with the file's **absolute** staged
  path, and routes into the **same** `_handle_unbound_topic` flow as text —
  stashing that message as the topic's `PENDING_THREAD_TEXT`. The existing
  pending-text delivery (window-picker / quickstart / directory wizard) then
  forwards it once the user binds a session, and Claude reads the file straight
  from staging via the absolute path.
- **Why this shape:** the upload's normal home is `<cwd>/.ccgram-uploads/`, but the
  cwd isn't known until a session is bound. Using an absolute staged path lets the
  **unchanged** pending-text machinery deliver it — **zero** edits in any of the
  three delivery sites (`window_callbacks._handle_bind`,
  `directory_callbacks._create_window_and_bind`, `recovery_banner`).
- **Scope:** only the **unbound + has-thread** path. A file in the **General**
  topic (`thread_id is None`) keeps the old explicit error (no per-topic session
  to open). Bound topics are completely unchanged. If the topic races into a
  binding between resolve and wizard, the file is delivered directly to the
  now-bound window.

### `fresh-launch-args` — fresh sessions get the provider's launch args again
- **Files:** `handlers/topics/window_launch_service.py`
- **What:** upstream's v4 refactor routed fresh window creation through
  `launch_window` → `create_window(launch_command=...)` without ever calling
  the provider's fresh `make_launch_args()` — silently dropping our default
  `--effort xhigh` for claude sessions. The args are now appended onto
  `launch_command` (covers both the create_window and worktree branches).
- **Why:** the effort default was added 2026-06-26 (Alexander's choice) and
  died unnoticed in the v4.3.5 merge; this restores it and gives the model
  picker its seam.

### `model-picker` — model selection button on the quick-start prompt
- **Files:** `model_catalog.py` (**new module**), `handlers/callback_data.py`,
  `handlers/topics/directory_browser.py`, `handlers/topics/directory_callbacks.py`,
  `handlers/topics/window_launch_service.py`
  (+ `tests/ccgram/test_model_catalog.py`)
- **What:** the quick-start "Use default settings?" prompt gains a
  **🧠 Model…** button. It opens a picker listing models fetched live from
  the Anthropic API (`GET /v1/models`, newest first, capped at 8; auth =
  `ANTHROPIC_API_KEY` env or the Claude Code OAuth token from
  `~/.claude/.credentials.json` + `anthropic-beta: oauth-2025-04-20`; static
  fallback list when unreachable, 1h success cache / 1m failure cache).
  The quick-start prompt shows a **`• Model:`** line: the picked model's
  display name, or the claude CLI default read from `~/.claude/settings.json`
  (`default_model_label()`) when nothing has been picked. Tapping a model in
  the picker **selects** it (stored as `PENDING_MODEL_ID`/`PENDING_MODEL_NAME`
  in `user_data`) and returns to the prompt with the new model shown — it does
  **not** launch. "Yes" then launches with the quick-start defaults plus
  `--model <id>` (`WindowLaunchRequest.model`, claude only). The selection is
  cleared on Yes-launch, on "No" (the wizard picks its own model), and on
  Cancel; `text_handler` clears it and shows the CLI default when a fresh
  prompt opens.
- **Why:** new sessions always started on the CLI default model; Alexander
  wanted a one-tap model choice at session start **and** to see which model
  the default launch will use before tapping Yes. The list comes from the API
  so new models appear without code changes.
- **Security note:** the model id round-trips through Telegram callback data
  and is typed into a shell via `send_keys(literal)` — `_MODEL_ID_RE`
  whitelists `[A-Za-z0-9._:-]` before it reaches the launch command.

### `grok-initial-prompt` — first message goes in as a launch positional
- **Files:** `handlers/topics/window_launch_service.py`
- **What:** for providers that declare `accepts_initial_prompt` (grok), the
  topic's pending first message is `shlex.quote`d onto the launch command
  instead of being typed into the pane afterwards; the keystroke-delivery path
  is then skipped (`initial_prompt_consumed`).
- **Why:** the Grok CLI's welcome screen swallows keystrokes sent right after
  launch, so the first Telegram message vanished and no hooks fired. Claude
  keeps the keystroke path (its multiline/quoted text would break `send_keys
  literal` on a command line — see `skip-synthetic-continue`).

### `private-session` — grok sessions that stay out of the assistant's reach
- **Files:** `handlers/topics/window_launch_service.py`,
  `handlers/topics/directory_callbacks.py` (+ `providers/grok_private.py`)
- **What:** `WindowLaunchRequest.private` (grok only). The quick-start prompt
  gains a private-launch button; a private launch runs in an isolated working
  directory and prefixes the pane command with a redirected `GROK_HOME`
  (`ensure_grok_private_home()`), which grok's hooks inherit.
- **Why:** Alexander's private Grok sessions must never land in a path the
  assistant reads, indexes or distills. Redirecting `GROK_HOME` puts both the
  session files and `chat_history.jsonl` outside every scan path, rather than
  relying on the assistant to avoid them.

### `status-bubble-persist` — the "✓ Ready" bubble survives a restart
- **Files:** `handlers/status/status_bubble.py`
- **What:** the `(user_id, thread_key) -> (message_id, window_id, last_text,
  chat_id)` map is mirrored to `<CCGRAM_DIR>/status_msg_info.json` on every
  mutation and reloaded on startup (`_PersistentStatusMap`).
- **Why:** the map was in-memory only, so every daemon restart (upgrade or
  crash) orphaned the live bubble: the next Stop couldn't find the existing
  message, posted a fresh one and left the old one behind. In quiet forum
  topics these piled up and read as replies to the "topic created" service
  message.

### `transcript-decode-guard` — a bad byte doesn't kill the relay
- **Files:** `transcript_reader.py`
- **What:** transcript reads tolerate non-UTF-8 bytes instead of raising.
- **Why:** one malformed byte in a transcript took down the whole relay for
  that window.

---

## Marker → files quick map

| marker | files | commit |
|---|---|---|
| `autoresume` | text_handler.py, recovery_banner.py | b7a3133 |
| `sticky-topic-name` | topic_emoji.py | 7e3d20e |
| `sticky-bind-name` | topic_emoji.py, directory_callbacks.py, window_callbacks.py, recovery_banner.py | 7e3d20e |
| `sticky-create-name` | registry.py, topic_lifecycle.py | 7e3d20e |
| `freeze-topic-name` | thread_router.py | 7e3d20e |
| `ended-banner-sticky-name` | recovery_banner.py | 7e3d20e |
| `fresh-no-dup-topic` | recovery_banner.py | 7e3d20e |
| `no-dup-on-probe-timeout` | topic_orchestration.py | 7e3d20e |
| `rich-tables` | rich_tables.py (new), message_sender.py | ab1f3e1 |
| `no-yolo-dice` | topic_emoji.py | f920999 |
| `claude-stop-permmode` | adapters.py | f9710e8 |
| `no-interactive-on-idle-nudge` | hook_events.py | bdc21c6 |
| `skip-synthetic-continue` | synthetic_continue.py (new), message_routing.py, recovery_banner.py | c7907da |
| `resume-session-collision` | recovery_banner.py | (see git log) |
| `quickstart-defaults` | callback_data.py, directory_browser.py, directory_callbacks.py, text_handler.py | (see git log) |
| `no-false-dead` | polling/window_tick/__init__.py | (see git log) |
| `quiet-dead-banner` | config.py, polling/window_tick/apply.py | (see git log) |
| `resume-own-session` | recovery/recovery_banner.py | (see git log) |
| `file-first-unbound` | handlers/file_handler.py | (see git log) |
| `fresh-launch-args` | topics/window_launch_service.py | (see git log) |
| `model-picker` | model_catalog.py (new), last_model.py (new), callback_data.py, directory_browser.py, directory_callbacks.py, provider_mode_callbacks.py, text_handler.py, window_launch_service.py | (see git log) |
| `session-topic-name` | multiplexer/tmux.py | (see git log) |
| `resolve-1to1-binding` | window_resolver.py | (see git log) |
| `grok-initial-prompt` | topics/window_launch_service.py | 2bcea89 |
| `private-session` | topics/window_launch_service.py, topics/directory_callbacks.py | 2bcea89 |
| `status-bubble-persist` | status/status_bubble.py | 2bcea89 |
| `transcript-decode-guard` | transcript_reader.py | 2bcea89 |

Verify all present in an install:
```bash
SP=$(find ~/.local/share/uv/tools/ccgram -path '*/site-packages/ccgram/__init__.py' | head -1 | xargs dirname)
grep -rho 'CCGRAM-HOTFIX:[a-z-]*' "$SP" | sort | uniq -c
```
