# Desktop Helper: design note

Status: **proposal**. Nothing here describes shipped behavior. This is a
product-layer proposal that sits on top of the existing engine; it does not
change the engine's authority model or containment.

Last updated: dev@0d3cc473 | 2026-11-21

## Problem

Odysseus is a self-hosted AI workspace: chat, documents, email, calendar,
search, research, and a browser tool. It is designed for a server, reached from
a browser tab, and administered by someone who understands the approval gate and
the access levels.

A different user exists: a non-technical person on a single-user Linux desktop
who wants help with day-to-day tasks.

- "Help me update my system."
- "Change my wallpaper to something dark and brooding."
- "Help me get my inbox under control."

That user should not run `install-service.sh`, should not see the admin console,
and should not be able to switch the approval gate off. But they should get the
engine's real capabilities, not a toy.

## Goals

- Reuse the engine unchanged: chat, memory, tools, authority, approvals, and
  bwrap containment.
- Add a small, named set of **desktop verbs** that the agent can request.
- Give the non-technical user a plain-language surface with guardrails on by
  default.
- Keep the engine's blast radius in the workspace. Desktop actions execute in
  the helper app, not in the engine.
- Make every desktop verb reversible or explicitly confirmed.

## Non-goals

- No generic "run any command" verb. That would collapse the design back to
  "agent with a shell."
- No silent system updates. Installing packages is "show me," never "auto."
- No admin surfaces in the daily UI: no YOLO override, no MCP server config, no
  token management, no global access policy.
- No second engine and no fork of the engine. This is a new front end, not a new
  brain.
- No claim that the app is a security boundary. It is a UX and capability layer.

## Shape

```text
┌────────────────────────────────────┐
│ Desktop helper app                 │
│  • webview of the engine UI        │
│  • tray icon + autostart           │
│  • first-run setup                 │
│  • local MCP server:               │
│      set_wallpaper                 │
│      check_updates                 │
│      open_software_manager         │
│  • simple-mode navigation          │
└────────────────┬───────────────────┘
                 │ HTTP API + MCP
┌────────────────▼───────────────────┐
│ Odysseus engine (unchanged)        │
│  • chat, memory, tools             │
│  • authority + approval gate       │
│  • bwrap for agent shell           │
└────────────────────────────────────┘
```

The app is a **client** and a **tool provider**. It embeds the engine's web UI
so chat, documents, email, calendar, search, and the browser tool work without a
second implementation. It registers its desktop verbs as a **local MCP server**,
so the engine's existing authority model and approval cards apply to them.

### Why a webview and not a rebuilt UI

The engine's UI is large and changes often. A webview stays in sync for free. A
native rebuild would mean two UIs and permanent drift. For v1, reuse wins.

### Why the verbs live in the app

If the engine executed `gsettings` or `dbus` calls, the engine would need desktop
access — a real widening of its authority. If the app executes them, the engine
only proposes and the app performs. That is a capability-broker model, and it
keeps the engine's authority where it already is.

## Repository strategy

Start in this fork, under `docs/`, next to the engine code the note references.
Move the app to its own repository when it has its first commit.

Reasons to keep the note here for now:

- It references engine internals (`src/tool_capabilities.py`, local MCP
  declarations, access levels) that change with the engine.
- Keeping it adjacent makes drift visible during engine work.
- No code exists yet, so a separate repository would be an empty shell.

Reasons to split later:

- The app has its own release cadence, packaging, and signing.
- The app's issues are product issues, not engine issues.
- The fork's `dev` branch should stay a faithful base for engine work.

If this ever goes upstream, it is a **product proposal**, not a patch. Open a
discussion or issue rather than a pull request.

## Security model

The engine already has two independent locks:

1. **Authority / access level** — may this tool run at all?
2. **Containment / bwrap** — if it runs, where can it reach?

Desktop verbs are not shell commands, so bwrap does not cover them. Their safety
comes from being **few, named, and narrow**, plus per-verb confirmation.

| Boundary | Owner | Notes |
| --- | --- | --- |
| May the agent request this verb? | engine | authority + access level |
| Does the user see a card? | engine | effect classification |
| What does the verb actually do? | app | narrow, auditable implementation |
| Can it be undone? | app | every verb returns an undo handle |

The app must not expose a general command runner. Each verb is a fixed
operation with typed arguments.

### Effect classification for desktop verbs (implemented)

The engine classifies user-configured MCP tools as **unknown** and fails high.
The original escape hatch declared local read and write tools through two env
vars, which map to `READ_PRIVATE` and `WRITE_PRIVATE`. Desktop verbs are
neither: `set_wallpaper` is a local UI change (`UI_SIDE_EFFECT`), not
private-data access.

The implemented replacement is a per-server JSON map in
`mcp_servers.tool_effects`:

```json
{
  "set_wallpaper": ["ui_side_effect"],
  "check_updates": ["ui_side_effect"],
  "install_updates": ["admin_change", "destructive"]
}
```

Resolution rules:

