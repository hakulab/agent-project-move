# Assistant stores

See [Tested with](../../README.md#tested-with) for checked assistant versions, [Design](design.md) for shared constraints, and the [skill](../../skills/agent-project-move/SKILL.md) for procedures and selection rules.

## Antigravity CLI

The `agy` adapter reads the standalone CLI store in `~/.gemini/antigravity-cli`. Each conversation has a SQLite database at `conversations/<id>.db`. Its primary workspace determines the cwd shown by `scan` and used for export, retirement, and selected moves. Selection includes subagent descendants across workspaces. Forks are independent conversations.

A default move updates matching workspace and Git root URIs across all conversations, including those that reference the moved directory through `--add-dir`. Moves also update the corresponding summaries in `conversation_summaries.db` and the picker cache. The workspace keys in `cache/last_conversations.json` determine what `agy -c` resumes. When two keys map to the same workspace, the destination's existing selection takes precedence. Historical steps and artifact contents retain their original paths.

Export takes a SQLite snapshot of each selected conversation and carries its summaries, continue pointers, files under `brain/<id>/`, and `annotations/<id>.pbtxt` when present. Import checks existing conversation databases for matching schema and content after path mapping. Retirement removes the selected databases, their artifacts and annotations, and their entries in the summaries and caches.

Conversations retain their project IDs across hosts. Manage named project definitions in `~/.gemini/config/projects/<id>.json` separately. To resume an imported conversation by ID, use `agy --conversation <id>` in the destination workspace.

## Claude Code

Root: `$CLAUDE_CONFIG_DIR`, defaulting to `~/.claude`. Project keys in `$CLAUDE_CONFIG_DIR/.claude.json`, or `~/.claude.json` when the variable is unset, help resolve physical project ownership and are only read.

A default move merges `projects/<encoded-cwd>/` directories owned under the source path and rewrites top-level and `<parent>/subagents/agent-*.jsonl` transcripts, including references in directories that stay in place. Subagent `.meta.json` sidecars supply nested `parentAgentId` links and travel with their transcripts. Selected moves relocate only selected transcripts, `.meta.json` sidecars, and same-stem artifact subtrees. See [project merges](../../skills/agent-project-move/SKILL.md#project-merges) for collisions.

Export carries `.meta.json` sidecars, same-stem artifacts, and `file-history/<session>/`. Local moves leave file history in place. Retirement leaves project directories, shared memory, global configuration, and prompt history intact.

## Codex

Root: `$CODEX_HOME`, defaulting to `~/.codex`. The active database is the highest-numbered `state_<N>.sqlite` containing `threads`.

Moves rewrite thread cwds, JSON sandbox paths, and rollouts in `sessions/` and `archived_sessions/`, including `.zst`. Rewritten rollouts lose their `thread_history_<N>.sqlite` projections until resume, after `codex unarchive` for archived threads. Previews remain empty until then.

Selection follows spawn relationships. Selected moves and exports require existing canonical rollout files, and selected moves preserve project metadata and spawn relationships. Retirement removes project metadata only when unreferenced.

Export carries incoming spawn edges and `guardian_review_feedback` records for selected threads when those tables exist. Feedback retains its binary payload. Importing these rows requires the same SQLite text encoding on both hosts (UTF-8 by default). A matching edge or feedback key at the destination requires identical values in every carried column, even if the thread already exists.

Export carries `project_roots` when available. Existing project IDs require identical ordered roots after mapping. Other project metadata and logical `agent_path` identities stay unchanged.

Desktop workspace state in `.codex-global-state.json` stays unchanged and may need a separate path update. Codex may request project trust after a move or import.

## OMP

Root: `$PI_CODING_AGENT_DIR`, defaulting to `~/.omp/agent`. Sessions live in home-relative encoded directories under `sessions/` and `archive/sessions/`.

The header supplies the ID and optional `parentSession` ID or file path. Each journal owns its same-stem artifact directory. Default moves update absolute parent references across journals. Selected moves update only selected journals and relocate their owned paths, preserving unselected parent identities. Imports map known parent files to their destinations and preserve external identities. Existing filenames determine imported artifact destinations.

The global `blobs/` pool holds canonical SHA-256 files for `blob:sha256:<hash>` locators and `<hash>.<extension>` aliases for file viewing. Export carries referenced canonical files and available aliases, validating their hashes. Import rejects differing bytes and maps complete blob path tokens to the local pool. Native locators and prose references retain their spelling.

Retirement leaves the blob pool intact. OMP memory, `history.db`, and `agent.db` stay untouched in every operation. Re-export older bundles containing memory or database state before importing them.
