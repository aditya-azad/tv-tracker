# TV Tracker

A command-line tool for tracking movies and shows from the terminal. Search across [TMDB](https://www.themoviedb.org/) and [Jikan](https://jikan.moe/) (MyAnimeList), build a watch list, mark episodes and movies as watched, and get alerts when new episodes are available.

## Features

- **Multi-source search** — query TMDB (movies & TV) and Jikan (anime) at the same time
- **Tracking** — add movies and shows to a local watch list with statuses: *planning*, *watching*, *completed*, *on hold*, *dropped*
- **Episode-level tracking** — mark individual episodes as watched and see progress per show
- **Dashboard** — an at-a-glance view of currently watching, unwatched episodes, and recently completed items
- **On-demand sync** — the `alerts` command syncs fresh data from the APIs, then lists shows with unwatched episodes

## Requirements

- Python ≥ 3.13
- A free [TMDB API key](https://developer.themoviedb.org/docs) (Jikan requires no key)

## Installation

```bash
git clone <repo-url> && cd tv-tracker
uv sync
```

## Usage

```bash
tv-tracker                                      # open dashboard (cached data, no sync)
tv-tracker search "breaking bad"                # search TMDB & Jikan
tv-tracker search "cowboy bebop" --type show    # filter by type
tv-tracker details tmdb 1396                     # view title details
tv-tracker details tmdb 1396 --season 1          # list episodes in a season
tv-tracker add tmdb 1396                         # add to tracking list
tv-tracker list                                  # list all tracked items
tv-tracker list --status watching                # filter by status
tv-tracker status 1 watching                     # update watch status
tv-tracker watch 1                               # mark a movie as watched
tv-tracker watch 1 --season 1 --episode 1        # mark an episode as watched
tv-tracker unwatch 1 --season 1 --episode 1      # remove a watched mark
tv-tracker remove 1                              # remove from tracking list
tv-tracker alerts                                # sync, then list unwatched episodes
tv-tracker config                                # show TMDB credential status
tv-tracker config --set-key                      # set or update the TMDB API key
tv-tracker config --set-token                    # set or update the TMDB access token
```

The first time you run a command that needs TMDB (search, details, add, alerts),
you will be prompted to enter your TMDB API key. The key is stored in the local
database so you only need to enter it once. Use `tv-tracker config --set-key` to
update it later.

### Statuses

| Status      | Meaning                          |
|-------------|----------------------------------|
| `planning`  | Want to watch, haven't started   |
| `watching`  | Currently in progress            |
| `completed` | Finished                         |
| `on_hold`   | Paused                           |
| `dropped`   | Stopped watching                 |

## Configuration

TMDB credentials are stored in the local database. Use `tv-tracker config` to
view their status, and `tv-tracker config --set-key` / `--set-token` to update
them. The following environment variables control non-secret settings:

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

- **CLI**: [Typer](https://typer.tiangolo.com/) + [Rich](https://rich.readthedocs.io/)
- **HTTP**: [httpx](https://www.python-httpx.org/) (async with rate limiting & caching)
- **ORM**: [SQLAlchemy](https://www.sqlalchemy.org/) 2.x
- **Database**: SQLite (local, zero-config)
- **Package manager**: [uv](https://docs.astral.sh/uv/)
