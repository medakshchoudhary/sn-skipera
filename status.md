# SN-Skipera Status

## Current Scope
CLI tool that auto-completes ServiceNow NowLearning course videos by marking items complete via XHR API calls from within a real Zen browser session.

## ✅ Done / Working
- **Browser automation**: Selenium 4 + geckodriver 0.35 → Zen browser with Servicenow profile
- **Firefox wrapper**: `/tmp/zen-wrapper/firefox` tricks geckodriver into accepting Zen binary
- **Auth**: Okta SSO session in profile works — page loads authenticated
- **API discovery**: All REST endpoints return **200** when called via XHR from browser context
- **Item discovery**: Course items extracted from AngularJS scope (`c.data.items`) — **62 items found**
- **Item completion**: XHR-based API calls to `/lxp/rest/learning-course/complete-item` etc. — **~92% success rate**
- **SCORM fallback**: Not working (`None` returned) — these aren't SCORM items
- **Key discovery**: `XMLHttpRequest` with `withCredentials=true` works for API calls; `fetch()` crashes in Selenium sandbox

## Latest Test Run (01:44, Jul 20)
- 36/62 items processed in ~5 min
- 33 succeeded, 3 failed (likely groups/modules, not content items)
- ETA for all 62 items: ~9 minutes
- `_angular_scope_items` successfully extracts items from Angular scope

## Blocked / Pending
- 3 items failed — might be groups (parent items) that don't have a completion API
- Need to verify if failed items are actually course structure groups
- `_fetch_course_items` (XHR-based) should be tested as fallback for item discovery
- `_selenium_find_items` is probably obsolete (items aren't in DOM as `<a>` links)

## Next Steps
1. Wait for full completion run and note final success/fail counts
2. Verify on ServiceNow portal that items actually show as completed
3. Speed optimization: reduce per-item time further
4. Handle groups vs content items — skip items that are groups/modules
5. Clean up dead code (`_selenium_find_items`, `_scorm_complete_js`, unused `_fetch_course_items` parts)

## Key Files
- `src/sn_skipera/__main__.py` — main source (721 lines)
- `pyproject.toml` — deps: httpx, click, loguru, selenium
- `~/.sn-skipera/config.json` — saved config
- `~/.sn-skipera/debug_*.json/html` — debug output
- `/tmp/zen-wrapper/firefox` — geckodriver compatibility wrapper
- `knowledge.md` — technical findings and architecture
