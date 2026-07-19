# SN-Skipera

CLI tool to auto-complete ServiceNow NowLearning videos. Like [skipera](https://github.com/serv0id/skipera) but for ServiceNow.

## Setup

```bash
cd sn-skipera
uv sync
uv run playwright install chromium
uv tool install .
```

## Usage

```bash
sn-skipera servicenow-administration-fundamentals-on-demand
```

Or with course_id / full URL:

```bash
sn-skipera 5b19953497ffaa90e7b47b70f053af31
sn-skipera "https://learning.servicenow.com/lxp/en/now-platform/servicenow-administration-fundamentals-on-demand?course_id=5b19953497ffaa90e7b47b70f053af31"
```

First run opens a browser — log into ServiceNow once. Session is saved for next time.

```bash
sn-skipera 5b19953497ffaa90e7b47b70f053af31 --headless   # after session saved
sn-skipera --clear-session                                # reset login
```

## How it works

Opens a Playwright browser, navigates each course item, and calls `window.API.LMSSetValue("cmi.core.lesson_status", "completed")` directly in the page context — same as watching the full video.
