# Phase 0 spike results (2026-10-04)

| Spike | Result |
|---|---|
| S1 Agent SDK tool lockdown / Node | **Moot.** Owner chose D2b (plain Anthropic SDK, hand-written loop). Only registry tools are ever sent; `assert_tool_surface` aborts startup otherwise (unit test for E11). No Node/CLI in the image. |
| S2 Skills on Linux | **Moot.** Skills are `skills/*/SKILL.md`, read by `prompts.py` and placed in the system prompt; works identically on any OS. |
| S3 Google Calendar MCP | **Not run; Backend B dropped for v1.** Developer Preview + interactive-only documented auth. Revisit in Phase 6. |
| S4 Direct API | **Verified live 2026-10-04** on a test calendar (personal Gmail, Testing mode): the two scopes below suffice (accessRole=owner); insert with a custom uuid4-hex ID works; re-insert hits 409 and is handled as success; patch changes `updated`; delete then get → not found. Event IDs: base32hex chars (`a–v`, `0–9`), length 5–1024 → `uuid4().hex` fits. All writes send `sendUpdates="none"`. Scopes requested: `calendar.events` + `calendar.calendarlist.readonly` (conflicts use `events.list`, so the broader `freebusy` scope isn't needed). Stale check uses the event's `updated` field. |
| Evals | **28/28 real-model evals passed** on 2026-10-04 (E1–E4, E6, E7, E13, E15, E17, E18, E22–E27, E21 starter set incl. two events in one message). Single run; re-run after any prompt/model change. |
| S5 Telegram limits | 4096-char messages (`split_message`), callback data ≤ 64 bytes (`p:ok:<32-hex>` = 37 bytes, unit-tested). Non-owner ignored: unit-tested (E9); **confirm with a second account once live.** |
| S6 Strict schemas | Counted on `tool_input_schema` output and unit-tested against the documented limits (≤24 optional, ≤16 union). **Verified live 2026-10-04:** `claude-sonnet-5-5` accepted all three strict `propose_*` schemas together with `fallbacks: "default"`; prompt caching active (~4.5k tokens cached). Fallback path kept: if a future API change rejects strict schemas, `agent.py` drops `strict` and relies on Pydantic validation. |

## Changes from the spec found during build

- `resolve_people` reference matcher returned *unknown* for multi-word names ("Wei Ling" vs "Wei Ling Tan"). Fixed: every typed word must prefix-match a word of the same student. Single letters no longer match.
- `date_candidates` now normalises shorthand (`tmrw`, `sat`, `thurs`, …) so E21-style input resolves.
- Claude Sonnet 5.5 rejects forced `tool_choice` and binds replayed thinking blocks to an unchanged prefix, so: history is append-only and stored verbatim; commit notes are added as mid-conversation `role: "system"` messages; the system prompt contains nothing volatile.
- `ask_user` ends the loop immediately; the owner's answer is delivered as that tool's `tool_result` on the next turn (no extra API call).
- Refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`) is on by default; `REFUSAL_FALLBACK=false` disables it.
- `BookEvent` gained an optional `recurrence` (RRULE with COUNT/UNTIL required), per the tennis-lessons skill.

## Google OAuth note

Publishing the consent screen to *In production* now requires a homepage URL and a privacy policy URL on the Branding page. For now the app stays in **Testing** (owner added as test user), so the refresh token expires after 7 days: re-run `scripts/google_auth.py` when the bot sends the auth alert. Publishing is a follow-up.
