# Learnings & Technical Notes

This document captures technical findings about ServiceNow NowLearning's internals for anyone who wants to contribute or understand how the tool works.

## Course Item States

Items in the Angular scope (`$scope.c.data.items`) have these fields:

| Field | Description |
|---|---|
| `child_sys_id` | 32-char hex item ID |
| `child_name` | Item title |
| `state` | Student completion: `completed`, `in_progress`, `not_started` |
| `child_state` | Content publication: `published` (never use for completion) |
| `_url` | Navigation URL (starts with `&`, needs base path prepended) |
| `_allowNavigation` | Boolean — `false` means locked by prerequisite |
| `_hasChildren` | `true` for lesson containers (folders) |
| `child_type` / `_type` | Content type: `video`, `html` |
| `order` | Item order (string, must be parsed to int) |
| `_percent_complete` | Number 0-100 |

## Item Locking

Items are **sequentially locked** by prerequisites. You cannot navigate to item N+1 until item N is completed. The SPA enforces this via `_allowNavigation: false`. After completing an item:
1. Refresh the course page.
2. The SPA re-fetches progress from the server.
3. Previously locked items become navigable (`_allowNavigation: true`).

## Video Player

ServiceNow uses **Brightcove** via the `<video-js>` custom element (a web component, no shadow DOM):

- `<video-js>` contains a `<video>` as a child element.
- The `<video>` element is created **~7-15 seconds** after page load (asynchronous initialization).
- Seeking to `duration - 15` and playing (muted) reliably triggers the `ended` event.
- The player must be muted (`vid.muted = true`) before calling `vid.play()` to avoid autoplay restrictions.
- After the video ends, the server needs ~5 seconds to register the completion.

Detection query: `document.querySelector('video-js')` then `vjs.querySelector('video')`.

## SPA Routing

The course page uses ServiceNow's **Next Experience** framework (`nowUiFramework`). Key observations:

- Old URL: `/lxp/en/pages/learning-course?id=learning_course&course_id=X`
- Sometimes redirects to `/now/lxp/home?id=learning_course&course_id=X`
- When the session expires, the SPA redirects to `home` route instead of the course.
- After SSO re-authentication, re-navigating to the original URL loads the course correctly.

The initial route payload is in `window.__initialRoutePayload`:
```json
{"route": "home", "path": "now/lxp", ...}
```

The `g_ck` token (X-UserToken for API calls) is set after page load in a script:
```html
<script>window.g_ck = '...';</script>
```

## API Behavior

- XHR POSTs to `/lxp/rest/learning-course/complete-item` return **200 with HTML** but **do not persist completion**.
- The real completion mechanism is the Brightcove video player — it sends its own analytics/completion events.
- Button clicks ("Mark Complete") work temporarily but also don't persist reliably.
- The `/lxp/rest/learning-course/items` endpoint returns the correct item list.

## Browser Automation

- **Selenium 4+** with Firefox geckodriver.
- Profile-based login preserves Okta SSO session cookies.
- Stale lock files (`lock`, `.parentlock`) must be cleaned before launch.
- WAL files (`.sqlite-wal`) can cause `NS_ERROR_STORAGE_BUSY` errors.

### Profile Safety

**Critical:** geckodriver modifies a profile's `compatibility.ini`, `prefs.js`, cache, and other metadata when it opens a profile. This causes Firefox/Zen to reject the profile with the error "You've launched an older version of Zen Browser."

**Architecture:** sn-skipera **always copies** the user's profile to a temp directory (`tempfile.mkdtemp`) and launches the browser from the copy. The original profile is never touched by geckodriver.

- Full recursive copy with `shutil.copytree(symlinks=True, ignore_dangling_symlinks=True)`.
- Stale lock files are cleaned from the copy only.
- Temp directory is deleted in `finally` via `shutil.rmtree`.
- If the copy fails, the error is silently skipped per-file — partial copies are sufficient for login.

Do **not** revert to launching the original profile directly. If launch fails, log the error and exit — do not fall back to the original.

## Quiz Detection

Quiz pages contain:
- Radio buttons (`input[type=radio]`) or checkboxes for multiple-choice.
- Containers with class `question`, `.quiz-question`, `fieldset`, etc.
- Question text in `label b`, `legend`, `h3/h4`, `.question-text`.
- Submit/check buttons with text "Submit", "Check Answer", "Finish".

The Gemini call uses a simple prompt asking for exact answer text matching.
