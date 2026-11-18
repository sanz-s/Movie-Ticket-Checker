# Movie Ticket Checker

Watches **District** and **TicketNew** for a movie's shows and sends you a
**desktop notification** the moment tickets open at the theatres you care about.
It also runs a local **dashboard** with the shows, seat counts and the actual
seat map of each show.

Works on **macOS** and **Linux**. Pure Python standard library: no `pip install`.

> District and TicketNew sell the same seats (every District cinema is tagged
> `ticketnew`), so one check covers both. BookMyShow blocks automated requests
> and is not supported.

## Features

- Checks every N seconds (default 15s) and notifies you when a matching show
  gets seats, or when a sold-out show gets seats back
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
- Background service that starts at login (LaunchAgent on macOS, systemd user
  service on Linux)

## Requirements

- Python **3.8+**
- macOS, or Linux with a desktop session
  - Linux notifications need `notify-send` (`sudo apt install libnotify-bin`,
    `sudo dnf install libnotify`, `sudo pacman -S libnotify`)
  - Linux background service needs systemd (most distros)

## Install

```bash
git clone https://github.com/sanz-s/Movie-Ticket-Checker.git
cd Movie-Ticket-Checker
./install.sh
```

Then open **http://127.0.0.1:8765** and click the bell icon to send a test
notification.

`install.sh` copies the app to the data folder and starts it as a service:

| | macOS | Linux |
|---|---|---|
| App + data | `~/Library/Application Support/doomsday-tracker/` | `~/.local/share/doomsday-tracker/` |
| Service | `~/Library/LaunchAgents/com.user.doomsday-tracker.plist` | `~/.config/systemd/user/doomsday-tracker.service` |
| Logs | `~/Library/Logs/doomsday-tracker.log` | `journalctl --user -u doomsday-tracker -f` |

Options:

```bash
PORT=9000 ./install.sh        # different dashboard port
./install.sh --uninstall      # stop + remove the service (keeps your config)
```

After editing any `.py` / `.html` file, run `./install.sh` again to update the
running copy.

### Run without installing

```bash
python3 tracker_server.py                 # dashboard + checks every 15s
python3 district_tracker.py --dry-run     # one check, print matching shows
python3 district_tracker.py --dry-run --all   # every show, no filters
python3 district_tracker.py --test-notify # test the desktop notification
```

## Configuration

Use the Settings panel in the dashboard, or edit `config.json` in the
data folder. It is created with defaults on first run.

| Key | Meaning | Default |
|---|---|---|
| `movie_name` | name used in notifications | `Avengers: Doomsday` |
| `movie_url` | District movie page (`…-tickets-in-<city>-MV<id>`) | Doomsday, Chennai |
| `language` | show language | `English` |
| `window_start` / `window_end` | show start-time window (24h `HH:MM`) | `10:00` / `16:00` |
| `min_seats` | alert only with at least this many free seats | `1` |
| `front_classes` | front-row seat classes (shown yellow when only these are left) | `budget, classic, normal` |
| `theatres` | theatres to alert on (`id` = District cinema id) | 6 Chennai theatres |
| `sites` | `district` / `ticketnew` on or off (`bookmyshow` unsupported) | both on |
| `open_links_on` | site a click opens | `district` |
| `interval_seconds` | seconds between checks (min 10) | `15` |
| `district_access_token` | optional, see *Seat map* below | empty |

To track another movie or city, paste its District movie URL into Settings and
pick theatres from the list (the list shows Chennai cinemas).

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

## Files

| File | What it does |
|---|---|
| `district_tracker.py` | fetch + parse District showtimes, filters, alerts, config, notifications |
| `tracker_server.py` | background checker + dashboard web server (`127.0.0.1` only) |
| `seatmap.py` | read-only seat-map fetch for one show |
| `tracker_ui.html` | the dashboard |
| `install.sh` | install / update / uninstall the service (macOS + Linux) |

## Notes and troubleshooting

- **Times** are shown in India time (District's raw data is UTC).
- **No notification on macOS?** Notifications come from *Script Editor*: allow it
  in System Settings → Notifications, and check Focus / Do Not Disturb.
- **No notification on Linux?** Install `notify-send`. If the service started
  before your desktop session, run `./install.sh` again from a terminal inside
  the session. To keep it running after you log out, run
  `loginctl enable-linger $USER`.
- **Red banner "fetch failed"**: District may be rate-limiting you. Raise
  `interval_seconds`. The checker also backs off on its own.
- The computer must be awake for checks to run.

## License

MIT, see `LICENSE`.
