# UI guidelines: tabs, pop-ups, panels

NX Logger follows the workbench pattern of SCADA engineering tools (ICONICS Genesis64
Workbench, FUXA editor): things you configure open as documents, quick actions open as
pop-ups. One question decides it:

> **Does it stay saved in the project after you close it?**
> Yes -> **editor tab**. No (a one-off action) -> **pop-up**.

## The four places

| Place | For | Examples | Buttons |
|---|---|---|---|
| **Editor tab** (closable document) | configuring one saved item | alarm, notification profile, variable, logging profile, view, view component (*All settings*), AI/ML log settings | Apply · Refresh · Close · New... · Apply & Close · Apply & New... |
| **Work page** (document tab) | working with many items / watching live data | Quick View, Connectivity (device table), Tags, Logging, Alarms, Variables, views | none - they act at once |
| **Properties panel** (right) | quick changes to the selection | a component's position, a view's grid; devices, tags and groups are **read-only** there (their pop-up is the one place to change them) | none - applies at once; devices / tags / groups: *Edit...* |
| **Pop-up** (modal dialog) | one action, finished when it closes | device, tag and group settings (Add / Edit - Cancel · *Add device* / *Add tag* / *Add group* / *Save*), delete?, write value, add tags in bulk, browse OPC UA, pick tags, new view, marker, import devices / tags, export, unsaved changes | Cancel · **Verb** |

Opening: single-click selects (Properties panel), **double-click opens the editor tab** (a device, tag or group: its settings pop-up),
right-click lists the actions. The same item never opens twice: a second double-click
brings its tab to the front.

## Editor tabs

```
| Full Path:  Project / Tags / PLC / Boiler / Temperature                       [TAG] |
|  ▼ General           (collapsible sections, one per topic)                          |
|  ▼ Address                                                                          |
|  ▼ Scaling                                                                          |
| [Write value...] [Add alarm] [Delete]     Apply  Refresh  Close  New...  Apply & Close  Apply & New... |
```

- **Path bar** (accent colour): where the item lives, and its type on the right.
- **Sections**: the form's pages, stacked and collapsible - long forms stay scannable.
- **Item actions** on the left of the button bar (things you *do* with the item), the
  editing buttons on the right, always in the same order.
- **Apply** writes into the project (the project file is saved with File → Save).
  Apply / Apply & Close are enabled only when something changed; **Refresh** only when
  there are edits to discard.
- **New items are drafts**: *New...* opens an empty editor; the item joins the project on
  the first Apply. Closing an unapplied draft leaves the project untouched.
- **Modified**: the tab title gets `*` and an accent icon. Closing it asks
  *Apply / Don't apply / Cancel*; so do New project, Open and Exit for all open editors.
- Errors stay in the path bar (red) until fixed; "Applied · 14:05:31" fades after 3 s.
- Locked while recording (devices, tags, variables, logging profiles): Apply explains why.
- Keys: **Ctrl+S** Apply, **Ctrl+Enter** Apply & Close, **Ctrl+W** close tab; middle-click
  closes a tab; right-click a tab: Close, Close others, Close tabs to the right,
  Close all editors.

## Pop-ups

The Connectivity and Tags pages work alike: their items (devices, tags, groups, virtual tags) are added and
edited in a settings pop-up (header band, body, footer *Cancel* · *verb*) and are
read-only in the Properties panel. A tabbed form gets that look with `common.popup_form()`.
The body is tabs (default - long forms) or one page of titled sections (`common.POPUP_STYLE`);
both are kept and described in the nxlogger-dev skill, `references/popup-styles.md`.
Right-clicking a section header of the Project tree (Connectivity, Tags, Logging, Alarms,
Variables, Views, the project) shows no menu - the items under it have theirs.

- Title, **one line saying what will happen**, the form, a message line, then
  `[Cancel] [Verb]`. The primary button names the action: *Write*, *Create view*,
  *Add 10 tags*, *Delete 3 tags* - never a bare "OK" for an action.
- Destructive confirmations (`common.confirm(danger=True)`): warning icon, the verb on the
  button, a second line with the consequence, and **Cancel is the default** - Enter never
  deletes by accident. Choices with more than yes / no get one button per outcome
  ("Keep tags" / "Delete group and tags" / Cancel).
- Unsaved changes (`common.ask_unsaved`): *Save / Don't save / Cancel* (project) or
  *Apply / Don't apply / Cancel* (editors).
- A pop-up may open from an editor tab (pick tags, browse a server) - never the other way
  round, and never an editor form as a pop-up.
- Information only: About, Keyboard shortcuts, "view is full". Background events
  (alarms, connection lost, recording problems) are **not** pop-ups: toasts, banners and
  the Log panel carry them, so unattended logging is never blocked by a dialog.

## Properties dock

Same bar as the Project / Data docks: **Refresh**, the item's actions as icons (tooltips), and
**Expand all / Collapse** on the right. Sections are full-width bands with a border (neutral
theme colours). A tab that comes back to the front restores its selected row and shows its
properties again (`activate()` on Connectivity / Tags).

## Toolbars never move

Every toolbar button keeps its place and size. A button whose text changes (Connect /
Disconnect, Edit / Done, *Alarms (3)*, the REC timer) gets the width of its longest text with
`ui_kit.fixed_width(widget, *texts)`; a button that does not apply is **disabled, not
hidden** (Tags: *Browse...* outside OPC UA). Buttons that switch with a mode (view edit mode)
sit to the right of the fixed ones.

## For developers

- Editor tab: a form in `ui/dialogs/` (QDialog with `_ok()` that calls `accept()` and a
  `result_*()` / `apply()`); its QTabWidget pages become sections. Add
  `_spec_<what>()` in `main_window.py` and open it with `open_editor(what, ident, **ctx)`.
- Pop-up: subclass `common.ActionDialog(parent, title, intro, verb)`, fill `self.form`,
  override `validate()`. Confirmations: `common.confirm(...)`.
- Do not call `QMessageBox.question` or `QInputDialog` directly.
