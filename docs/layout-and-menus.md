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
      All tags     (127)        every category in one table
      Device        (81)
      Virtual        (7)
      Action                    planned (muted)
      Aggregation    (6)
      Memory                    planned (muted)
      System        (33)
  ▾ Assets · Demo factory       the Enterprise (click: its areas and machines)
      Main            (1)       the areas, one level (click: its machines and tags;
      Production      (3)       the icon turns red on an alarm)
      Utilities       (4)
      Templates       (1)       muted, always last (click: the templates)
  ▾ Logging                     every profile + the log folder
      Interval        (2)       the logging types, one level (click: its profiles;
      On change       (1)       muted while empty) - profiles only in the document
      Trigger         (1)
      Event           (0)
      Batch / Shift   (1)
      Statistics      (1)
  ▸ Alarms
  ▸ Views
```

Section headers (the project, Connectivity, Tags, Assets, Logging, Alarms, Views)
and the category / area / logging-type nodes have **no right-click menu** (double-click an area: its pop-up).

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
| All tags | All, Device, Virtual, Aggregation, System | - | Each row's own pop-up |
| Device | All tags, devices | Tag..., Tags..., Browse OPC UA... | Tag pop-up |
| Virtual | All tags, one row per type | Constant..., Formula..., Conversion..., Logic..., Selector..., Text map... | Virtual tag pop-up |
| Aggregation | All tags, one row per type | Statistic..., Totalizer..., Counter..., Runtime..., Min / Max hold..., Change... | Aggregation pop-up |
| System | All tags, Clock, App, Recording, Alarms, Devices > device | - (read-only) | - |
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

### All tags

The first Tags node: every tag of every category in one table; the left list filters it by
Device / Virtual / Aggregation / System. Tags have **no groups** - search and sort find a tag,
the **Assets** column shows where it is used ("Utilities / Air compressor 1").
Right-click any tag rows: *Add to asset ▸* (every area and machine with its icon, or *New
area...*) and *New view from selection*.

### Assets (Project tree > Assets)

The plant in three levels, like an ISA-95 equipment model kept short:
**Enterprise** (the project - its name in the Project pop-up) > **Area / Zone / Production** >
**Machine / Station / Unit** > tags. Types are labels and icons only. An asset references tags
of any category by key - a tag can be in several assets; deleting an asset never deletes a tag.
An area can hold tags of its own (a meter of the area, the plant's key values).

The left list is always **one level** - a band, an *All ...* row, the items - like Tags and
Connectivity. Selecting a row filters the table; double-clicking goes one level down. One toolbar
on every level: `[+ Add ▾][Edit][⧉][🗑] | [Map tags...][Remove] | [New view][Monitor]  [Search]`.

```
Project tree > Assets: the areas left, their machines right
┌──────────────────────────────┬───────────────────────────────────────────────────────┐
│▌⌂ Demo factory   621 kW      │ Name            Type     Area        Template  Tags  │
│ All areas                (5) │ ● Air comp. 1   Machine  Utilities   Power m.     9  │
│ ● ▦ Utilities  186 kW    (4) │ ▲ Line 1        Machine  Production  Power m.     9  │
│ ▲ 🏭 Production 412 kW    (3) │                                                       │
└──────────────────────────────┴───────────────────────────────────────────────────────┘
 Add ▾: Area... · Zone... · Production... · Machine... (in the selected area) · From template ▸ · Use a starter ▸

