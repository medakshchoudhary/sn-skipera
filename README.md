# SN-Skipera

Auto-complete ServiceNow NowLearning course videos via browser automation.

Opens your Firefox/Zen browser (with your existing Okta session), plays each course video to completion, and moves to the next item. Handles SSO re-authentication automatically.

## What it does

- **Videos** — Detects `<video-js>` Brightcove players, seeks to the last 15 seconds, plays to end, waits 5s for server confirmation.
- **Knowledge checks** — Same video-based approach (these are also video items).
- **Quizzes** — If you provide a Gemini API key, the tool can answer quiz questions automatically via `--gemini-key`.
- **Labs** — Lab exercises are not automated (hands-on instance work). The tool skips non-video items gracefully.

Items are completed sequentially (prerequisite-locked items unlock automatically as earlier items finish).

## Prerequisites

- **Python 3.10+**
- **Firefox** (or Zen Browser, any Firefox-based browser)
- **geckodriver** — [Download](https://github.com/mozilla/geckodriver/releases) and place in your `PATH`
- **An active Okta session in your browser profile** — You must be logged into `learning.servicenow.com` via Okta in your browser before running the tool. The tool reuses your existing session; it does not handle login from scratch.

## Install

From GitHub:

```bash
pip install git+https://github.com/YOUR_USER/sn-skipera.git

# Or with uv:
uv tool install git+https://github.com/YOUR_USER/sn-skipera.git
```

From source:

```bash
git clone https://github.com/YOUR_USER/sn-skipera.git
cd sn-skipera
uv tool install .
```

## Usage

### Quick start

```bash
sn-skipera "https://learning.servicenow.com/lxp/en/pages/learning-course?id=learning_course&course_id=..." --browser
```

1. Copy the full course URL from your browser's address bar.
2. Run the command above.
3. The tool opens your browser, navigates to the course, and starts completing items one by one.

### Headless mode (no visible window)

```bash
sn-skipera "COURSE_URL" --browser --headless
```

### Answer quizzes with Gemini

```bash
# Save your API key once:
sn-skipera --set-gemini-key

# Then run normally — quizzes are answered automatically:
sn-skipera "COURSE_URL" --browser
```

Or pass the key each time:

```bash
sn-skipera "COURSE_URL" --browser --gemini-key "YOUR_KEY"
```

Or set `GEMINI_API_KEY` environment variable.

### Specify a browser profile

```bash
sn-skipera "COURSE_URL" --browser --profile /path/to/profile
```

Or set `SN_PROFILE_PATH` environment variable.

### Debug mode

```bash
sn-skipera "COURSE_URL" --browser --debug
```

Saves page source and state dumps to `~/.sn-skipera/` for troubleshooting.

## Configuration

| Environment variable | Purpose |
|---|---|
| `SN_BROWSER_BINARY` | Path to Firefox/Zen browser binary |
| `SN_GECKODRIVER_PATH` | Path to geckodriver executable |
| `SN_PROFILE_PATH` | Path to browser profile directory |
| `GEMINI_API_KEY` | Gemini API key for quiz answering |

## How it works

1. Launches Firefox/Zen with your browser profile (preserves Okta SSO session).
2. Loads the course page and extracts all items from the Angular scope.
3. For each incomplete item:
   - If it's a video: seeks to `duration - 15s`, plays muted to the end, waits 5s.
   - If it's a quiz and Gemini key is available: scrapes questions, calls Gemini API, clicks answers, submits.
   - Otherwise: skipped (labs, etc.).
4. Refreshes the course page to unlock subsequent items.
5. Repeats until all items are complete.

## Limitations

- **Labs are not automated.** The tool skips lab items (they require hands-on interaction in a ServiceNow instance).
- **Only works with ServiceNow NowLearning** (`learning.servicenow.com`).
- **You must be logged in via Okta before running.** The tool reuses your existing session.
- **Quiz support is experimental.** Works best with multiple-choice and true/false questions.
