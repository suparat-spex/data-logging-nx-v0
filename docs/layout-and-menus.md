# Layout and menus: Connectivity and Tags

How the Connectivity and Tags documents are laid out, what each list and table shows, which
menus exist and what the pop-ups look like. **When to use** an editor tab or a pop-up is in
[ui-guidelines.md](ui-guidelines.md); tokens, widgets and the window shell are in
`src/nxlogger/ui/theme/DESIGN.md`.

## 1. Principles

- **One pattern for both documents:** a band heading with a list on the left, a table on the
  right, and the Properties and Runtime docks on the far right (unchanged).
- **Project tree nodes are flat categories.** No devices or tags are listed in the tree.
  Clicking a node opens the document on that category.
- **Right-click only on table rows.** Tree nodes, list rows and empty table space have no menu.
  Everything is also on the toolbar, so nothing is reachable only through right-click.
- **Toolbars never move:** buttons are disabled, never hidden; Add ▾ changes its items per
  category but stays in the same place.
- **Settings pop-ups use the tabs style** (`common.POPUP_STYLE = "tabs"`), one fixed size
  (560 × 520), so they fit a 1280 × 800 laptop and no tab scrolls.
- **Properties is read-only** for devices and tags of every category, with an *Edit...*
  button that opens the pop-up.
- **Wording:** plain English ("All connections"), no decorative symbols such as ▾ in hint
  text, the verb on the button ("Add tag", "Save", "Delete 3 tags").

## 2. Project tree

```
▾ plant.nxl
  ▾ Connectivity  ·  3 online
      Protocols      (5)
      Database       (0)        muted while it has no devices
      Web Services   (0)
  ▾ Tags
      Device        (81)
      Virtual        (7)
      Action                    planned (muted)
      Aggregation    (6)
      Memory                    planned (muted)
      System        (33)
      Groups         (5)        number of groups
  ▸ Logging
  ▸ Alarms
  ▸ Variables
  ▸ Views
```

Section headers (the project, Connectivity, Tags, Logging, Alarms, Variables, Views) and the
category nodes have **no right-click menu**.

