# TV Tracker

A terminal user interface (TUI) for tracking movies and shows. Search across [TMDB](https://www.themoviedb.org/) and [Jikan](https://jikan.moe/) (MyAnimeList), build a watch list, mark episodes and movies as watched, and sync fresh data — all from an interactive, keyboard-driven interface built with [Textual](https://textual.textualize.io/).

## Features

- **Interactive TUI** — tabbed interface with keyboard navigation, no command-line flags needed
- **Multi-source search** — query TMDB (movies & TV) and Jikan (anime) at the same time
- **Scroll-to-add** — search results appear in a scrollable table; press **Enter** on any result to add it to your tracking list
- **Episode-level tracking** — open any tracked show, browse seasons and episodes, mark individual episodes as watched/unwatched with a single key
- **Mark next episode** — press **n** to instantly mark the next unwatched episode as watched
- **Status management** — cycle through watch statuses (upcoming, planning, watching, completed, on hold, dropped) with a single key
- **Dashboard** — an at-a-glance view of currently watching, unwatched episodes
- **On-demand sync** — sync fresh data from the APIs with a single button press

## Requirements

- Python >= 3.13
- A free [TMDB API key](https://developer.themoviedb.org/docs) (Jikan requires no key)

## Installation

### Prerequisites

Install [uv](https://docs.astral.sh/uv/) (the package manager used below):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows (PowerShell):

```
irm https://astral.sh/uv/install.ps1 | iex
```

Also available via `pipx install uv`, `pip install uv`, or `brew install uv`.

### Install

Install the TUI globally as an editable tool so the `tv-tracker` command is
available from any directory, while source edits are picked up on the next
invocation — no reinstall needed:

```bash
uv tool install --editable .
```

Data (database, credentials) is stored in `~/.tv-tracker/`, independent of the
clone location. To remove the tool later: `uv tool uninstall tv-tracker`.

## Usage

Launch the TUI:

```bash
tv-tracker
```

### Tabs & Navigation

| Key       | Action                          |
|-----------|---------------------------------|
| `1`       | Switch to Shows tab             |
| `2`       | Switch to Movies tab            |
| `3`       | Switch to Search tab            |
| `4`       | Switch to Tracked tab           |
| `5`       | Switch to Config tab            |
| `q`       | Quit                            |
| `Tab`     | Cycle through tabs              |
| `Enter`   | Activate/Select row or button   |
| `Arrows`  | Navigate tables and lists       |

### Shows Tab

Shows stats summary, currently watching shows with progress bars, unwatched
episodes, and recently completed items. Press the **Sync & Check Alerts**
button to fetch fresh data from the APIs.

### Movies Tab

Shows movies that haven't been watched yet, with their status and date added.
Press the **Sync & Check Alerts** button to fetch fresh data from the APIs.

### Search Tab

1. Type a query in the search input and press **Enter** (or click **Search**)
2. Filter by type (All / Movies / Shows) using the dropdown
3. Scroll through results with arrow keys
4. Press **Enter** on any result to add it to your tracking list

### Tracked Tab

1. Filter by status using the dropdown (All / Planning / Watching / etc.)
2. Scroll through your tracked items
3. Press **Enter** to open the item detail screen
4. Press **w** to mark the next unwatched episode as watched
5. Press **W** to mark the whole show as watched (all episodes)
6. Press **r** to remove an item from your tracking list

### Item Detail Screen

Opened from the Tracked tab by pressing **Enter** on an item.

| Key       | Action                                    |
|-----------|-------------------------------------------|
| `Enter`   | Select a season to load its episodes      |
| `w` / `u` | Toggle watched/unwatched on an episode    |
| `W`       | Mark the whole show as watched (all episodes) |
| `n`       | Mark the next unwatched episode as watched |
| `s`       | Cycle watch status (planning -> watching -> ...) |
| `Esc`     | Go back to tracked list                   |

For **shows**: select a season from the seasons table to load its episodes.
Then select an episode and press **w** to mark it watched or **u** to unwatch.

For **movies**: press **w** to toggle watched status.

### Config Tab

Shows the status of your TMDB API key and access token. Use the **Set API Key**
and **Set Token** buttons to enter credentials (they'll be masked as you type).
Use **Clear** buttons to remove stored credentials.

The first time you use search, you'll be notified if no TMDB API key is set.
Open the Config tab (press **4**) to add it.

### Statuses

| Status      | Meaning                          |
|-------------|----------------------------------|
| `upcoming`  | Not yet released                 |
| `planning`  | Want to watch, haven't started   |
| `watching`  | Currently in progress            |
| `completed` | Finished                         |
| `on_hold`   | Paused                           |
| `dropped`   | Stopped watching                 |

## Configuration

TMDB credentials are stored in the local database. Use the Config tab to
view their status and update them. The following environment variables control
non-secret settings:

| Variable              | Default                          | Description                         |
|-----------------------|----------------------------------|-------------------------------------|
| `TMDB_BASE_URL`       | `https://api.themoviedb.org/3`   | TMDB API base URL                   |
| `JIKAN_BASE_URL`      | `https://api.jikan.moe/v4`       | Jikan API base URL                  |
| `TV_TRACKER_HOME`     | `~/.tv-tracker`                  | Data directory                      |
| `TV_TRACKER_DB_PATH`  | `~/.tv-tracker/tracker.db`       | SQLite database path                |

## Development

```bash
uv sync --group dev          # install dev dependencies
uv run ruff check .          # lint
uv run ruff format .         # format
uv run pyrefly check .       # type check
```

## Tech Stack

- **TUI**: [Textual](https://textual.textualize.io/) (built on [Rich](https://rich.readthedocs.io/))
- **HTTP**: [httpx](https://www.python-httpx.org/) (async with rate limiting & caching)
- **ORM**: [SQLAlchemy](https://www.sqlalchemy.org/) 2.x
- **Database**: SQLite (local, zero-config)
- **Package manager**: [uv](https://docs.astral.sh/uv/)
