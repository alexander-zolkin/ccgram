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
python3 -m py_compile $(git diff --name-only HEAD~ -- '*.py')   # Py2-except landmine guard
git push origin main
# then on N100:  ~/.ccgram/ccgram-upgrade.sh
```

If a marker block can't be reconciled (upstream rewrote the function), re-derive
the behaviour, keep the marker, and update this file's entry.

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

Verify all present in an install:
```bash
SP=$(find ~/.local/share/uv/tools/ccgram -path '*/site-packages/ccgram/__init__.py' | head -1 | xargs dirname)
grep -rho 'CCGRAM-HOTFIX:[a-z-]*' "$SP" | sort | uniq -c
```
