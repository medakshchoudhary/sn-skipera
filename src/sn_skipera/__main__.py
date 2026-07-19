#!/usr/bin/env python3
"""
SN-Skipera — ServiceNow video auto-complete CLI.
sn-skipera <course_id_or_full_url>
"""
import json, re, sys, time, subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import click, httpx
from loguru import logger

CONFIG_DIR = Path.home() / ".sn-skipera"
CONFIG_FILE = CONFIG_DIR / "config.json"
BASE = "https://learning.servicenow.com"

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/json,*/*"}

def load_config():
    cfg = {"cookies": {}, "endpoints": []}
    if CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    return cfg

def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

def parse_cookie_string(raw):
    result = {}
    for pair in re.split(r'[;\n]+', raw):
        pair = pair.strip()
        if not pair: continue
        m = re.match(r'^"?([A-Za-z0-9_-]+)"?\s*[:=]\s*"?([^";\n]+)?"?$', pair)
        if m: result[m.group(1)] = m.group(2).strip('"').strip("'")
    return result

def prompt_cookies():
    click.echo("\nRequired cookie for learning.servicenow.com:")
    click.echo("  1. JSESSIONID\n")
    result = {}
    raw = click.prompt("  JSESSIONID", default="")
    if raw:
        parsed = parse_cookie_string(raw)
        if parsed: result.update(parsed)
        else: result["JSESSIONID"] = raw.strip('"').strip("'")
    if click.confirm("Add other cookies?", default=False):
        extra = click.prompt("Paste extras (name=value; ...)", default="")
        result.update(parse_cookie_string(extra))
    return result

def make_session(cfg):
    s = httpx.Client(timeout=30, follow_redirects=True, verify=False)
    s.headers.update(HEADERS)
    if cfg.get("cookies"):
        # Use Cookie header directly to avoid httpx domain/path matching issues
        cookie_str = "; ".join(f"{k}={v}" for k, v in cfg["cookies"].items())
        s.headers["Cookie"] = cookie_str
    return s

def build_content_url(course_id, group_id=None, child_id=None):
    url = f"{BASE}/lxp/en/pages/learning-course?id=learning_course&course_id={course_id}&spa=1"
    if group_id: url += f"&group_id={group_id}"
    if child_id: url += f"&child_id={child_id}"
    return url

def extract_course_id(val):
    m = re.search(r'course_id=([a-f0-9]{32})', val)
    if m: return m.group(1)
    if re.match(r'^[a-f0-9]{32}$', val): return val
    return None

def discover_endpoints(session, base_url):
    logger.info("Discovering API endpoints...")
    patterns = [
        "/lxp/rest/learning-course/items", "/lxp/rest/learning-course/structure",
        "/lxp/rest/learning-course/progress", "/lxp/rest/learning-course/complete-item",
        "/lxp/rest/learning-course/update-progress", "/lxp/rest/scorm/complete",
        "/lxp/rest/progress/update", "/api/now/learning/progress", "/api/now/learning/complete",
    ]
    found = []
    for p in patterns:
        try:
            r = session.options(f"{BASE}{p}")
            if r.status_code < 500: found.append(f"{BASE}{p}"); logger.debug(f"  ✓ {p}")
        except: pass
    return found

def get_items_via_api(session, cid):
    for ep in [f"{BASE}/lxp/rest/learning-course/items?courseId={cid}",
               f"{BASE}/lxp/rest/learning-course/structure?courseId={cid}"]:
        try:
            r = session.get(ep, follow_redirects=False)
            if r.status_code in (200, 201):
                data = r.json()
                for key in (["items","result","data","elements","records"] if isinstance(data,dict) else [""]):
                    sub = data.get(key,[]) if isinstance(data,dict) else data
                    if isinstance(sub,list):
                        items = []
                        for x in sub:
                            iid = x.get("id") or x.get("childId") or x.get("sys_id")
                            if iid: items.append({"id":iid,"title":x.get("title") or x.get("name","")})
                        if items: return items
        except: pass
    return []

def complete_via_api(session, cid, child_id, endpoints):
    if not endpoints: return False
    payload = {"courseId":cid,"childId":child_id,"status":"completed","progress":100,"score":100}
    for ep in endpoints:
        try:
            r = session.post(ep, json=payload, headers={"Content-Type":"application/json"})
            if r.status_code in (200,201,204): return True
        except: pass
    return False

LOGIN_TIMEOUT = 600

ZEN_BINARY = "/tmp/zen-wrapper/firefox"
ZEN_PROFILE_PATH = Path.home() / ".zen" / "6bciciiq.Servicenow Profile"
GECKODRIVER_PATH = Path.home() / ".local" / "bin" / "geckodriver"


def _get_zen_binary():
    wrapper = Path("/tmp/zen-wrapper/firefox")
    if wrapper.exists():
        return str(wrapper)
    if Path(ZEN_BINARY).exists():
        return ZEN_BINARY
    for c in ("zen-browser", "zen", "firefox"):
        try:
            fb = subprocess.run(["which", c], capture_output=True, text=True).stdout.strip()
            if fb:
                return fb
        except Exception:
            continue
    return None


def browser_login(course_id, headless, delay, debug=False):
    """Launch Zen browser with your profile (has Okta session), auto-complete items."""
    zen = _get_zen_binary()
    if not zen:
        logger.error("Zen/Firefox binary not found.")
        return 0, 0

    if not GECKODRIVER_PATH.exists():
        logger.error(f"geckodriver not found at {GECKODRIVER_PATH}")
        logger.error("Install: wget ... && tar xzf ... && cp geckodriver ~/.local/bin/")
        return 0, 0

    try:
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service
    except ImportError:
        logger.error("selenium not installed. Run: pip install selenium")
        return 0, 0

    click.echo("\n" + "=" * 60)
    click.echo("  Opening Zen browser with your Servicenow profile.")
    click.echo("  (You should already be logged in — Okta SSO will")
    click.echo("   complete silently using your saved session.)")
    click.echo("=" * 60 + "\n")

    options = Options()
    options.binary_location = zen
    options.add_argument("-profile")
    options.add_argument(str(ZEN_PROFILE_PATH))
    if headless:
        options.add_argument("-headless")

    service = Service(str(GECKODRIVER_PATH))
    driver = webdriver.Firefox(service=service, options=options)

    try:
        # --- COURSE PAGE ---
        course_url = build_content_url(course_id)
        logger.info(f"Loading course page: {course_url}")
        driver.get(course_url)
        time.sleep(12)

        # Check if we need to wait for login
        if _selenium_needs_login(driver):
            logger.info("Okta SSO redirecting... waiting for auto-login.")
            for _ in range(120):
                time.sleep(2)
                if not _selenium_needs_login(driver):
                    logger.success("Auto-login via existing Okta session!")
                    break

        # --- DEBUG: SAVE PAGE SOURCE AND STATE ---
        if debug:
            src = driver.page_source
            (CONFIG_DIR / "debug_page.html").write_text(src)
            logger.info(f"Saved page source ({len(src)} bytes)")

            # Safe state dump (avoid cyclic objects)
            state = driver.execute_script("""
                var gck = (typeof g_ck !== 'undefined' ? g_ck : null);
                try {
                    var cookies = document.cookie;
                } catch(e) { cookies = 'ERROR: ' + e.message; }
                try {
                    var lsLen = localStorage.length;
                } catch(e) { lsLen = -1; }
                try {
                    var ssLen = sessionStorage.length;
                } catch(e) { ssLen = -1; }
                return {
                    url: location.href,
                    cookies: cookies,
                    g_ck: gck,
                    now_user: (typeof NOW !== 'undefined' ? NOW.user_name : null),
                    now_session: (typeof NOW !== 'undefined' ? NOW.session_id : null),
                    localStorageKeys: lsLen,
                    sessionStorageKeys: ssLen,
                    angularPresent: (typeof angular !== 'undefined'),
                    pageTitle: document.title,
                    bodyTextLen: (document.body && document.body.innerText ? document.body.innerText.length : 0)
                };
            """)
            (CONFIG_DIR / "debug_state.json").write_text(json.dumps(state, indent=2))
            logger.info(f"Saved state → URL: {state.get('url')}")
            logger.info(f"  cookies: {state.get('cookies','')[:200]}")
            logger.info(f"  g_ck: {state.get('g_ck')}")
            logger.info(f"  user: {state.get('now_user')}")

            links = driver.execute_script("""
                var links = [];
                document.querySelectorAll('a').forEach(function(a) {
                    try {
                        links.push({
                            href: a.getAttribute('href'),
                            text: (a.textContent || '').trim().slice(0,100)
                        });
                    } catch(e) {}
                });
                return links;
            """)
            (CONFIG_DIR / "debug_links.json").write_text(json.dumps(links, indent=2))
            logger.info(f"Saved {len(links)} links")

        # --- PROBE APIs FROM BROWSER (using XHR, not fetch) ---
        probe_results = driver.execute_script("""
            var courseId = arguments[0];
            var endpoints = [
                '/lxp/rest/learning-course/items?courseId=' + courseId,
                '/lxp/rest/learning-course/structure?courseId=' + courseId,
                '/lxp/rest/learning-course/progress?courseId=' + courseId,
            ];
            var results = [];
            var done = 0;
            var gck = (typeof g_ck !== 'undefined' ? g_ck : null);

            function probe(url, extraHeaders) {
                var xhr = new XMLHttpRequest();
                xhr.open('GET', url, true);
                xhr.withCredentials = true;
                xhr.setRequestHeader('Accept', 'application/json');
                if (extraHeaders) {
                    for (var k in extraHeaders) xhr.setRequestHeader(k, extraHeaders[k]);
                }
                xhr.onload = function() {
                    results.push({
                        url: url + (extraHeaders && extraHeaders['X-UserToken'] ? ' (with X-UserToken)' : ''),
                        status: xhr.status,
                        body: (xhr.responseText || '').slice(0, 500)
                    });
                    done++;
                    if (done === endpoints.length + (gck ? 1 : 0)) window._sn_probeDone = results;
                };
                xhr.onerror = function() {
                    results.push({ url: url, status: 0, body: 'NETWORK_ERROR' });
                    done++;
                    if (done === endpoints.length + (gck ? 1 : 0)) window._sn_probeDone = results;
                };
                xhr.send();
            }

            endpoints.forEach(function(url) { probe(url, null); });
            if (gck) {
                probe('/lxp/rest/learning-course/items?courseId=' + courseId,
                      { 'X-UserToken': gck });
            }

            return null; // async, we'll poll for _sn_probeDone
        """, course_id)

        # Poll for probe results (up to 15s)
        for _ in range(30):
            time.sleep(0.5)
            probe_results = driver.execute_script("return window._sn_probeDone || null;")
            if probe_results:
                break

        if probe_results:
            logger.info("--- API Probe Results (from browser XHR) ---")
            for r in probe_results:
                status_str = str(r['status'])
                logger.info(f"  {status_str} {r['url']}")
                if r['status'] != 200:
                    snippet = (r.get('body') or '')[:300]
                    logger.info(f"    body: {snippet}")
            logger.info("------------------------------------------")
            if debug:
                (CONFIG_DIR / "debug_probe.json").write_text(json.dumps(probe_results, indent=2))
        else:
            logger.warning("API probe timed out")

        # --- SEQUENTIAL ITEM COMPLETION LOOP ---
        completed = failed = 0
        max_items = 200
        seen_ids = set()

        for _ in range(max_items):
            driver.execute_script("""
                document.querySelectorAll('[aria-expanded="false"], summary, .accordion-header:not(.active), [class*="collapsed"]').forEach(function(el) {
                    try { el.click(); } catch(e) {}
                });
            """)
            time.sleep(1)
            items = _angular_scope_items(driver, course_id)
            if not items:
                time.sleep(3)
                items = _angular_scope_items(driver, course_id)
            if not items:
                logger.warning("No items in scope, trying DOM fallback...")
                items = _selenium_find_items(driver)
            if not items:
                # One more attempt: hard refresh the course page
                logger.warning("Course page may be stale, navigating again...")
                driver.get(build_content_url(course_id))
                time.sleep(10)
                items = _angular_scope_items(driver, course_id)
            if not items:
                logger.warning("No items found at all.")
                break

            if debug:
                for it in items[:5]:
                    logger.debug(f"  {it['id'][:8]}... {it.get('title','?')[:40]} state={it.get('state','?')} allowNav={it.get('allowNavigation')} type={it.get('childType','?')}")

            target = None
            for it in items:
                sid = it["id"]
                state = it.get("state", "")
                allow = it.get("allowNavigation", True)
                has_children = it.get("hasChildren", False)

                if has_children:
                    continue
                if state in ("completed", "complete"):
                    continue
                if sid in seen_ids:
                    continue
                if allow:
                    target = it
                    break

            if not target:
                # Try locked items — maybe navigable via direct URL
                for it in items:
                    sid = it["id"]
                    state = it.get("state", "")
                    has_children = it.get("hasChildren", False)
                    if has_children or state in ("completed", "complete") or sid in seen_ids:
                        continue
                    target = it
                    break

            if not target:
                logger.success("All items completed!")
                break

            child_id = target["id"]
            title = target.get("title") or child_id
            child_type = target.get("childType", "")
            seen_ids.add(child_id)
            logger.info(f"[{completed+1}] {title} (state={target.get('state','?')}, type={child_type})")

            href = target.get("href")
            if href:
                if href.startswith("&"):
                    href = f"{BASE}/lxp/en/pages/learning-course?id=learning_course&spa=1{href}"
                elif not href.startswith("http"):
                    href = BASE + href
                driver.get(href)
                time.sleep(2)

            ok = False
            mark = ""

            # Try button click first (fast, works for most items)
            ok = _click_complete_button(driver)
            if ok:
                mark = "(btn)"
            elif child_type == "video":
                ok = _complete_via_video(driver)
                if ok:
                    mark = ""

            if not ok:
                time.sleep(3)
                ok = driver.execute_script("""
                    return document.querySelectorAll('[class*="completed" i], .sn-check-mark, .is-complete').length > 0;
                """)
                if ok:
                    mark = "(wait)"

            # Last resort: XHR API fallback (might process server-side despite HTML response)
            if not ok:
                ok = _complete_item_via_xhr(driver, course_id, child_id)
                if ok:
                    mark = "(api)"

            if ok:
                logger.success(f"  ✓ {mark}")
                completed += 1
            else:
                logger.warning("  ✗ failed")
                failed += 1

            time.sleep(delay)
            course_url = build_content_url(course_id)
            driver.get(course_url)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            driver.execute_script("window.scrollTo(0, 0);")
            # Wait for SPA to fully reload course items
            time.sleep(3)
            for _ in range(10):
                scope_check = driver.execute_script("""
                    var els = document.querySelectorAll('[data], [widget], [sn-atf-area]');
                    for (var wi = 0; wi < els.length; wi++) {
                        try {
                            var s = angular.element(els[wi]).scope();
                            if (!s) continue;
                            var p = s;
                            while (p) {
                                if (p.c && p.c.data && p.c.data.items) return true;
                                if (p['$parent'] === p) break;
                                p = p['$parent'];
                            }
                        } catch(e) {}
                    }
                    return false;
                """)
                if scope_check:
                    break
                time.sleep(1)

        logger.info(f"\nDone: {completed} completed, {failed} failed")
        logger.info("Closing browser...")
    finally:
        driver.quit()
    return completed, failed


def _selenium_needs_login(driver):
    try:
        url = driver.current_url.lower()
        if any(d in url for d in ["servicenow.com", "service-now.com"]) and not any(
            ind in url for ind in ["okta", "sso", "login", "signin"]
        ):
            return False
        body = driver.find_element("tag name", "body").text.lower()
        if "sign in" in body or "log in" in body:
            return True
    except Exception:
        pass
    return True


def _fetch_course_items(driver, course_id, debug=False):
    """Fetch course items via XHR from within the browser (has session auth)."""
    script = """
        var courseId = arguments[0];
        var urls = [
            '/lxp/rest/learning-course/items?courseId=' + courseId,
            '/lxp/rest/learning-course/structure?courseId=' + courseId,
        ];
        var items = [];
        var done = 0;
        var gck = (typeof g_ck !== 'undefined' ? g_ck : null);

        function tryXHR(url, extraHeaders) {
            var xhr = new XMLHttpRequest();
            xhr.open('GET', url, true);
            xhr.withCredentials = true;
            xhr.setRequestHeader('Accept', 'application/json');
            if (extraHeaders) {
                for (var k in extraHeaders) xhr.setRequestHeader(k, extraHeaders[k]);
            }
            xhr.onload = function() {
                if (xhr.status === 200) {
                    try {
                        var data = JSON.parse(xhr.responseText);
                        var list = data.items || data.result || data.data || data.elements || data.records || data;
                        if (Array.isArray(list)) {
                            list.forEach(function(x) {
                                if (typeof x === 'string') {
                                    items.push({ id: x, title: x });
                                } else {
                                    var id = x.id || x.childId || x.sys_id || x.content_id || '';
                                    var title = x.title || x.name || x.short_description || id;
                                    if (id) items.push({ id: id, title: title });
                                }
                            });
                        }
                    } catch(e) {}
                }
                done++;
                if (done === urls.length + (gck ? 1 : 0)) window._sn_fetchItemsDone = items;
            };
            xhr.onerror = function() {
                done++;
                if (done === urls.length + (gck ? 1 : 0)) window._sn_fetchItemsDone = items;
            };
            xhr.send();
        }

        urls.forEach(function(url) { tryXHR(url, null); });
        if (gck) {
            tryXHR('/lxp/rest/learning-course/items?courseId=' + courseId,
                   { 'X-UserToken': gck });
        }
        return null; // async
    """
    try:
        driver.execute_script(script, course_id)
        found = None
        for _ in range(30):
            time.sleep(0.5)
            found = driver.execute_script("return window._sn_fetchItemsDone || null;")
            if found is not None:
                break
        if found and len(found) > 0:
            for item in found:
                item["href"] = f"/lxp/en/pages/learning-course?id=learning_course&course_id={course_id}&child_id={item['id']}&spa=1"
            return found
    except Exception as e:
        logger.debug(f"Fetch course items failed: {e}")

    return []


def _complete_item_via_xhr(driver, course_id, child_id):
    """Mark course item as complete via XHR API from within the browser."""
    script = """
        var courseId = arguments[0];
        var childId = arguments[1];
        var gck = (typeof g_ck !== 'undefined' ? g_ck : null);
        var q = '?courseId=' + courseId + '&childId=' + childId;
        var urls = [
            '/lxp/rest/learning-course/complete-item' + q,
            '/lxp/rest/learning-course/update-progress' + q,
            '/lxp/rest/scorm/complete' + q,
            '/lxp/rest/progress/update' + q,
            '/api/now/learning/complete' + q,
        ];
        var payload = JSON.stringify({
            courseId: courseId,
            childId: childId,
            status: 'completed',
            progress: 100,
            score: 100
        });
        var done = 0;
        var success = false;

        function tryUrl(url) {
            var xhr = new XMLHttpRequest();
            xhr.open('POST', url, true);
            xhr.withCredentials = true;
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.setRequestHeader('Accept', 'application/json');
            if (gck) xhr.setRequestHeader('X-UserToken', gck);
            xhr.onload = function() {
                if (xhr.status >= 200 && xhr.status < 300) success = true;
                done++;
                if (done === urls.length) window._sn_completeDone = success;
            };
            xhr.onerror = function() {
                done++;
                if (done === urls.length) window._sn_completeDone = success;
            };
            xhr.send(payload);
        }

        urls.forEach(function(u) { tryUrl(u); });
        return null;
    """
    try:
        driver.execute_script(script, course_id, child_id)
        for _ in range(10):
            time.sleep(0.3)
            result = driver.execute_script("return window._sn_completeDone;")
            if result is not None:
                return bool(result)
    except Exception as e:
        logger.debug(f"Complete via XHR failed: {e}")
    return False


def _selenium_find_items(driver):
    time.sleep(5)

    script = """
        var items = [];
        var links = document.querySelectorAll('a');
        for (var i = 0; i < links.length; i++) {
            var href = links[i].getAttribute('href') || '';
            var m = href.match(/child_id=([^&]+)/);
            if (!m) continue;
            var id = m[1];
            var title = links[i].textContent.trim() || links[i].getAttribute('title') || id;
            var fullHref = href.startsWith('http') ? href : 'https://learning.servicenow.com' + (href.startsWith('/') ? '' : '/') + href;
            if (!items.some(function(x) { return x.id === id; })) {
                items.push({ id: id, title: title, href: fullHref });
            }
        }
        return items;
    """

    try:
        found = driver.execute_script(script)
        if found and len(found) > 0:
            return found
    except Exception as e:
        logger.debug(f"JS item search failed: {e}")

    # Fallback: try to expand sections and retry
    try:
        driver.execute_script("""
            document.querySelectorAll('[class*="expand"], [class*="collapse"], button, summary, .accordion-header').forEach(function(el) {
                try { el.click(); } catch(e) {}
            });
        """)
        time.sleep(3)
        found = driver.execute_script(script)
        if found and len(found) > 0:
            return found
    except Exception:
        pass

    return []


def _angular_scope_items(driver, course_id):
    """Extract course items from AngularJS scope data with status/href."""
    script = """
    var courseId = arguments[0];
    var results = [];

    function extractItems(arr, depth) {
        var out = [];
        for (var i = 0; i < arr.length; i++) {
            var item = arr[i];
            var id = item.child_sys_id || item.sys_id || item.id || item.childId || item.content_id || '';
            if (!id || !/^[a-f0-9]{32}$/.test(id)) {
                if (item.children && Array.isArray(item.children)) {
                    out = out.concat(extractItems(item.children, depth + 1));
                }
                continue;
            }
            var title = item.child_name || item.title || item.name || item.short_description || id;
            var href = item._url || '';
            if (href && href.charAt(0) === '&') {
                href = '/lxp/en/pages/learning-course?id=learning_course&spa=1' + href;
            }
            var state = item.state || item.child_state || item.status || 'unknown';
            var allowNav = item._allowNavigation !== false;
            var childType = item.child_type || item._type || '';
            var pct = item._percent_complete;
            if (typeof pct !== 'number') pct = 0;
            var order = parseInt(item.order, 10);
            if (isNaN(order)) order = i;
            var completed = state === 'completed' || state === 'complete';

            out.push({
                id: id,
                title: title,
                state: state,
                completed: completed,
                href: href,
                allowNavigation: allowNav,
                childType: childType,
                percent: pct,
                order: order,
                depth: depth,
                hasChildren: !!(item.children && item.children.length)
            });

            if (item.children && Array.isArray(item.children)) {
                out = out.concat(extractItems(item.children, depth + 1));
            }
        }
        return out;
    }

    try {
        var widgetEls = document.querySelectorAll('[data], [widget], [sn-atf-area]');
        for (var wi = 0; wi < widgetEls.length; wi++) {
            var el = widgetEls[wi];
            try {
                var scope = angular.element(el).scope();
                if (!scope) continue;
                var s = scope;
                var safety = 0;
                while (s && safety < 20) {
                    safety++;
                    var candidates = null;
                    if (s.c && s.c.data && s.c.data.items && s.c.data.items.length) candidates = s.c.data.items;
                    else if (s.data && s.data.items && s.data.items.length) candidates = s.data.items;
                    else if (s.items && s.items.length) candidates = s.items;
                    else if (s.c && s.c.items && s.c.items.length) candidates = s.c.items;

                    if (candidates) {
                        results = results.concat(extractItems(candidates, 0));
                    }

                    if (s['$parent'] === s) break;
                    s = s['$parent'];
                }
            } catch(e) {}
        }
    } catch(e) {}

    // Deduplicate by id
    var seen = {};
    results = results.filter(function(x) {
        if (seen[x.id]) return false;
        seen[x.id] = true;
        return true;
    });

    // Sort by order
    results.sort(function(a, b) { return a.order - b.order; });

    return results;
    """
    try:
        found = driver.execute_script(script, course_id)
        if found and len(found) > 0:
            for item in found:
                item["href"] = f"/lxp/en/pages/learning-course?id=learning_course&course_id={course_id}&child_id={item['id']}&spa=1"
            return found
    except Exception as e:
        logger.debug(f"Angular scope search failed: {e}")

    # Fallback: try accessing via spPageCtrl directly
    script2 = """
    var courseId = arguments[0];
    try {
        var body = document.body;
        var scope = angular.element(body).scope();
        if (scope) {
            var s = scope;
            while (s) {
                if (s.c && s.c.data && s.c.data.items && Array.isArray(s.c.data.items)) {
                    var items = [];
                    function extract(arr) {
                        for (var i = 0; i < arr.length; i++) {
                            var x = arr[i];
                            var id = x.id || x.sys_id || '';
                            if (id && /^[a-f0-9]{32}$/.test(id)) {
                                items.push({id: id, title: x.title || x.name || id});
                            }
                            if (x.children && Array.isArray(x.children)) extract(x.children);
                        }
                    }
                    extract(s.c.data.items);
                    return items;
                }
                if (s.$parent === s) break;
                s = s.$parent;
            }
        }
    } catch(e) {}
    return [];
    """
    try:
        found = driver.execute_script(script2, course_id)
        if found and len(found) > 0:
            for item in found:
                item["href"] = f"/lxp/en/pages/learning-course?id=learning_course&course_id={course_id}&child_id={item['id']}&spa=1"
            return found
    except Exception as e:
        logger.debug(f"spPageCtrl scope search failed: {e}")

    return []


def _click_complete_button(driver):
    """Look for and click 'Mark Complete', 'Done', 'Finish', 'Next', 'Continue' buttons."""
    try:
        ok = driver.execute_script("""
            var labels = ['mark complete', 'done', 'finish', 'next', 'continue', 'complete', 'submit'];
            var buttons = document.querySelectorAll('button, a, input[type=button], input[type=submit], [role=button]');
            for (var b = 0; b < buttons.length; b++) {
                var el = buttons[b];
                var txt = (el.textContent || el.value || '').toLowerCase().trim();
                for (var l = 0; l < labels.length; l++) {
                    if (txt === labels[l] || txt.indexOf(labels[l]) === 0) {
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        """)
        return bool(ok)
    except Exception as e:
        logger.debug(f"Click button failed: {e}")
    return False


def _has_video_element(driver):
    """Quick check if a <video> element exists on the page (0 wait)."""
    return bool(driver.execute_script("return !!document.querySelector('video');"))


def _wait_video_ready(driver, timeout=10):
    for _ in range(timeout * 2):
        ok = driver.execute_script("""
            var v = document.querySelector('video');
            if (!v) return false;
            if (v.readyState >= 1 && v.duration && v.duration > 0 && v.duration !== Infinity) return true;
            return false;
        """)
        if ok:
            return True
        time.sleep(0.5)
    return False


def _complete_via_video(driver):
    # Quick check: if no <video> element exists, skip immediately
    if not _has_video_element(driver):
        return False
    if not _wait_video_ready(driver):
        return False

    try:
        result = driver.execute_script("""
            var v = document.querySelector('video');
            if (!v) return 'NO_VIDEO';
            var dur = v.duration;
            if (!dur || dur === Infinity || dur === 0) return 'NO_DURATION';
            v.currentTime = Math.max(0, dur - 5);
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                if (btns[i].textContent.toLowerCase().includes('play video')) {
                    btns[i].click();
                    break;
                }
            }
            v.play();
            setTimeout(function() {
                window._sn_videoDone = v.ended || v.currentTime >= dur - 1;
            }, 8000);
            return 'PLAYING';
        """)
        if result in ('NO_VIDEO', 'NO_DURATION'):
            return False
        for _ in range(20):
            time.sleep(0.5)
            done = driver.execute_script("return typeof window._sn_videoDone !== 'undefined' ? window._sn_videoDone : null;")
            if done is True:
                return True
            if done is False:
                return False
    except Exception as e:
        logger.debug(f"Video completion failed: {e}")
    return False


def _scorm_complete_js():
    return """(() => {
        for (let el of document.querySelectorAll('[class*="completed" i], [class*="passed" i]')) {
            if (el.offsetParent !== null) return 'SKIPPED';
        }
        function findAPI() {
            if (window.API) return window.API;
            if (window.API_1484_11) return window.API_1484_11;
            for (let f of document.querySelectorAll('iframe')) {
                try {
                    let w = f.contentWindow;
                    if (w && (w.API || w.API_1484_11)) return w.API || w.API_1484_11;
                } catch(e) {}
            }
            return null;
        }
        let api = findAPI();
        if (!api) return 'NO_API';
        let is2004 = !!window.API_1484_11;
        if (typeof api.LMSInitialize === 'function') api.LMSInitialize('');
        else if (typeof api.Initialize === 'function') api.Initialize('');
        if (is2004) {
            api.SetValue('cmi.completion_status', 'completed');
            api.SetValue('cmi.success_status', 'passed');
            api.SetValue('cmi.score.raw', '100');
            api.SetValue('cmi.score.max', '100');
            api.SetValue('cmi.score.min', '0');
        } else {
            api.LMSSetValue('cmi.core.lesson_status', 'completed');
            api.LMSSetValue('cmi.core.score.raw', '100');
            api.LMSSetValue('cmi.core.score.max', '100');
        }
        if (typeof api.Commit === 'function') api.Commit('');
        else if (typeof api.LMSCommit === 'function') api.LMSCommit('');
        if (typeof api.Finish === 'function') api.Finish('');
        else if (typeof api.LMSFinish === 'function') api.LMSFinish('');
        return 'OK';
    })()"""

@click.command(context_settings={"max_content_width":100})
@click.argument("course")
@click.option("--set-cookies", is_flag=True)
@click.option("--discover", is_flag=True)
@click.option("--browser", is_flag=True, help="Use browser automation")
@click.option("--headless", is_flag=True)
@click.option("--debug", is_flag=True, help="Save page source for debugging")
@click.option("--delay", default=0.5)
def main(course, set_cookies, discover, browser, headless, debug, delay):
    cfg = load_config()
    if set_cookies:
        cfg["cookies"] = prompt_cookies()
        save_config(cfg)
        click.echo(f"Saved to {CONFIG_FILE}")
        return
    cid = extract_course_id(course)
    if not cid:
        logger.error("Could not find course_id. Pass a full URL or the 32-char hex course_id.")
        sys.exit(1)

    logger.info(f"Course ID: {cid}")

    if browser:
        browser_login(cid, headless, delay, debug)
        return

    if not cfg.get("cookies"):
        click.echo("No cookies found. For non-browser mode, run: sn-skipera --set-cookies")
        click.echo("Or use --browser for browser-based auto-completion.")
        return

    course_url = build_content_url(cid)
    session = make_session(cfg)
    if discover:
        eps = discover_endpoints(session, course_url)
        if eps: cfg["endpoints"] = eps; save_config(cfg); logger.success(f"Saved {len(eps)} endpoints")
        else: logger.warning("No endpoints found")
        return

    # HTTP API mode
    items = get_items_via_api(session, cid)
    if not items:
        logger.warning("No items found via API. Try --browser if the course has SCORM content.")
        return

    endpoints = cfg.get("endpoints", [])
    if not endpoints:
        logger.warning("No saved endpoints — results may vary. Run --discover first.")

    completed = failed = 0
    for i, it in enumerate(items):
        logger.info(f"[{i+1}/{len(items)}] {it['title']}")
        ok = complete_via_api(session, cid, it["id"], endpoints)
        if ok: completed += 1; logger.success("  ✓")
        else: failed += 1; logger.warning("  ✗")
        time.sleep(delay)
    logger.info(f"\nDone: {completed}/{len(items)} completed")

if __name__ == "__main__":
    main()
