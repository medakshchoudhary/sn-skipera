# SN-Skipera

Auto-complete ServiceNow NowLearning course videos via browser automation.

Opens your Firefox/Zen browser (with your existing Okta session), plays each course video to completion, and moves to the next item. Handles SSO re-authentication automatically.

## What it does

- **Videos** — Detects `<video-js>` Brightcove players, seeks to the last 15 seconds, plays to end, waits 5s for server confirmation.
- **Knowledge checks** — Same video-based approach (these are also video items).
- **Labs** — Handled as video items; the tool plays through the video instruction. (Lab exercises themselves are not automated — currently a "watch and learn" walkthrough only.)

Items are completed sequentially (prerequisite-locked items unlock automatically as earlier items finish).

## Requirements

- **Python 3.10+**
- **Firefox** (or Zen Browser, or any Firefox-based browser)
- **geckodriver** — [Download](https://github.com/mozilla/geckodriver/releases) and place in your `PATH`

## Install

```bash
pip install sn-skipera
# or from source:
uv tool install .
```

## Usage

### Quick start (browser automation — recommended)

```bash
sn-skipera "https://learning.servicenow.com/lxp/en/pages/learning-course?id=learning_course&course_id=..." --browser
```

This opens your default Firefox profile, navigates to the course, and starts completing items. Okta SSO re-authenticates automatically using your saved session.

### Headless mode

```bash
sn-skipera "COURSE_URL" --browser --headless
```

Runs without a visible window. Useful for servers or background execution.

### Specify a browser profile

```bash
sn-skipera "COURSE_URL" --browser --profile /path/to/profile
```

Or set the `SN_PROFILE_PATH` environment variable.

### Debug mode

```bash
sn-skipera "COURSE_URL" --browser --debug
```

Saves page source and state dumps to `~/.sn-skipera/` for troubleshooting.

### Getting the course URL

Open the course in your browser and copy the full URL. It should look like:

```
https://learning.servicenow.com/lxp/en/pages/learning-course?id=learning_course&course_id=5b19953497ffaa90e7b47b70f053af31&spa=1
```

## How it works

1. Launches Firefox/Zen with your browser profile (preserves Okta SSO session).
2. Loads the course page and extracts all items from the Angular scope.
3. For each incomplete item:
   - Navigates to the item URL.
   - Waits for the Brightcove `<video-js>` player to load (up to 20s).
   - Seeks to `duration - 15s` and plays muted.
   - Polls for `video.ended` or `currentTime >= duration - 1s`.
   - Waits 5s for the server to register completion.
   - Refreshes the course page to unlock subsequent items.
4. Repeats until all items are complete.

## Configuration

| Environment variable | Purpose |
|---|---|
| `SN_BROWSER_BINARY` | Path to Firefox/Zen browser binary |
| `SN_GECKODRIVER_PATH` | Path to geckodriver executable |
| `SN_PROFILE_PATH` | Path to browser profile directory |

## Limitations

- **Labs are not automated.** Lab exercises require hands-on interaction in a ServiceNow instance. The tool plays through lab instruction videos only.
- **HTTP-only mode (`--set-cookies` + `--discover`)** is legacy and rarely works — the XHR completion API does not reliably persist. Use `--browser`.
- Only works with **ServiceNow NowLearning** (`learning.servicenow.com`).