- A declared tool is classified with its declared effects and stays
  `EXTERNAL_UNTRUSTED`, so its output still arms the gate.
- Unknown, malformed, empty, or contradictory declarations fail closed.
- A tool's own `destructiveHint` vetoes a declaration that omits `destructive`.
- The database declaration wins over the env shorthand for the same tool.
- Builtin servers and built-in tool names cannot be declared.
- The admin surface is `PATCH /api/mcp/servers/{id}/tool-effects`; the agent
  cannot edit its own classifications.

### Known gap: natural-language admission

Authority admission requires the trusted request to name the qualified tool
exactly, or to match the notes-family heuristic. A user saying "change my
wallpaper" does not name `mcp__<server>__set_wallpaper`, so the authority would
deny the call even though the classification is correct.

This is the next integration point. Options include trigger phrases in the
declaration, a desktop-intent family in the router, or an explicit "the user
asked for a desktop action" signal from the helper app. It must not be solved by
admitting every declared tool on every request — that would defeat the authority
model.


## Simple mode versus admin

The daily surface shows chat and daily features. Admin surfaces sit behind an
explicit **Advanced** unlock.

| Surface | Simple mode | Advanced |
| --- | --- | --- |
| Chat, docs, email, calendar, search | visible | visible |
| Desktop verbs | visible | visible |
| Per-chat access level | Ask / Auto | Ask / Auto / Full Access |
| Global YOLO override | hidden | visible |
| MCP server configuration | hidden | visible |
| Tokens, endpoints, auth | hidden | visible |

Simple mode is product design, not a security boundary. It exists so the
non-technical user cannot remove the guardrails by accident.

## Verbs

| Verb | Effect | Default | Reversible |
| --- | --- | --- | --- |
| `set_wallpaper` | `ui_side_effect` | auto | yes |
| `check_updates` | read-only | auto | n/a |
| `open_software_manager` | `ui_side_effect` | auto | yes (close window) |
| `install_updates` | `admin_change` + `destructive` | **show me** | no |
| `send_email` | `external_side_effect` | **show me** | no |

The rule: reversible and local runs without a card; irreversible or external
always asks. `install_updates` is the deliberate exception to "auto for local
changes" — a package transaction can break a system and cannot be undone.

## First verb: `set_wallpaper`

The goal is to validate the whole loop — engine proposes, app executes, user
sees and can revert — before adding more verbs.

### Request

```json
{
  "verb": "set_wallpaper",
  "args": {
    "mode": "dark",
    "image": "dark-brooding.jpg",
    "target": "desktop"
  }
}
```

### Result

```json
{
  "ok": true,
  "changed": "org.gnome.desktop.background picture-uri",
  "undo": {
    "verb": "set_wallpaper",
    "args": { "restore": "file:///home/user/Pictures/current.jpg" }
  }
}
```

### Approval card

> Change your desktop wallpaper to a dark image?
> [Change it] [Not now]

After the change, the app keeps an **Undo** affordance for the session.

### Adapters

The verb is implemented per desktop environment, chosen at runtime.

| Desktop | Backend |
| --- | --- |
| GNOME | `gsettings set org.gnome.desktop.background picture-uri` |
| KDE Plasma | `plasma-apply-wallpaperimage` or dbus |
| XFCE | `xfconf-query` |
| Sway | `swaybg` |
| Niri | `niri msg action` |
| Unknown | report "not supported here" rather than fall back to a shell |

The fallback matters: an unknown desktop must fail closed, not shell out.

## Packaging

This is the hard part, not the architecture. A non-technical user needs:

- one installer, no terminal
- auto-update
- a tray icon that starts at login
- the engine installed and kept running for them

The current native install (venv, `systemctl --user`, `install-service.sh`) is
exactly what this user should not have to do. The app must therefore install or
supervise the engine itself, or attach to one that is already running.

Open packaging questions:

- Bundle the engine, or require an existing install?
- Tauri (WebKitGTK) or Electron (bundled Chromium)?
- How to handle distros without systemd?
- How to update the engine without breaking the app's webview?

## Open questions

- Should the app's own confirmation UI replace the engine's approval cards, or
  render them? Two approval surfaces would be confusing.
- Should simple mode be a route/profile in the engine, or a separate app UI?
- Multi-user machines: one helper per user, or per seat?
- How does the helper handle a desktop it does not recognize — refuse, or offer
  a read-only mode?
- Does the helper ship its own model, or attach to the engine's endpoints?

## Next steps

1. ~~Decide the effect-classification gap~~ — implemented as the
   `mcp_servers.tool_effects` map plus the admin route.
2. Solve natural-language admission so "change my wallpaper" can authorize the
   declared verb without naming it literally.
3. Prototype `set_wallpaper` as a local MCP tool with an undo handle.
4. Add a simple-mode route/profile to the engine UI.
5. Wrap the UI in a tray app and wire first-run setup.
6. Revisit `check_updates` and `open_software_manager` once the loop works.
