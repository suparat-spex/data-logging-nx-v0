# config.ini

Defaults that a person or an installer sets by hand. Anything changed inside the app
(theme, broker form, window layout) is remembered separately and takes priority.

| Running from | File |
|---|---|
| source (`python -m nxlogger`) | `config.ini` in the repo root (git-ignored) |
| installed EXE | `%APPDATA%\NX Logger\config.ini` |

The file is created from `src/nxlogger/resources/config.default.ini` on first run.
Help → About shows which file was loaded. Comments start with `;` (after a space when on
the same line as a value).

## [app]

| Key | Values | Default |
|---|---|---|
| `theme` | `dark`, `light`, `system` | `dark` |

## [mqtt]

Defaults for the Connection dialog.

| Key | Meaning | Default |
|---|---|---|
| `host` | broker host name | `broker.emqx.io` |
| `port` | broker port | `1883` |
| `transport` | `TCP`, `TLS/SSL`, `WebSocket`, `Secure WebSocket` | `TCP` |
| `ws_path` | WebSocket path | `/mqtt` |
| `username`, `password` | broker login; the password is plain text - leave it empty on shared PCs | empty |
| `client_id` | empty = random per connection | empty |
| `keepalive` | seconds | `30` |

## [paths]

| Key | Meaning | Default |
|---|---|---|
| `recordings` | default folder for new projects' recordings | `Documents\NX Logger\Recordings` |
| `projects` | where Open / Save As start | `Documents\NX Logger\Projects` |

Relative paths are inside `Documents\NX Logger`; `%VARS%` and `~` are expanded.