## 3. Connectivity document

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ [+ Add ▾][Edit][⧉][🗑] | [Import...][Export...]          [Columns ▾][Filter] │
├────────────────────┬────────────────────────────────────────────────────────┤
│▌🏭 Protocols       │ Name      Connector   Address          Scan   Tags State│
│ All connections (5)│ ● PLC-01  Modbus TCP  10.0.0.5:502     500 ms  32  Online│
│ Modbus TCP     (2) │ ● Meter-2 Modbus TCP  10.0.0.9:502     500 ms  12  Online│
│ Modbus RTU     (0) │ ● WTP     MQTT        broker:1883      -       49  Online│
│ MQTT           (1) │                                                          │
│ OPC UA client  (1) │                                                          │
│ Simulation     (1) │                                                          │
│ OPC DA (planned)   │ 5 device(s) · 5 enabled · 3 online · double-click ...   │
└────────────────────┴────────────────────────────────────────────────────────┘
```

**Categories and connectors**

| Category | Connectors |
|---|---|
| Protocols | Modbus TCP, Modbus RTU, MQTT, OPC UA client, Simulation, OPC DA (planned) |
| Database | MySQL, PostgreSQL, InfluxDB (all planned) |
| Web Services | REST / HTTP, Webhook (in), WebSocket, Firebase, MCP Server (all planned) |

A driver's category is `DRIVERS[key]["category"]`; planned connectors are
`core/tags.CONNECTORS_PLANNED`. Nothing is stored in the project file.

**Left list:** the category band, *All connections (n)*, then one row per connector with
its device count.
- The connector icon takes the worst state colour of its devices (bad > warn > ok).
- It is muted when the connector has no devices or is planned.
- The list has the same left margin as the Tags list.

**Selection:** switching category or connector clears the table selection and Properties
shows the empty state; no row is selected automatically. Only the first visit to the tab
selects the first device. Opening a device from elsewhere (a banner, *Open tags*) switches to
its category and selects it.

**Properties with nothing selected**

| Category | Title | Line |
|---|---|---|
| Protocols | No device selected | Select a device to view its settings |
| Database | No database selected | Select a database to view its settings |
| Web Services | No web service selected | Select a web service to view its settings |
| Several rows | 3 devices selected | Select one device to view its settings |

When the table is empty, the line repeats the table hint, which says how to add one:
- "No Modbus TCP devices - Add > Modbus TCP..., or Import..."
- "No databases yet - the Database connectors are planned."
- "MySQL is planned - not available yet."

**Toolbar:** *Add ▾* lists the connectors of the shown category (planned ones disabled).

**Row menu** (device rows only, the same for every connector):

```
Edit device...            Enter
Open tags
──────────────────────
Connect / Disconnect      (several rows: "Connect (3)")
Disable device / Enable
──────────────────────
Duplicate
Export...
──────────────────────
Delete device...          Del
```

With several rows selected, *Edit device...*, *Open tags* and *Duplicate* are disabled.
Connector actions (Browse OPC UA, Test connection, ...) belong in the device pop-up, not in
the menu.

## 4. Tags document

The same pattern: band + list on the left, tag table on the right. Columns (Columns ▾,
remembered): Name, Device, Group, Address, Type, Access, Unit, Value, Quality, Updated,
Description.

| Category | Left list | Add ▾ | Rows edited in |
|---|---|---|---|
| Device | All tags, devices > groups | Tag..., Tags..., Group..., Browse OPC UA... | Tag pop-up |
| Virtual | All tags, one row per type, the groups in use | Constant..., Formula..., Conversion..., Logic..., Selector..., Text map... | Virtual tag pop-up |
| Aggregation | All tags, one row per type, the groups in use | Statistic..., Totalizer..., Counter..., Runtime..., Min / Max hold..., Change... | Aggregation pop-up |
| System | All tags, Clock, App, Recording, Alarms, Devices > device | - (read-only) | - |
| Groups | All tags, the group tree, (no group) | Group... | Each row's own pop-up |
| Action, Memory | planned | - | - |

A type row turns red when one of its tags has an error. Import / Export are disabled outside
Device.

### Virtual and Aggregation

```
┌──────────────────┬───────────────────────────────────────────────────────────┐
│▌ƒ Virtual        │ Name        Device   Group  Address            Type   Value│
│ All tags     (7) │ Total_Flow  Virtual  Water  (A + B) * 3.6      Formula 84.78│
│ # Constant   (1) │ Pipe_Area   Virtual         0.0314             Constant 0.03│
│ ƒ Formula    (2) │ Side        Virtual         Flow > Low, High   Text map High│
│ ⇄ Conversion (1) │                                                          │
│ ✓ Logic      (1) │                                                          │
│ ☰ Selector   (1) │                                                          │
│ T Text map   (1) │                                                          │
│ 📁 Water     (2) │ 7 virtual tag(s) · numbers work in views, alarms ...     │
└──────────────────┴───────────────────────────────────────────────────────────┘
```

Aggregation looks the same; its Address column adds the period ("∫ Flow dt (per hour) ·
Day 06:00").

**Row menus**

```
Virtual                      Aggregation
Edit...                      Edit...
Duplicate                    Duplicate
Disable / Enable (n)         Disable / Enable (n)
───────────────              Reset now...          (tags with a period)
Add alarm...    (not text)   ───────────────
───────────────              Add alarm...
Delete tag...                ───────────────
                             Delete tag...
