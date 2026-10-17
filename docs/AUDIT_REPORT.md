# Atlas audit report

Date: 2026-10-13 · Branch: `stage` · Scope: independent audit against `Atlas_PRD.md` and
`design.md`, with fixes. 76 non-merge commits since the audit baseline (`4ca539b`).

## 1. Executive summary

**Before the audit: partially correct.** The pipeline shape was right and 655 tests passed. But
several defects meant the app could not meet its own acceptance criteria:

- Every Google Doc containing a code block, inline code or a nested list would have been rejected
  by the real Docs API (invalid request field names), so M4 could never pass for a programming playlist.
- Single-app mode served `.env`, `token.json` and `credentials.json` to anyone through
  `/..%2f..%2f.env` (path traversal).
- Every LLM error was treated as transient: a bad Gemini key burned all retries, and a Gemini 429
  was shown as "YouTube is rate-limiting". Gemini calls had no timeout, so a hung call held a
  worker slot forever.
- The UI reported false successes: a publish showed "Nothing new" while still running, and
  diagnostics said Drive was "authorized" while publishing failed.

**After the audit: yes for everything that could be verified.** The one requirement not verified against a
real service is **Google Drive publishing**: your stored token is revoked (`invalid_grant`), so it
is verified only against faked Google APIs that check every request against the documented schema.
Re-authorize once (Settings → Authenticate account) and run one real publish to close that gap.

Final gate on `stage` (all run, all green):

