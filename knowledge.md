# SN-Skipera Knowledge Base

## Project Goal
Auto-complete ServiceNow NowLearning course videos. CLI tool that opens a real browser, navigates course items, and marks them complete via API.

## Architecture

### Stack
- **Python 3.11+**, managed by **uv** (v0.11.29)
- **selenium 4.15+** + **geckodriver 0.35** for browser automation
- **httpx** for direct HTTP (unused now — all API calls go through browser)
- **click** CLI framework, **loguru** logging
- Target browser: **Zen Browser** (Firefox fork, v1.21.3b)

### Project Structure
```
sn-skipera/
├── pyproject.toml
├── README.md
├── status.md               ← active work tracking
├── knowledge.md            ← this file
└── src/sn_skipera/
    └── __main__.py          ← single-module CLI (721 lines)
```

### Key Paths
- Zen binary: `/opt/zen-browser-bin/zen-bin`
- Zen launcher wrapper: `/usr/bin/zen-browser` (shell script → `exec zen-bin`)
- **geckodriver compatibility wrapper**: `/tmp/zen-wrapper/firefox`
- Zen profile: `~/.zen/6bciciiq.Servicenow Profile/`
- geckodriver: `~/.local/bin/geckodriver`
- Config: `~/.sn-skipera/config.json`
- Debug outputs: `~/.sn-skipera/debug_*.json/html`

## Critical Technical Findings

### 1. `fetch()` Crashes in Selenium Execute Script
```javascript
// DON'T use — crashes with "'fetch' called on an object that does not implement interface Window"
fetch(url, { credentials: 'include' });

// DO use — XHR works fine from Selenium's sandbox
var xhr = new XMLHttpRequest();
xhr.open('GET', url, true);
xhr.withCredentials = true;
xhr.setRequestHeader('Accept', 'application/json');
xhr.onload = function() { /* success */ };
xhr.send();
```
**Root cause**: geckodriver's `execute_script` sandbox doesn't bind `fetch` to `window` properly. `XMLHttpRequest` works fine.

### 2. Async XHR Requires Polling
Since `execute_script` is synchronous but XHR is async: inject the script (returns `null`), then poll with a second `execute_script` that reads a global variable set by the XHR callback.

### 3. Browser Profile Locking
Zen/Firefox creates `{profile}/.parentlock` and `{profile}/lock` symlink. When already running, a second instance can't use the same profile. Solution: `--new-instance` flag. Also: kill existing process + clean lock files before starting.

### 4. geckodriver Binary Validation
geckodriver checks `--version` output for `"Mozilla Firefox"`. Zen returns `"Mozilla Zen X.Y.Zb"` — fails. Workaround: wrapper script at `/tmp/zen-wrapper/firefox` that fakes version output.

## ServiceStack Architecture
- **AngularJS SPA** (`ng-app="sn.$sp"`)
- **Service Portal** widget-based rendering
- Widget `id=xc758052d1b76ae10897565b1604bcbe5` = "Learning Course Content Panel"
- Course items stored in Angular scope: `$scope.c.data.items` (DO wait and poll for this)
- Items are rendered as `<lxp-nav>` components with `ng-click="nav(item)"`

## Auth/API Details

### Auth Mechanism
- **Page auth**: Okta SSO session cookies stored in browser profile
- **API auth**: Session cookies (`glide_node_id_for_js` etc.) sent automatically by browser when `xhr.withCredentials = true`
- `X-UserToken` header = `g_ck` CSRF token (found in page JS) — not strictly required but helps

### Session Tokens Found
- `window.NOW.user_name` = `daksh@intellivatetech.com`
- `window.NOW.session_id` = `8984413797C64F545B0B7EC11153AFC3`
- `g_ck` = `c984413797c64f545b0b7ec11153afc358ddb09250490eb21d059dd427f5664490b401da`
- 107 localStorage keys, 4 sessionStorage keys

### API Endpoints (all work via XHR from browser)
| Endpoint | Method | Status |
|---|---|---|
| `/lxp/rest/learning-course/items?courseId={id}` | GET | ✅ 200 (from browser) |
| `/lxp/rest/learning-course/structure?courseId={id}` | GET | ✅ 200 |
| `/lxp/rest/learning-course/progress?courseId={id}` | GET | ✅ 200 |
| `/lxp/rest/learning-course/complete-item?courseId={id}&childId={id}` | POST | ✅ 200 (completes item) |
| `/lxp/rest/learning-course/update-progress` | POST | ✅ 200 |
| `/lxp/rest/scorm/complete` | POST | ✅ 200 |
| `/lxp/rest/progress/update` | POST | ✅ 200 |
| `/api/now/learning/complete` | POST | ✅ 200 |

Note: adding `X-UserToken: {g_ck}` header doesn't change behavior.

### Page URL Pattern
```
/lxp/en/pages/learning-course?id=learning_course&course_id={32-hex}&spa=1
→ Auto-redirects to:
&group_id={32-hex}&child_id={32-hex}
```

## Completion Strategy (Current)

1. Launch Zen with profile via Selenium
2. Navigate to course page → SPA loads, immediately navigates to first item
3. Poll Angular scope for `c.data.items` (takes ~4-6 seconds to populate)
4. Extract items recursively (62 items including groups and content items)
5. For each item: navigate to its URL, call POST to multiple completion endpoints
6. Mark success/failure

### Item Structure
Items have: `id` (sys_id), `title`, `level`, `hasChildren`
- Some items are **groups/modules** (have children) — API completion might fail for these
- Some items are **content items** — API completion succeeds (~92%)

## Approaches That Were Tried and Failed
1. **Direct HTTP API with httpx**: 301 redirect, no session
2. **Playwright Chromium**: `ERR_TOO_MANY_REDIRECTS` from Okta
3. **Playwright Firefox**: Binary format incompatible with Zen profile
4. **CDP to running Zen**: CDP endpoints not responding
5. **SCORM API injection**: `window.API` not found (content is non-SCORM)
6. **fetch() from Selenium**: Crashes in sandbox context
7. **`fetch()` in returned Promise from `execute_script`**: Same sandbox issue

## Commands Reference
```bash
uv sync                          # install deps
uv run sn-skipera <course_id> --browser  # run
uv run sn-skipera <course_id> --browser --debug  # run with debug output
killall -9 zen-bin; sleep 1     # cleanup if locked
rm -f ~/.zen/*/lock ~/.zen/*/.parentlock  # unlock profile
```