Project tree > Utilities: its machines left, their tags right (+ the area's own)
│▌▦ Utilities · Area           │ Name      Tag                   Source   Machine      │
│ All Utilities           (36) │ Power     Energy meters · …_kW  Modbus   Air comp. 1  │
│ ● ⚙ Air compressor 1 [Power meter] (9)                                               │
 Add ▾: Machine... · Station... · Unit... · From template ▸ · Tags... · Area...

Project tree > Templates: the templates left, their attributes right
│▌⧉ Templates                  │ Attribute  Template     Unit  KPI  Match   Used      │
│ ◔ Power meter     (11 used)  │ Power      Power meter  kW    ✓    *_kW      11      │
 Add ▾: Template... · From machine ▸
```

- **Status dots:** ● ok · ▲ alarm (an active alarm level on one of its tags) · ○ no data.
  **Rollups** (areas, the Enterprise): the sum of each machine's first KPI value, in its unit.
- **KPI** (a tick on an attribute, bold in the table): the Start Page *Key values* and the
  Monitor bar chips.
- **Templates:** what a kind of machine has (attributes with unit, KPI and a *match* pattern,
  `*_kW`, `*speed*|*Hz*`). A machine made from one, or given one (*Apply template ▸*), gets its
  attributes as slots (*Not mapped*); **Map tags...** fills them with the tags of one source by the
  patterns (preview, untick to skip). Saving a template adds new attributes to its machines.
- **Empty levels** show one centred message with its buttons (*Add area* / *Use a starter ▾*,
  *Add machine* / *From template ▾*, *Add tags...* / *Map tags...*) - never a pop-up.
- **Edit** acts on the selected machine row, else the selected area, else (Enterprise) the
  Project pop-up. **Duplicate** numbers on (Compressor 2 -> Compressor 3); an area with its machines.
- **Asset pop-up** (tabs): *General* (name, type, area + template for machines, icon grid +
  Default = the type's, description) · *Tags* (short name, tag, KPI). **Template pop-up**:
  *General* (name, type, icon, description, used by) · *Attributes*.
- **Area / machine menu:** Edit... · Open · Add machine ▸ · Add tags... · Map tags... · Apply
  template ▸ · Move to ▸ (another area) · Make a template from it · Duplicate · New view · Monitor ·
  Add alarms for its tags... · Delete...
- **Tag row menu:** Open tag... · Map tags... · New view from selection · Remove from asset.
- **Every tag picker** (views, alarms, logging, pop-ups) has *By tag* / *By asset*; under an
  area, *Same name ▾ - Select in all* ticks e.g. Power of every compressor (compare machines).
- **Logging profiles** can tick whole assets: kept live - tags added later are logged.
- Collections, the Assets of 2.x and Tags groups of older projects become areas and machines with
  the same tags ("A / B / C": area A, machine "B / C").

### Logging (Project tree > Logging)

A logging profile says **when** to write and **which tags**; every enabled profile runs on
Record and writes into the same session folder (docs/recording-format.md). Six types
(`core/log_types.py`):

| Type | Writes | When tab |
|---|---|---|
| Interval | every value, or one row every N s | Every |
| On change | a row when a tag moves more than its deadband, at least every heartbeat | Deadband (% / abs), Heartbeat, Min. gap |
| Trigger | only while a condition is true, with the seconds before / after it (`run` column) | Start when, Stop, Debounce, Pre / Post-trigger, While active |
| Event | one row of chosen tags (any category, text too) per event | On: alarm (any / one) · condition · time of day · Marker; Debounce |
| Batch / Shift | rows while a batch / shift runs (`batch` column) + a summary row at its end | Batches by (shift times / condition), Batch name tag, Summary, While it runs |
| Statistics | one row per period: mean, min, max ... | Period, Statistics |

```
Project tree > Interval: its profiles left, the selected one right (card + tags)
┌──────────────────────────────┬───────────────────────────────────────────────────────┐
│▌⏱ Interval                   │ ⏱ Data 1 s                         ● Writing  [Edit…] │
│ ◂ All profiles               │ 📁 E:\Trend\2026\11\05                   [Open] [Copy] │
│ ● Data 1 s                   │───────────────────────────────────────────────────────│
│   every 1 s · All enabled    │ Every      1 s                New file  every day 00:00│
│ ○ Fast trend                 │ Format     CSV                Keep      90 days       │
│   every value · 4 tags · off │ Folders    Year \ Month \ Day  Files     38 · 1.2 GB   │
│                              │───────────────────────────────────────────────────────│
│                              │ Tags (99)        [+ Add tags...][Remove]   [Search]   │
│                              │ Name      Source          Unit  Value   Asset         │
└──────────────────────────────┴───────────────────────────────────────────────────────┘
Project tree > Logging: the types left (click one: open it), every profile right
│ Name         Type        When          Folder               Tags        Status       │
```

- **Health strip** (always on top): ● RECORDING and its time / ○ Ready, how many profiles are writing,
  armed, **stalled** (a writing profile without a row for 3 intervals - a device that stopped) or in
  **error** (its folder), the first issue as a link (selects that profile), disk free and **days
  left** at the measured (estimated before recording) size a day, rows/s and dropped blocks.
- **All profiles** table: Name, Type, When, Folder, Last write, Rate, Size/day, Status.
  On the Logging page a click on a type (left) shows only its profiles in the table; a double
  click (or Project tree > the type) opens the type page.
- **A type** has no "All profiles" row (the Project tree / Backspace goes back): the band shows the
  type and its count, each profile three lines (name / when · tags / rate · size a day).
- **The profile view:** a header (name, status · last row, ✎ Edit, ⋯ more - icon buttons) and its
  folder (📂 open, ⧉ copy), then tabs
  **Overview** (its settings as a sentence, key values, rows per minute, problems) · **Tags**
  (last logged value and time, quality, asset) · **Files** (its files in a period - Open, Show in
  folder, Copy path; ● writing / ✓ complete) · **History** (interval, on change, statistics: size
  a day for 30 days, amber = a day without data; trigger / batch: runs and minutes per hour;
  event: the newest events).
- **The card** shows the profile's folder first (today's date folder; while recording the folder
  of the file written now; *(default)* = the project's; ⚠ not reachable in red), its type's
  settings, the folder layout, when the next file starts, keep / compress, and live counts
  (rows, files, disk, the file written now). The settings change only in the pop-up (*Edit…*).
- **The tags table:** *All enabled* lists every enabled tag (new ones join); *Add tags...* (By
  tag / By asset - whole assets kept live, one expandable row) and *Remove* make it a list.
- **Status:** ● Writing (green) · ● Armed (amber: a trigger / batch waiting, an event profile
  listening) · ○ Off. Counts (runs, events, batches) update while recording and stay after Stop.
- **Add ▾** offers the six types (inside a type: that type); double-click a profile: its pop-up;
  in *All profiles* double-click opens its type with it selected. While recording, profiles cannot
  change; *Test* fires a trigger (a 5 s run) or an event (one row) into the running session.
- **Profile pop-up** (tabs): *General* (name, enabled, type - fixed, description) · *When* (the
  type's settings; conditions = tag ▾ / operator / value - a Virtual logic tag for anything
  more) · *Tags* · *Output*:

```
Folder       (●) Project default  D:\Logs     ( ) This folder [E:\Trend     ] [Browse...]   ✓ exists
Folders      [Year \ Month \ Day ▾]   None · Session · Year · Year \ Month · Year \ Month \ Day · Date · Custom
New file     [Every day ▾] at [00:00]   Per session · hour · day (at) · week (day, at) · month · N MB · per run / batch
File name    [automatic: {profile}_{device}_{yyyy}{MM}{dd}]  [Tokens ▾]
Preview      E:\Trend\2026\11\05\Data_1_s_Energy_meters_20261105.csv
             E:\Trend\2026\11\06\Data_1_s_Energy_meters_20261106.csv   (next)
Closed files [ ] Compress (.zst)                       Keep [90 days]
Format       [CSV ▾]   [ ] Also raw packets (.wpk)
```

  Tokens: `{yyyy} {MM} {MMM} {dd} {HH} {ww} {project} {profile} {device} {run} {session}`. A
  rotation without its token in the name gets it added (a note says so). *Keep N days* needs
  `{profile}` in the name - it deletes only this profile's files (and empty date folders).
  New profiles: interval - year \ month \ day, a file a day; on change - year \ month, a month;
  trigger - year \ month \ day, a file per run; event - year, a month; batch - year \ month, per
  batch; statistics - year, a month. Older profiles keep the session folder, a file per session.
- **Project pop-up > Files:** the *default log folder* and which profiles use it.
- **Data dock:** the sessions of every folder in use, plus one row per profile writing into date
  folders (double-click: today's folder).
- **Row menu:** Edit... · Enable / Disable · Test (trigger, event) · Duplicate · Open folder · Delete...
- Variables are gone from the tree and the menus; those of older projects still run and log.

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
  other document - Connectivity, Tags, Assets, Logging, Alarms and the project's views - opens
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
- Converting the Variables of older projects into Virtual / Aggregation tags; Virtual and
  Aggregation tags in the recording files (event profiles already snapshot them).
