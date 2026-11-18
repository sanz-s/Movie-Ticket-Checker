# Movie Ticket Checker

Watches **District** and **TicketNew** for a movie's shows and sends you a
**desktop notification** the moment tickets open at the theatres you care about.
It also runs a local **dashboard** with the shows, seat counts and the actual
seat map of each show.

Works on **macOS**, **Windows** and **Linux**. Pure Python standard library: no `pip install`.

> District and TicketNew sell the same seats (every District cinema is tagged
> `ticketnew`), so one check covers both. BookMyShow blocks automated requests
> and is not supported.

## Disclaimer

This is an **unofficial, personal-use** tool. It is not affiliated with,
endorsed by or connected to District, Zomato, TicketNew, Paytm, BookMyShow, PVR
INOX, Cinépolis or any cinema. All names and logos belong to their owners.

- It reads publicly visible showtime pages and, for the seat-map view, an
  **undocumented private API** of District. Automated access may be **against the
  terms of service** of these sites, and the API can change or stop working at
  any time.
- It only **reads** information. It never books, holds or reserves seats. It
  does not get around logins, queues, waiting rooms or CAPTCHAs, and it must not
  be changed to do so.
- **Do not use it for bulk buying, scalping or reselling tickets**, or for
  sending high volumes of requests. Keep the check interval reasonable (60s or
  more is gentler on their servers).
- Seat counts and times come from third-party data and may be delayed or wrong.
  Always confirm on the official site before booking.
- You are solely responsible for how you use this software and for following
  the terms of the sites you access and applicable law (e.g. India's IT Act,
  2000). The software is provided "as is", without warranty of any kind (see
  `LICENSE`). The authors accept no liability for any misuse, account bans,
  blocks or losses.

This is not legal advice. If you're unsure whether your use is allowed, don't
use it.

## Features

- Checks every N seconds (default 15s) and notifies you when a matching show
  gets seats, or when a sold-out show gets seats back
- Pick any **city** and **movie** District lists (posters, languages). The
  theatre list switches to that city's theatres
- Filters: theatres, language, show start-time window, minimum free seats
- Front-row awareness: mark cheap front-row classes (e.g. *Budget*, *Classic*).
  Their seats still count, but a show with **only** front rows left is shown
  yellow, never green
- Dashboard at `http://127.0.0.1:8765`
  - one row per theatre with logo, status and show times (sold-out times hidden)
  - colours follow District's own status: Available / Filling fast / Almost full
  - hover a time for the **real seat map**: rows, aisles, free vs booked seats
  - one click to the show's seat page on District or TicketNew
  - Settings panel for everything below, no restart needed
- Background service that starts at login (LaunchAgent on macOS, Startup
  folder on Windows, systemd user service on Linux)

## Requirements