| Check | Result |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run pytest -q` | 873 passed |
| `npm run lint`, `npx tsc -b`, `npx prettier --check .` | clean |
| `npx vitest run` | 24 files, 195 tests passed |
| `npm run build` | built |
| `npm run e2e` (Playwright, mocked API) | 3 passed |
| `scripts/test.ps1` (all of the above in one command) | `All checks passed.` exit 0 |

## 2. Requirements traceability (final status)

Status key: **V** = implemented and verified (tests and/or real run); **V-mock** = verified only
against mocked externals; **Partial**; **Open** = a decision is needed (section 6).

| PRD requirement | Implementation | Status | Evidence |
|---|---|---|---|
| FR1 playlist input + URL validation (§20) | `playlists-home.tsx` → `POST /api/playlists` → `ingest.py`, `domain/services/playlist_url.py` | V | Real run: `example.com` → 400 `INVALID_URL`; watch URL without `list=` → 400; nonexistent list → 404 `PLAYLIST_UNAVAILABLE`, nothing stored, shown inline + toast in the browser; e2e test 2 |
| FR2 ingestion, >50 items (pagination), 1-based order | `youtube/playlist.py:fetch_playlist_videos`, `ingest._create_new` | V | `test_pagination_beyond_50_with_placeholders_numbers_densely_from_one`, `test_250_video_playlist_is_stored_completely...`; real 5-video playlist stored 1–5 |
| FR3 transcripts; permanent vs transient | `transcript/ytdlp/provider.py`, `failure_classifier.py` | V | Real yt-dlp HTTP 429 → `rate_limited/transient`, auto-retried 4× then `retries_exhausted`; `test_video_without_any_transcript_fails_fast...`, `test_throttling_disguised_as_unavailable_is_rate_limited` |
| FR4 notes via configured LLM, stored as pending review | `llm/gemini/notes.py`, `llm/factory.py`, `process_video.py` | V | Real Gemini: 4 videos → `pending_review`, exactly 4 calls in the usage ledger |
| FR5 review: view, inline edit, approve, approve-all | `review-screen.tsx`, `playlist-detail.tsx`, `routers/videos.py`, `repo._approve_all_pending` | V | Real run: edit survives re-read (`edited=true`), approve twice is idempotent, approve-all approved 3 = only `pending_review`; `test_approve_all_scope`; `review-screen.test.tsx` (31, includes focus mode, wide column and fullscreen); e2e test 1 |
| FR6 / §19 Drive: folder → index → docs, naming, code boxes | `google/publisher.py`, `drive.py`, `markdown_to_docs.py`, `doc_naming.py` | **V-mock** | `test_first_publish_creates_folder_then_index_then_docs_in_playlist_order`, `test_docs_requests_applied.py` (every request validated against the Docs schema); real Drive blocked by the revoked token |
| FR7 status dashboard (stages, reasons, polling 3s/15s) | `playlist-detail.tsx`, `lib/stage-badge.ts`, `lib/polling.ts` | V | `stage-badge.test.ts` (pinned to design §3.3 and backend enums), `polling.test.ts`, screenshots |
| FR8 retry: auto for transient (backoff), manual for all | `domain/services/backoff.py`, `process_video._handle_error`, `POST /videos/{id}/retry` | V | Real run: 4 auto retries then exhausted, manual Retry → `queued`, `retry_count=0`; `test_forced_429_schedules_a_persisted_backoff_then_succeeds` |
| FR9 incremental re-run (append, no renumbering, removed flagged) | `sync_diff.py`, `ingest._sync_existing` | V | Real resubmit: same playlist id, 0 new Gemini calls; `test_upstream_removal_and_rename_are_flagged_not_applied`, `test_running_the_same_playlist_twice_creates_no_duplicates_or_extra_calls` |
| FR10 delete / full reprocess | `actions.py`, `DELETE /playlists/{id}`, `POST /reprocess` | V | Real delete → playlists/videos/transcripts/summaries all 0 rows; reprocess covered by pipeline tests (not run for real, to save quota) |
| FR11 transcript cleanup, summary kept, audit trail | `repo.soft_delete_*`, `DELETE /videos/{id}/transcript`, `DELETE /playlists/{id}/transcripts` | V | Real run: "Cleared 3 transcripts", summary still present; `test_soft_delete_all_transcripts` |
| §15 crash recovery | `repo.recover_in_flight_videos`, `worker.start` | V | Real `taskkill /F` mid-fetch → restart logged "Crash recovery reset 1 in-flight videos to queued"; Gemini count unchanged |
| §15 skip completed work, no recompute | `process_video._run` | V | Usage ledger across crash + resync: Gemini 4 calls for 4 completed videos |
| §15 idempotent publish; edit-after-publish updates in place | `publish.py`, `_PersistingProgress`, `_create_once` | V-mock | `test_second_publish_creates_zero_duplicates...`, `test_failure_on_doc_3_of_5_then_resume...`, `test_edit_after_publish_republishes_into_the_same_doc_in_place` |
| §15 regenerate reuses transcript | `actions.py` regenerate | V-mock | pipeline tests; `test_mark_published_ignores_a_video_regenerated_mid_publish` |
| §16 API contract, `{code, message, details}` envelope | `routers/*`, `errors.py`, `backend/spa.py` | V | `test_error_envelope.py` (22), real curl of 400/404/409/422 above |
| §17 config in one place, `.env.example`, startup validation | `config/settings.py`, `src/configs/config.yaml` | V | `test_config_validation.py`; bad value → plain problem list, exit 2 |
| §17 secrets never logged / returned | `shared/redaction.py`, `errors.py` | V | `test_redaction.py`, `test_http_errors_never_leak_the_api_key`, `test_a_server_error_message_carries_no_request_url` |
| §18 prompt file versioned; model + prompt version recorded | `src/prompts/notes_v1_1.yaml`, `NotesPromptSource` | V | Real summary recorded `prompt=notes_v1.1 model=gemini-3.5-flash` |
| §18 output validation, truncation, too-large input | `notes_base.validate_notes`, `check_finish_reason`, `check_input_size` | V | `test_gemini_output_cut_off_at_max_tokens_is_rejected`, `TestCheckInputSize` |
| §20 duplicate video in a playlist | `process_video._reuse_first_occurrence` | V | `test_duplicate_reuses_the_first_summary_without_an_llm_call`, `test_duplicate_of_a_permanently_failed_video_is_not_left_queued_forever` |
| §20 private/deleted videos inside a playlist | `fetch_playlist_videos` (skipped) | **Open** (Q1) | `test_private_and_deleted_videos_are_skipped_and_numbering_stays_dense` |
| §20 non-English transcript "used as is" | ytdlp language list `[requested, "en"]` | **Partial** (Q5) | language is recorded (real run `lang=en`) |
| §21 migrations, indexes, WAL | `storage/migrations.py`, `db.py` | V | `test_migrations.py`; WAL confirmed |
| §21 logging: structured, rotating, level from config | `shared/logging_setup.py` | V | `logs/atlas.log`; diagnostics recent errors |
| §10 provider portability (config + one adapter) | `llm/factory.py` | V | `TestBuildNotesGenerator`; documented in `docs/ARCHITECTURE.md` |
| One playlist processed at a time | `worker.py` | **Partial** (Q6) | the worker drains queued videos oldest playlist first; it does not refuse a second playlist |
| Single-app mode (one process, one port) | `backend/__main__.py`, `backend/spa.py`, `scripts/start.*` | V | Real: built UI + API + worker on 8765/8768, `/diagnostics` screenshot |
| Dev mode (reload + Vite proxy) | `scripts/dev.*`, `frontend/vite.config.ts` (`ATLAS_API_URL`) | V | `dev.ps1` on 8766 and `dev.sh` on 8767: `/api/*` through Vite answered by the right backend |
| First-run health page | `GET /api/diagnostics`, `features/diagnostics` | V | `test_diagnostics.py`; real screenshot shows the revoked Drive token as a Warning |

## 3. Bugs found and fixed

Severity: C = critical, H = high, M = medium, L = low. Each fix is its own commit on `stage`.

| # | Sev | Location | Root cause | Fix | Covering test |
|---|---|---|---|---|---|
| 1 | C | `domain/services/markdown_to_docs.py` | Four Docs API fields that don't exist (border padding map, `fontWeight`, text `shading`, `indentLevel`): any Doc with code or nested lists → HTTP 400 | Correct field names; nesting via tabs; bullets applied bottom-up | `tests/test_docs_requests_applied.py` (schema-validated) |
| 2 | C | `backend/main.py` SPA catch-all | Decoded path joined to `dist` unchecked → `/..%2f..%2f.env` served secrets | Resolve inside `dist`; reject `..`/drive letters (`backend/spa.py`) | `tests/test_spa_serving.py` (8 traversal cases) |
| 3 | H | `markdown_to_docs.py` | Indexes counted in Python chars, not UTF-16 → every style after an emoji shifted | `utf16_len` everywhere | `test_docs_requests_applied.py` surrogate cases |
| 4 | H | `markdown_to_docs.py` | `List<String>`, `Map<K,V>` stripped as HTML even in code; `snake_case` lost `_` | Strip only real tags; never touch code spans | converter tests |
| 5 | H | `llm/gemini/notes.py`, `llm/openai/notes.py` | All SDK errors → transient (`rate_limited`/`output_malformed`) | Shared `classify_provider_exception`: bad key/model → permanent config error, 5xx/429 transient with the right provider name | `tests/test_llm_failure_classification.py` |
| 6 | H | `llm/gemini/notes.py` | No request timeout (SDK default: none) → video stuck in `generating_notes` | Timeout, now the setting `notes_request_timeout_seconds` | `test_request_timeout_comes_from_settings_with_a_safe_default` |
| 7 | H | `google/drive.py` | Revoked refresh token → opaque error while status said "authorized"; network blip → "re-run auth" | `invalid_grant` = permanent "Re-authorize Drive"; transport errors transient | `tests/test_drive_auth.py` |
| 8 | H | `shared/redaction.py` | Missed bare `AIza…` keys, `{"access_token": "ya29…"}`, `refresh_token`/`client_secret` | Match key/token shapes anywhere | `tests/test_redaction.py` |
| 9 | H | `frontend/.../use-publish-job.ts` | The 202 reply (counts 0) read as finished → toast "Nothing new" while running (false success) | Follow the playlist publish status | `playlist-detail.test.tsx` "never reports success from the 202 itself" |
| 10 | H | `frontend/src/lib/errors.ts`, `api.ts` | Non-JSON 500 crashed the screen; no error boundary | One error handler + app/route error boundaries | `api.test.ts`, `error-boundary.test.tsx`, e2e test 3 |
| 11 | M | `google/publisher.py` | Lost create response → duplicate Doc/folder; retried first write inserted notes twice | Private tag + lookup before retrying a create; full-body replace | `test_a_lost_create_response_adopts_the_doc...`, `test_a_lost_content_write_response...` |
| 12 | M | `google/publisher.py` | Folder or Doc deleted/trashed in Drive → every later publish 404 (or wrote into the trash) | Detect and re-create | `test_a_folder_deleted_in_drive_is_recreated...`, `test_a_single_doc_deleted_in_drive_is_recreated_on_republish` |
| 13 | M | `google/publisher.py` | Same as #12 for the **index** Doc (found in docs review) | Check index Doc state; re-create with every title | `test_an_index_doc_deleted_in_drive_is_recreated_with_every_title` |
| 14 | M | `storage/playlist_repo.py:_mark_published` | A regenerate during a background publish was stamped `published` (unreviewed notes) | Only update videos still `approved`/`published` | `test_mark_published_ignores_a_video_regenerated_mid_publish` |
| 15 | M | `failure_classifier.py` rule order | yt-dlp "unavailable… try again later" → permanent `video_deleted` | Rate-limit rules first | `test_throttling_disguised_as_unavailable_is_rate_limited` |
| 16 | M | `process_video.py` | Duplicate of a permanently failed video stayed `queued` forever | Fails with "Same video as #N, which failed: …" | `test_duplicate_of_a_permanently_failed_video_is_not_left_queued_forever` |
| 17 | M | `process_video.py` | Retry budget shared across phases; `increment_retry` lacked the run-token guard | Reset after transcript stored; guarded | `test_transcript_retries_do_not_use_up_the_notes_retry_budget` |
| 18 | M | `youtube/playlist.py` | Real bad key arrives as `badRequest` → 502; `userRateLimitExceeded` not a rate limit | Map both correctly | `test_invalid_youtube_key_as_the_live_api_reports_it...` (found in the real run) |
| 19 | M | `youtube/playlist.py` | `str(HttpError)` (with request URL) became the user-facing message | Use the API's reason text | `test_a_server_error_message_carries_no_request_url` |
| 20 | M | `process_video.py`, `failure_classifier.py` | Exhausted transient failure still said "retrying shortly" (found in the real run) | Says "Automatic retries are used up; press Retry to try again." | `test_429_until_exhaustion...` |
| 21 | M | `backend/services/diagnostics.py` | Revoked token looked refreshable on disk → "ok" while publishing failed (found in the real run) | Refresh an expired token to verify it | `test_a_revoked_drive_token_is_not_reported_ok` |
| 22 | M | `google/drive.py`, `errors.py` | User saw raw `RefreshError(...)` text and API paths | Message names the Settings screen; raw text logged | `test_publish_without_drive_consent_is_409_with_a_hint` |
| 23 | M | `routers/drive_auth.py` | Credentials upload: no size limit, no validation, non-atomic | 64 KiB cap, OAuth-client check, atomic write | `tests/test_drive_credentials_upload.py` |
| 24 | M | `config/settings.py` | Unknown/misspelled YAML keys and bad numbers silently ignored; missing prompt found only per video | Startup error with a readable list | `tests/test_config_validation.py` |
| 25 | M | `backend/main.py` `/api/dev/shutdown` | Unauthenticated `os._exit`, skipped the worker's graceful stop | Removed | `test_dev_shutdown_endpoint_is_gone` |
| 26 | M | `.gitignore` | Rotated logs and `.env.local` committable; broad patterns hid source dirs | Anchored patterns | `tests/test_repo_hygiene.py` |
| 27 | M | `scripts/dev.*`, `vite.config.ts` | Vite proxy hard-coded to :8000 → with another port the dev UI talked to the Docker Atlas | `ATLAS_API_URL`, set by `scripts/dev` from config | real run on 8766/8767 |
| 28 | M | `transcript/ytdlp/provider.py` | Transcript files written relative to the working directory, deleted relative to the repo root | Resolve against repo root | `test_a_relative_output_folder_is_under_the_repo_root_not_the_cwd` |
| 29 | M | `frontend/disabled-reason.tsx` | A disabled button's reason was hover-only (keyboard/screen-reader users never got it) | Focusable wrapper while disabled | `disabled-reason.test.tsx` |
| 30 | M | frontend contrast tokens | `--text-muted` on `--surface-muted` 4.34:1; dark accent on accent-soft 4.24:1 | Use `--text` on those surfaces | token contrast tests |
| 31 | L | `worker.py` | A freed slot waited up to 2 s for the idle poll | Wake on completion | `test_a_freed_slot_is_refilled_without_waiting_for_the_idle_poll` |
| 32 | L | `llm/cost.py` | Default model had no price; thinking tokens not counted (usage showed $0) | Price entry; count thinking tokens | usage tests |
| 33 | L | `notes_base.render_prompt` | Title containing `{{Transcript}}` pasted the transcript twice | Single-pass substitution | prompt tests |
| 34 | L | `actions.py` | Retry/regenerate of a video removed upstream queued it forever | 409 with a reason | `test_retry_or_regenerate_of_a_removed_upstream_video_is_refused_not_stranded` |
| 35 | L | `errors.py`, `middleware.py` | Request bodies (notes) logged on 422; every request logged twice; correlation-id log injection | Fixed | `test_error_envelope.py`, `test_correlation_id_is_echoed_only_when_safe` |
| 36 | L | `frontend/playlists-home.tsx` | Re-adding a known link said "Added" | "Already tracked: synced, N new" | `playlists-home.test.tsx` |
| 37 | L | `frontend/google-auth-panel.tsx` | Only a file named exactly `credentials.json` accepted | Any Google client `.json` | `google-auth-panel.test.tsx` |
| 38 | L | `frontend/src/test/setup.ts` | First role query per jsdom file took >5 s under load (flaky timeouts) | Warm-up query in setup | 3 consecutive green full runs |
| 39 | L | `tests/test_worker.py` | Concurrency test depended on scheduler timing (flaky) | Deterministic paired fake | 5 consecutive green runs |

Dead code removed: `storage/cli.py`, `classify_drive_error`, `Stage.is_active`, `Video.has_transcript`,
unused frontend dependencies and `lib/digest.ts`, stale `docs/IMPLEMENTATION_PLAN.md`,
`docs/SYSTEM_ARCHITECTURE.md` and `SETUP_SOP.md`.

## 4. Real services vs mocks

| Service | Real | How / result |
|---|---|---|
| YouTube Data API | **Yes** | 5-video public playlist ingested (positions 1–5); resubmit made no duplicates; nonexistent playlist → `PLAYLIST_UNAVAILABLE`; bad key → config error |
| yt-dlp transcripts | **Yes** | 4 of 5 fetched (`lang=en`); video 1 got real HTTP 429s → 4 automatic retries → exhausted → manual Retry worked |
| Gemini | **Yes** | 4 real generations (+2 earlier smoke calls); bad key → permanent `NotesGeneratorConfigError`, key redacted |
| Google Drive / Docs | **No** | Stored token refresh → `invalid_grant`. All publishing is verified against `StubGoogle`, which validates every Docs request against the documented schema |
| Crash / restart | **Yes** | `taskkill /F` while fetching; restart re-queued it; no repeated LLM calls |
| Mocked only | | Quota exceeded, Gemini 429/5xx/timeout/safety block/truncation, network down mid-run, Drive 429/5xx/403, lost responses, partial publish, deleted folder/Doc, 250-video playlist, >50 pagination, revoked Drive mid-publish |
| macOS / Linux | **No** | `.sh` scripts run via Git Bash on Windows only |

## 5. Design deviations found and fixed (design.md)

| Rule | Was | Now |
|---|---|---|
| §3 tokens defined once, no hard-coded colors | Dark values declared twice; Tailwind default palette usable | One `light-dark()` per token in `tokens.css`; palette locked; no hex/rgb/arbitrary colors in components (grep-verified) |
| §8 contrast 4.5:1 | Two pairs below 4.5:1 | Fixed (bug #30) |
| §8 keyboard / ARIA | Rows, tabs, progress bar not keyboard accessible; disabled reasons hover-only | Fixed (`1c6925c`, bug #29) |
| §4 mono only for code and IDs | `#` column and rail numbers in JetBrains Mono | Inter with tabular figures |
| §4 sentence case | Log levels WARNING/ERROR | Warning / Error |
| §5/§7 40px rows, hover | Diagnostics rows lacked both | Fixed |
| §5 spacing scale only | `pt-8` (Tailwind default) | Spacing token |
| §6.2 failed rows | Retry info / button not always shown | "Retrying in … (attempt n/N)", "Retries exhausted", Retry button |
| §6.4 toasts | Errors could auto-dismiss | Errors persist until dismissed (verified in the browser) |
| Notes rendering | Table cells without padding; links `rel=noreferrer` only, overridable by notes | Padding from tokens; `rel="noopener noreferrer"` enforced; XSS payload tests |

## 6. Remaining risks, limitations and open questions

**Questions for you (safest interpretation kept, nothing changed):**
1. **Private/deleted videos inside a playlist** are skipped at ingest, so they never appear as failed
   videos, and numbering follows the visible videos. PRD §8 lists them as permanent failures. Show them as failed rows instead?
2. **Index Doc contents:** it lists every video's title, including unapproved and failed ones.
   §10/§19 suggest only published titles. Which do you want?
3. **Padding growth (99 → 100 videos):** existing Docs are renamed `01 →` `001` (number unchanged).
   §19 needs this for sorting; §9 says existing Docs are "not renamed". Alternative: a fixed minimum width of 3.
4. **Drive scopes:** `documents` grants access to all your Docs; `drive.file` alone may suffice.
   Needs one real-account check before narrowing.
5. **Non-English videos:** the language list `[requested, "en"]` may pick a machine-translated English
   track instead of the original captions (§20 says "used as is").
6. **One playlist at a time:** the worker drains all queued videos, oldest playlist first. It does not block a second playlist.
7. **Uniqueness:** PRD §21 says UNIQUE (playlist, YouTube ID); §20 needs one row per position. The code uses UNIQUE (playlist, position).
8. **Config location:** PRD §17 says "all settings from env/.env"; you asked for one config file.
   Both work: `config.yaml` plus `ATLAS_<FIELD>` overrides. Update the PRD?
9. **Docker:** nothing needs it any more; files kept (they run uvicorn directly and use an unpinned
   `uv:latest`). Keep or delete?
10. **Reprocess** clears the `removed_upstream` flag, so removed videos are processed again. Intended?

**Known limitations / risks:**
- Drive publishing is unverified against the real API until you re-authorize. OAuth apps in
  "Testing" mode expire refresh tokens after 7 days; publish the app to avoid that.
- `POST /api/drive/auth` holds the request open during consent (5-minute timeout); consent must run
  on the machine that runs Atlas.
- If nothing changed, Publish is a no-op, so a folder deleted in Drive is only re-created on the next real change.
- Links in notes keep only their label in Docs; a numbered list broken by a code block restarts at 1.
- `npm audit`: 0 issues in runtime dependencies. 11 remain in dev-only tooling and need majors (vitest 5, Tailwind 4).
- `light-dark()` CSS requires Chrome 123+, Firefox 120+ or Safari 17.5+.
- The Playwright smoke test failed once on its first run after install and was not reproducible afterwards (6 green runs).
- Icons are mostly 16px where design.md §7 says 20px outside tables (kept: 20px makes the 32px buttons look heavy). Your call.
- `SqlUsageLedger` creates its table outside the migration system.
- GitNexus / graphify indexes are stale relative to these changes; run `node .gitnexus/run.cjs analyze` and `graphify update .`.

## 7. How to run

One-time setup (installs Python deps with `uv`, frontend deps with npm, creates `.env`):

```powershell
.\scripts\setup.ps1          # Windows
```
```bash
./scripts/setup.sh           # macOS / Linux
```

Then put your keys in `.env` (`YOUTUBE_API_KEY`, `GEMINI_API_KEY`), and do the Google consent
once on the Settings screen.

**Single-app mode** (one process, one port; Node is only needed for the build):

```powershell
.\scripts\build.ps1; .\scripts\start.ps1     # Windows → http://127.0.0.1:8000
```
```bash
./scripts/build.sh && ./scripts/start.sh     # macOS / Linux (= uv run python -m backend)
```

**Dev mode** (backend `--reload` + Vite with `/api` proxied to the configured port):

```powershell
.\scripts\dev.ps1                            # Windows → http://127.0.0.1:5173
```
```bash
./scripts/dev.sh                             # macOS / Linux
```

**All checks:** `.\scripts\test.ps1` or `./scripts/test.sh`. If PowerShell blocks scripts:
`powershell -ExecutionPolicy Bypass -File .\scripts\<name>.ps1`. Port 8000 is currently taken on
this machine by the Atlas Docker container; stop it or set `ATLAS_PORT`.

Documentation: `README.md`, `docs/ARCHITECTURE.md`, `docs/CONFIG.md`, `docs/TROUBLESHOOTING.md`.
