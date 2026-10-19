<p align="center">
  <img src="preview.png" alt="ShowMMR dashboard mod"/>
</p>

# ShowMMR (2026 rework)

A Dota 2 dashboard mod that brings back the MMR numbers in match history now that rank changes are variable.
Ranked rows on the profile stats page show `12345 (+45)` instead of Win / Loss, and the last match
background shows the change.

*[Русская версия](README.ru.md)*

Fork of [AveYo/ShowMMR](https://github.com/AveYo/ShowMMR) (MIT). The original mod stopped working after Valve
hardened panorama / vscript, this fork rebuilds it for the current client:

| | upstream | this fork |
|---|---|---|
| Valve layouts | replaced with modified copies, break on every Dota update | not replaced, rows are patched from `showmmr.js` |
| History storage | `JOY1-32` binds of slot 3 | archived convars, survive Dota restarts |
| Build | Workshop Tools DLC, Windows `.bat` | `python3 tools/build.py`, standard library only |
| Install | `pak01_dir.vpk` + `gameinfo_branchspecific.gi` | one `pak02_dir.vpk` in the `dota_<language>` folder |

## How it works

```
 panorama/scripts/showmmr.js                 scripts/vscripts/core/coreinit.lua
 (dashboard UI)                              (local vscript server)

 opens the hidden hero stats page
 reads #ranked_mmr_value and the start
 time of the latest ranked match
        |  SendCustomGameEventToServer
        |  ShowMMR_Refresh {mmr, time}
        v
                                             change = mmr - mmr of the previous known match
                                             data[time] = {mmr, change}
                                             Convars: showmmr_<account>_1..64  (FCVAR_ARCHIVE)
                                             host_writeconfig -> machine_convars.vcfg
        ^
        |  CustomNetTables ShowMMR_history / ShowMMR_update
        |
 patches the Win / Loss text of ranked rows:  12345 (+45)
```

- The start time of a match is the key. Panorama can only localize a time, not read it back, so
  `MatchTime()` derives candidate UTC timestamps from the displayed date and picks the one that localizes the same.
- History is saved as `time:[mmr,change]` entries, 8 per convar, 64 convars per account, so up to 512 recent matches.
- Every Dota start the vscript VM registers the convars again and reads the saved values back.
- History is refreshed when the dashboard is shown after a game, on `DOTARankUpdated`, and once 20 seconds after startup.
- Nothing touches gameplay, the scripts only run on the dashboard (`if GameRules then return end`).

The first match after a fresh install has no previous value to compare against and shows `(0)`.
The next one is calculated from it.

## Install

Dota 2 only mounts mods from a language folder, so the audio language must not be English
(Settings > Audio, e.g. Russian). The folder must exist: `game/dota_<language>`.

```
git clone https://github.com/kuanysh228/ShowMMR
cd ShowMMR
python3 tools/build.py --install
```

The tool finds Dota through Steam's `libraryfolders.vdf`. If it cannot, or you want another language:

```
python3 tools/build.py --install --game "/path/to/dota 2 beta/game" --language russian
```

Or copy `release/pak02_dir.vpk` into `game/dota_<language>/` by hand. Restart Dota.

Remove with `python3 tools/build.py --uninstall`.

## Build

`tools/build.py` compiles `source/dota` without Workshop Tools: it writes the KV3 binary for layouts, the
`.vjs_c` resource for scripts, and packs everything into a version 2 VPK.

```
python3 tools/build.py            # release/pak02_dir.vpk
```

## Repository layout

```
source/dota/
  panorama/layout/base.xml          includes Valve's base styles and showmmr.js
  panorama/scripts/showmmr.js       UI side: reads the number, patches rows
  scripts/custom_net_tables.txt     ShowMMR_history, ShowMMR_update
  scripts/vscripts/core/coreinit.lua  data side: computes changes, persists them
tools/build.py                      VPK builder and installer
release/pak02_dir.vpk               prebuilt mod
ShowMMR_tool/                       upstream C# tool that downloads old history from Steam (unchanged)
```

## Import old history

`ShowMMR_tool` (from upstream, needs .NET) fetches previous matches from the game coordinator and writes
`game/dota/cfg/showmmr_<account>.vcfg`. The mod imports it on startup and only fills in matches that are not saved yet.

## Disclaimer

The scripts do not touch gameplay and are easy to review, but Valve can change the dashboard at any time. Use at your own risk.

## Credits

Original mod by [AveYo](https://github.com/AveYo/ShowMMR). MIT license.
