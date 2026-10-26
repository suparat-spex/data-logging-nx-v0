# Settings pop-up styles: tabs and sections

Device and tag settings (Connectivity and Tags pages) open in a **settings pop-up**. Two
layouts exist and both stay in the code; one constant picks the default:

```python
# src/nxlogger/ui/dialogs/common.py
POPUP_STYLE = "tabs"        # "tabs" | "sections"
```

Every dialog can also be given one explicitly: `DeviceDialog(..., style="sections")`,
`popup_form(..., style="sections")`.

## The shared frame (both styles)

```
+--------------------------------------------------------------------+
| [icon]  Title (item name / "New ... ")                 dlgHeader   |
|         one muted line: what it is (connector, device · group)     |
+--------------------------------------------------------------------+
|                         body (see below)               dlgBody     |
+--------------------------------------------------------------------+
| summary / validation message            [Cancel] [Verb]  dlgFooter |
+--------------------------------------------------------------------+
```

- Header: `common.dialog_header(icon, title, subtitle)` - 32 px icon in the accent colour, the
  title follows the Name field as you type.
- Footer: the form's message line(s) on the left, `[Cancel] [Verb]` on the right. The verb names
  the action: *Add device*, *Add tag*, *Add group*, *Save* - never a bare OK.
- One label column across the whole form (every section / tab gets the widest label's width).
- Nothing is saved before the verb; Cancel discards. While recording: the pop-up opens to look,
  the verb is disabled and the footer says why.
- QSS object names: `dlgHeader`, `dlgBody`, `dlgFooter`, `pageSection` (section title), all
  token colours (`app_style.py`), right in dark and light.

## Style "sections" - one page, titled groups

```
| General ------------------------------------------------ |  <- pageSection: bold title, line under
|    Name          [..........................]            |
|    Description   [..........................]            |
| Network ------------------------------------------------ |
|    Host          [..........]                            |
| Connection test ---------------------------------------- |
|    [Test connection]  result text                        |
```

- Every section (a dialog's tab page, or a connector `SECTIONS` entry) is a titled block, one
  under the other, in a scroll area; the dialog is sized to the content (at most 85 % of the
  screen height).
- Best for **short forms** (up to ~12 fields) and when the user should see everything at once
  (review before Save). Group pop-up always uses it (one page).
- Built by `device_pages/base.section(v, title)` (device) and `popup_form(style="sections")`
  (any tabbed QDialog: its tabs become sections).

## Style "tabs" - one tab per section (default)

```
| [General] [Broker] [Authentication] [Session] [Payload]  |
|    Address       [broker.emqx.io            ]            |
|    Port          [1883]                                  |
|    Transport     [TCP           v]                       |
| Connection test ---------------------------------------- |  <- under the tabs: visible on every tab
|    [Test connection]  result text                        |
```

- Device: tab *General* (name, description, scan interval, enabled) + one tab per connector
  section (`ConnectionPage(tabs=True)` puts each `SECTIONS` entry on `self.pages`). Things that
  belong to the whole item (Connection test) stay **under** the tabs, not in one of them.
- Tag: the form's own tabs (General, Address, Scaling, Display) via `popup_form`.
- A form with a single page falls back to "sections" (a lone tab looks broken).
- Best for **long forms** and connectors with many sections (MQTT, OPC UA).

## Layout rules learned the hard way (keep them in both styles)

- The body follows the dialog width: `body.setSizePolicy(QSizePolicy.Policy.Ignored, Preferred)`
  inside the scroll area - otherwise one long check box / placeholder text pushes the fields
  under the scroll bar or off the right edge.
- Width: `min(max(width, body.sizeHint().width() + 40), 720)` (popup_form); device 682 px.
- Form rows on one line: `QFormLayout.setRowWrapPolicy(DontWrapRows)`; read-only info labels in
  the field column get `setWordWrap(True)` and a `Maximum` vertical size policy (no tall rows
  when a tab is shorter than the tallest one).
- Tab pages: 12 px margins, a stretch / top alignment under the form.
- Check screenshots in both themes (offscreen_test.py or a small grab script) - text renders as
  boxes offscreen, so check layout, clipping and alignment.

## Switching / reusing

- Whole app back to one-page dialogs: `POPUP_STYLE = "sections"` (tests cover both:
  `test_device_dialog_styles`).
- New settings pop-up: write the form as a QDialog with a QTabWidget (one page per topic, a
  `self.err` label, `_ok()` that validates and calls `accept()`), then
  `ok_btn, title = popup_form(dlg, icon, title, subtitle, verb)` - it gets the frame and the
  current style. Open it from a `_<what>_popup()` host method like `_tag_popup` in
  `main_window.py`, apply on `exec()` only.
- New connector: only `SECTIONS` in its `device_pages/<name>.py` - it shows as sections or tabs
  automatically.