- Python **3.8+**
- macOS, Windows 10/11, or Linux with a desktop session
  - Windows: install Python from [python.org](https://www.python.org/downloads/)
    and tick **Add python.exe to PATH**. Notifications use the built-in Windows
    PowerShell, so there's nothing else to install
  - Linux notifications need `notify-send` (`sudo apt install libnotify-bin`,
    `sudo dnf install libnotify`, `sudo pacman -S libnotify`)
  - Linux background service needs systemd (most distros)

## Install

```bash
git clone https://github.com/sanz-s/Movie-Ticket-Checker.git
cd Movie-Ticket-Checker
./install.sh
```

**Windows:** download or clone the repo, then **double-click `install.bat`**,
or from a terminal:

```bat
git clone https://github.com/sanz-s/Movie-Ticket-Checker.git
cd Movie-Ticket-Checker
install.bat
```

No admin rights needed. It opens the dashboard when it's done.

Then open **http://127.0.0.1:8765** and click the bell icon to send a test
notification.

`install.sh` copies the app to the data folder and starts it as a service:

| | macOS | Windows | Linux |
|---|---|---|---|
| Installer | `./install.sh` | `install.bat` | `./install.sh` |
| App + data | `~/Library/Application Support/doomsday-tracker/` | `%LOCALAPPDATA%\doomsday-tracker\` | `~/.local/share/doomsday-tracker/` |
| Service | LaunchAgent `com.user.doomsday-tracker` | `Ticket Tracker.cmd` in your Startup folder | systemd user service `doomsday-tracker` |
| Logs | `~/Library/Logs/doomsday-tracker.log` | `%LOCALAPPDATA%\doomsday-tracker\tracker.log` | `journalctl --user -u doomsday-tracker -f` |

Options:

```bash
PORT=9000 ./install.sh        # different dashboard port
./install.sh --uninstall      # stop + remove the service (keeps your config)
```

```bat
install.bat port 9000         :: different dashboard port (Windows)
install.bat uninstall         :: stop + remove auto-start (keeps your config)
```

After editing any `.py` / `.html` file, run the installer again to update the
running copy.

### Run without installing

```bash
python3 tracker_server.py                 # dashboard + checks every 15s (Windows: py tracker_server.py)
python3 district_tracker.py --dry-run     # one check, print matching shows
python3 district_tracker.py --dry-run --all   # every show, no filters
python3 district_tracker.py --test-notify # test the desktop notification
```

## Configuration

Use the Settings panel in the dashboard, or edit `config.json` in the
data folder. It is created with defaults on first run.

| Key | Meaning | Default |
|---|---|---|
| `city` | District city key (`chennai`, `bengaluru`, `delhi-ncr`, `kochi`, …) | `chennai` |
| `location_label` | place picked in the location search (display only) | empty |
| `movie_name` | name used in notifications | `Avengers: Doomsday` |
| `movie_url` | District movie page; its city part always follows `city` | Doomsday |
| `language` | show language | `English` |
| `window_start` / `window_end` | show start-time window (24h `HH:MM`) | `10:00` / `16:00` |
| `min_seats` | alert only with at least this many free seats | `1` |
| `front_classes` | front-row seat classes (shown yellow when only these are left) | `budget, classic, normal` |
| `theatres` | theatres to alert on (`id` = District cinema id) | 6 Chennai theatres |
| `sites` | `district` / `ticketnew` on or off (`bookmyshow` unsupported) | both on |
| `open_links_on` | site a click opens | `district` |
| `interval_seconds` | seconds between checks (min 10) | `15` |
| `district_access_token` | optional, see *Seat map* below | empty |

### Changing movie or city

Settings → **Movie & location**:

1. Search a city, area or landmark. Suggestions show the address, like
   District's own location search. Check it: a search for "madurai" also
   suggests a place called *MADURAI* in Gurugram, Haryana. Picking a suggestion
   sets District's city for that spot. Or click one of the popular city buttons.
2. Pick a movie. The list has movies **open** for booking in that city and
   **upcoming** ones with their release date, so you can track a movie before any
   cinema there opens bookings. The dashboard then says it's not open yet, and
   you get a notification when it opens. The language list follows the movie.
3. The theatre list now shows that city's theatres. Switching city clears your
   old theatres, so tick the new ones. It only has cinemas that sell on
   District/TicketNew (e.g. Madurai has just one); theatres that sell elsewhere
   can't be tracked.
4. **Save & re-check.** The dashboard switches to the new city and movie.

## Seat map

When you hover a show time, the server fetches that show's seat layout using the
same read-only request District's own seat page makes. It only reads the
layout and never holds or reserves seats. Results are cached for 30 seconds.

- It uses a visitor token, the same as the website for people who are not logged
  in. If District ever requires login, put your own `x-access-token` cookie from
  district.in into `district_access_token`.
- District does not share seat maps for some shows (e.g. opening day returns
  HTTP 412). The panel then shows the per-section counts instead.
- This relies on District's private API. It may change without notice and may
  be against their terms of use. Use it gently and at your own risk.
- The location search in Settings uses District's private location API in the
  same way (read-only, only when you type).

## Files

| File | What it does |
|---|---|
| `district_tracker.py` | fetch + parse District showtimes, filters, alerts, config, notifications |
| `tracker_server.py` | background checker + dashboard web server (`127.0.0.1` only) |
| `seatmap.py` | read-only seat-map fetch for one show |
| `district_api.py` | location search with address suggestions, and place → District city |
| `tracker_ui.html` | the dashboard |
| `install.sh` | install / update / uninstall the service (macOS + Linux) |
| `install.bat` | install / update / uninstall on Windows (no admin needed) |

## Notes and troubleshooting

- **Times** are shown in India time (District's raw data is UTC).
- **No notification on macOS?** Notifications come from *Script Editor*: allow it
  in System Settings → Notifications, and check Focus / Do Not Disturb.
- **No notification on Windows?** Check Settings → System → Notifications:
  notifications must be on, and *Windows PowerShell* (the sender) must be
  allowed. Focus / Do not disturb hides them.
- **Windows: the dashboard doesn't open after login?** Check that
  `Ticket Tracker.cmd` is in your Startup folder (Win+R → `shell:startup`) and
  look at `tracker.log` in `%LOCALAPPDATA%\doomsday-tracker`. Run `install.bat`
  again to restart it.
- **No notification on Linux?** Install `notify-send`. If the service started
  before your desktop session, run `./install.sh` again from a terminal inside
  the session. To keep it running after you log out, run
  `loginctl enable-linger $USER`.
- **Red banner "fetch failed"**: District may be rate-limiting you. Raise
  `interval_seconds`. The checker also backs off on its own.
- The computer must be awake for checks to run.

## License

MIT, see `LICENSE`.
