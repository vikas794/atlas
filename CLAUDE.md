<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **atlas** (2437 symbols, 5162 relationships, 209 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/atlas/context` | Codebase overview, check index freshness |
| `gitnexus://repo/atlas/clusters` | All functional areas |
| `gitnexus://repo/atlas/processes` | All execution flows |
| `gitnexus://repo/atlas/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

# Atlas Product Spec & Design Source of Truth

- **`Atlas_PRD.md`** is the source of truth for *what* to build: scope, functional requirements, data model, pipeline state machine, API contract, failure handling, Drive publishing, and milestone acceptance criteria. Read the relevant sections before implementing any feature, and do not add behavior the PRD lists as out of scope for v1.
- **`design.md`** is the source of truth for *how the UI looks and behaves*: color tokens, typography, spacing, screens, components, status-badge mapping, and accessibility rules. Read it before any frontend work. Follow it instead of making ad hoc visual choices, and keep tokens in the single global stylesheet; never hardcode colors in components.
- For any required implementation, check both files first. Backend/pipeline/API work follows `Atlas_PRD.md`; UI work follows `design.md` and the PRD sections it references (6, 7, 8, 15, 16).
- If the code, the PRD and `design.md` disagree, or a requirement is ambiguous, stop and ask the user. Do not silently pick one, and do not edit either document unless asked.
- Build in PRD milestone order (M1 to M6) unless the user directs otherwise.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

# Documentation Rules

- All project documentation lives in the `docs/` folder (e.g. `docs/ARCHITECTURE.md`, `docs/CONFIG.md`, `docs/TROUBLESHOOTING.md`, `docs/AUDIT_REPORT.md`). New docs go there, not the repo root. `README.md`, `CLAUDE.md`, `AGENTS.md`, `Atlas_PRD.md` and `design.md` stay at the root.
- **Before making any change**, read the docs relevant to it (`docs/`, `Atlas_PRD.md`, `design.md`) and verify they match the current code. If they disagree with the code, flag it to the user before proceeding.
- **After every change**, update the affected docs in the same change so they stay accurate to the code (behavior, config/env vars, API, architecture, troubleshooting). Never leave docs stale. Do not finish a task until the docs are checked.
- `Atlas_PRD.md` and `design.md` are still only edited when the user asks; if a change makes them out of date, tell the user instead of editing them.
