<p align="center">
  <img src="preview.png" alt="ShowMMR dashboard mod"/>
</p>

# ShowMMR (переработка 2026)

Мод для дашборда Dota 2, который возвращает MMR в историю матчей, теперь, когда изменение рейтинга переменное.
В строках рейтинговых матчей на странице статистики вместо Win / Loss показывается `12345 (+45)`,
а на фоне последнего матча — изменение.

*[English version](README.md)*

Форк [AveYo/ShowMMR](https://github.com/AveYo/ShowMMR) (MIT). Оригинал перестал работать после того, как Valve
ужесточила panorama и vscript. Этот форк переделан под текущий клиент:

| | оригинал | этот форк |
|---|---|---|
| Макеты Valve | подменяются своими копиями, ломаются с каждым обновлением | не подменяются, строки правятся из `showmmr.js` |
| Хранение истории | бинды `JOY1-32` слота 3 | архивные convar, переживают перезапуск Доты |
| Сборка | Workshop Tools и `.bat` под Windows | `python3 tools/build.py`, только стандартная библиотека |
| Установка | `pak01_dir.vpk` и `gameinfo_branchspecific.gi` | один `pak02_dir.vpk` в папке `dota_<язык>` |

## Как это устроено

```
 panorama/scripts/showmmr.js                 scripts/vscripts/core/coreinit.lua
 (интерфейс дашборда)                        (локальный vscript-сервер)

 открывает скрытую страницу статистики
 героев, читает #ranked_mmr_value и время
 начала последнего рейтингового матча
        |  SendCustomGameEventToServer
        |  ShowMMR_Refresh {mmr, time}
        v
                                             изменение = mmr - mmr предыдущего известного матча
                                             data[time] = {mmr, change}
                                             Convars: showmmr_<аккаунт>_1..64  (FCVAR_ARCHIVE)
                                             host_writeconfig -> machine_convars.vcfg
        ^
        |  CustomNetTables ShowMMR_history / ShowMMR_update
        |
 правит текст Win / Loss в рейтинговых строках:  12345 (+45)
```

- Ключ записи — время начала матча. Panorama умеет только локализовать время, но не читать его обратно,
  поэтому `MatchTime()` строит варианты UTC-времени из показанной даты и выбирает тот, что локализуется так же.
- История хранится как `время:[mmr,изменение]`, по 8 записей в convar, 64 convar на аккаунт, то есть до 512 последних матчей.
- При каждом запуске Доты vscript заново регистрирует convar и читает сохранённые значения.
- История обновляется при возврате на дашборд после игры, по событию `DOTARankUpdated` и один раз через 20 секунд после старта.
- Геймплей мод не затрагивает: скрипты работают только на дашборде (`if GameRules then return end`).

Первый матч после установки не с чем сравнивать, поэтому он покажет `(0)`. Следующий считается уже от него.

## Установка

Dota 2 подключает моды только из папки языка, поэтому язык озвучки не должен быть английским
(Настройки > Звук, например русский). Папка `game/dota_<язык>` должна существовать.

```
git clone https://github.com/kuanysh228/ShowMMR
cd ShowMMR
python3 tools/build.py --install
```

Скрипт находит Доту через `libraryfolders.vdf` Steam. Если не нашёл или нужен другой язык:

```
python3 tools/build.py --install --game "/путь/к/dota 2 beta/game" --language russian
```

Либо вручную скопируйте `release/pak02_dir.vpk` в `game/dota_<язык>/`. Перезапустите Доту.

Удаление: `python3 tools/build.py --uninstall`.

## Сборка

`tools/build.py` собирает `source/dota` без Workshop Tools: пишет бинарный KV3 для макетов, ресурс `.vjs_c`
для скриптов и упаковывает всё в VPK версии 2.

```
python3 tools/build.py            # release/pak02_dir.vpk
```

## Структура репозитория

```
source/dota/
  panorama/layout/base.xml          подключает базовые стили Valve и showmmr.js
  panorama/scripts/showmmr.js       сторона интерфейса: читает число, правит строки
  scripts/custom_net_tables.txt     ShowMMR_history, ShowMMR_update
  scripts/vscripts/core/coreinit.lua  сторона данных: считает изменения, сохраняет
tools/build.py                      сборщик VPK и установщик
release/pak02_dir.vpk               готовый мод
ShowMMR_tool/                       C#-утилита из оригинала, качает старую историю из Steam (без изменений)
```

## Импорт старой истории

`ShowMMR_tool` (из оригинала, нужен .NET) запрашивает прошлые матчи у game coordinator и пишет
`game/dota/cfg/showmmr_<аккаунт>.vcfg`. Мод импортирует файл при старте и добавляет только те матчи, которых ещё нет в сохранённых.

## Отказ от ответственности

Скрипты не трогают геймплей и легко проверяются, но Valve может изменить дашборд в любой момент. Используйте на свой риск.

## Авторство

Оригинальный мод — [AveYo](https://github.com/AveYo/ShowMMR). Лицензия MIT.