```

While recording, everything except *Add alarm...* (and *Reset now...*) is disabled.

### Groups

One group tree for the whole project (`Project.all_groups()`); the same path on two devices
is one group.

```
┌──────────────────┬───────────────────────────────────────────────────────────┐
│▌📁 Groups        │ Name           Device       Group           Type           │
│ All tags    (90) │ RawFlow        PLC-01       Raw water       Device · Number│
│ 📁 Filters   (6) │ Pump1_Run      PLC-01       Raw water/Pumps Device · Number│
│ ▾ 📁 Raw water(9)│ Volume_Today   Aggregation  Raw water       Aggregation ·  │
│     📁 Pumps (4) │ Flow_x2        Virtual      Raw water       Virtual · Formula│
│ (no group)  (12) │                                                          │
└──────────────────┴───────────────────────────────────────────────────────────┘
```

- Counts include sub-groups. A parent group shows the tags of its sub-groups too.
- **Toolbar:**
  - *Add ▾ > Group...* adds a group under the selected one.
  - *Edit* renames the selected group (with no row selected) or opens the selected row's own
    pop-up.
  - *Delete* deletes the selected group.
- **Rename** changes the path in every category. **Delete** moves the group's tags and
  sub-groups up one level - no tag is ever deleted from here.
- **Row menu:** *Edit...* · *Move to group ▸* (every group and "(no group)").
- The Group field of the device tag, Add tags, Virtual and Aggregation pop-ups offers the same
  project groups.

## 5. Settings pop-ups

All use `common.popup_form` (header band with icon, title and one line; tabs; footer with
the message line and [Cancel] [verb]).

```
┌───────────────────────────────────────────────────────────┐
│ ƒ  Total_Flow                                             │
│    Virtual tag · Formula · Calculated from tags, ...      │
├───────────────────────────────────────────────────────────┤
│ [General] [Expression] [Calculation] [Display]            │
│   Type         Formula - Calculated from tags, ...        │
│   Name         [Total_Flow                      ]         │
│   Group        [Water                         ▾]          │
│   Data type    [Number                        ▾]          │
│   Unit         [m³/h                          ▾]          │
│   Colour       [■ #e8b100]  [Default]                     │
│   Description  [Sum of the three flow meters    ]         │
│                [✓] Enabled                                 │
├───────────────────────────────────────────────────────────┤
│ ✓ Ready                              [Cancel] [Save]      │
└───────────────────────────────────────────────────────────┘
```

**Behaviour**
- **New:** the verb is *Add tag*. The pop-up opens on the second tab, the one that defines
  the tag.
- **Edit:** the verb is *Save*. The pop-up opens on *General*.
- **The verb stays disabled** until the definition is valid. The footer names the problem
  ("✗ Expression: Unknown name 'Flow4'") and its tab is marked with ●.
- **Live values** (inputs, result line "= 84.78 m³/h ✓", source "Now: ...") refresh every
  500 ms.
- **While recording:** the pop-up can be opened to look, but not saved.
- **The type is fixed** once the tag exists, like a device's connector.

**Colour** (same control as device tags): the swatch button shows the real colour and its
hex code and opens the colour picker; *Default* goes back to the palette colour, which is
saved as "" so it keeps following the palette.

**Tabs per type**

| Pop-up | Tabs |
|---|---|
| Virtual · Constant | General · Value · Display |
| Virtual · Formula | General · Expression · Calculation · Display |
| Virtual · Conversion | General · Conversion · Calculation · Display |
| Virtual · Logic | General · Condition · Calculation · Display |
| Virtual · Selector | General · Inputs · Calculation · Display |
| Virtual · Text map | General · Text map · Calculation · Display |
| Aggregation · Statistic | General · Statistic · Display |
| Aggregation · Totalizer / Counter / Runtime / Hold / Change | General · *type* · Period · Display |

- **General:** Type (info), Name, Group, Data type (Virtual only; fixed by some types), Unit,
  Colour, Description, Enabled.
- **Calculation (Virtual):** Update (when any input changes / on a trigger tag / every N ms)
  and Bad input (result is bad / keep the last good value; a Selector skips bad inputs).
- **Period (Aggregation):**
  - Reset: never, every hour / day / week / month at a time, at shift changes, or when a tag
    becomes 1.
  - Keep the previous period's value.
  - Keep the running value on restart.
  - The next reset is shown.
- **Display:** Decimals.

## 6. Start Page

The first tab (like the start pages of LabVIEW NXG, BenchVue, UaExpert): where you begin. It
has a ✕ and comes back from the toolbar house, Project > Start Page (Ctrl+0) or a click on the
project root in the Project tree (which also shows the project in Properties). "Show the Start
Page at startup" is the check box at its bottom.

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ ∿ NX Logger                                                                   │  banner
│   Version 2.x                                                                 │
├──────────────────────────────────────────────────┬────────────────────────────┤
│ Current project   Water Plant A   plant.nxl · …   │ Get started          1/4 ‹ ›│
│  ● 4 / 5 devices online · 81 tags · ⚠ 2 alarms …  │  Connect a device          │
│  [Connectivity] [Tags] [Views ▾] [Alarms] [Logging]│  Add a Modbus, MQTT, …     │
├──────────────────────────────────────────────────┤  [Open Connectivity]       │
│ Recent projects   (click to open) · Browse...     ├────────────────────────────┤
├──────────────────────────────────────────────────┤ Documentation              │
│ New project                                       │  • Connectivity and tag …  │
│  [Blank] [Simulation] [Modbus TCP]                │  • Layout and menus        │
│  [MQTT / IoT] [OPC UA] [Water plant demo]         │  • Recording file format … │
├──────────────────────────────────────────────────┤ Simulators                 │
│ Recent sessions   (click: open the folder)        │  ▸ Water treatment plant   │
│                                                   │  ▸ ESP32 waveform publisher│
└──────────────────────────────────────────────────┴────────────────────────────┘
  [✓] Show the Start Page at startup
```

- **Current project:** name (* when unsaved), file, a live status line (devices online, tags,
  alarms, recording) and buttons to the main documents. *Views ▾* lists the project's views and
  *New view...*.
- **Properties** shows the project read-only (name, operator, description, file, log folder,
  counts), like devices and tags; *Edit...* opens the project pop-up (tabs General · Files:
  name, operator, description; project file, log folder - locked while recording).
- **Recent projects:** up to 8, newest first (opened or saved); the open one is bold. A
  template or another project asks about unsaved changes first.
- **New project:** template tiles (`core/templates.py`). Each makes a ready project - device,
  tags, logging profile, popup alarm profile and an Overview view.
- **Recent sessions:** the newest 5 recordings of the log folder with their size.
- **Get started:** four steps (Connect a device, Add tags, Create a view, Start recording),
  each with its button; ‹ 1/4 › sits on the heading row and the card keeps one height (the
  longest step at the current width), so stepping never resizes it.
- **Documentation and Simulators:** links to `docs/*.md` and to the water plant / waveform
  simulators. The simulators start only from the source checkout - the EXE shows them muted.
- **Layout:** two columns on a wide page, one below 980 px. Tiles go 3 → 2 → 1 columns. The
  page never scrolls sideways.
- **At startup the Start Page is the only tab** (Connectivity when it is switched off). Every
  other document - Connectivity, Tags, Logging, Alarms, Variables and the project's views - opens
  as a tab when it is first used (Project tree, menus, buttons).
- **Quick View** (live plots of every tag) is still in the Project menu (Ctrl+1); it no longer
  opens at startup.

## 7. Views

- **Fixed size:** every view is a canvas of its own size - **1920 × 1080** by default; View
  pop-up > Size: 1280 × 800, 1366 × 768, 1920 × 1080, 2560 × 1440, 3840 × 2160 or custom, and
  *top left* / *top centre* on a bigger window. It never stretches: with the right panel open or
  closed the layout is the same, a smaller window shows horizontal and vertical scroll bars.
  Components stay inside it (dragging, resizing, the Position tab); while editing its edge is
  drawn in the accent colour. A view is never smaller than its components.
- **Pop-ups, like Connectivity and Tags** (tabs style, `common.popup_form`):
  - *View* (toolbar ⚙, Properties > *Edit...*, Project tree): General · Size · Canvas · Cards.
  - *Component* (double-click in Edit mode, right-click > *Edit...*, Properties > *Edit...*):
    the tabs of its kind + Appearance + **Position** (X, Y, W, H, snapped to the grid).
  - *Add* (Text / Graph / Indicator / Table ▾) opens the component pop-up first ("New
    component", verb *Add*); Cancel adds nothing.
  - Moving and resizing with the mouse in Edit mode stays direct.
- **Properties is read-only** for a view (name, size, components, tags used, grid; *Edit...*,
  *Edit layout*, *Monitor*) and a component (type, title, position, size, tags; *Edit...*,
  *Duplicate*, *Delete*). Views and components no longer open as editor tabs.
- **Toolbar:** Home, New, Open and Save are icons only at every window width (name and shortcut
  in the tooltip); the other buttons still show their text when there is room.

## 7a. Monitor mode

Full-screen views for watching a test or a plant on a big screen - a data-logger display, not a
control HMI. Enter with **Monitor** on a view's toolbar, View > Monitor mode or **F11**.

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│[Overview ▾] ‹ › Water Plant A   ● 4/5 online  ⚠ 2 alarm(s)  ● REC 01:12:05 │ 10:42:05 │ ⛶ [Cycle: off ▾] ✕ Exit │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│                               the view, full size                                        │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Hidden:** menu bar, toolbar, every panel (Project, Data, Properties, Runtime, Log), the tab
  bar, the status bar and the view's own toolbar. Editing is switched off. The connection /
  recording banners still show.
- **Monitor bar** (`ui/widgets/monitor_bar.py`, `QFrame#monitorBar`):
  - *View ▾* lists the project's views; *‹ ›* or Page Up / Page Down step through them.
  - Status chips refresh twice a second: devices online (green when all are), alarms (red;
    a click leaves Monitor mode on the Alarms page), REC with the elapsed time.
  - Clock, *⛶* full screen, *Cycle* (off / 10 s / 30 s / 1 min / 5 min) for wall displays.
- **Exit:** *✕ Exit*, F11 or Esc. Panels, tabs, menu, toolbar, status bar and the window size come
  back exactly as before (`saveState` on entry).
- The last view, full screen and cycle are remembered per PC (QSettings `monitor/...`), not in
  the project. Opening another project leaves Monitor mode first.
- Out of scope (SCADA): exit PIN, kiosk start, navigation buttons, writing values from the
  display.

## 8. Planned

- Connectors under Database and Web Services. They are listed now, muted, so the layout does
  not change when they arrive.
- An MCP Server (Web Services) so AI clients can read tags, alarms and history.
- Per-category table columns (Role, URL, Auth ...) once those connectors exist.
- Tags > Action and Tags > Memory.
- Statistics of Variables moving into Aggregation; Virtual and Aggregation tags in the
  recording files.
