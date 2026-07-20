#!/usr/bin/env python3
"""
SN-Skipera — ServiceNow video auto-complete CLI.
sn-skipera <course_id_or_full_url>
"""
import json, os, re, sys, time, subprocess
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
    click.echo(f"Could not extract course_id from: {val}")
    click.echo("Usage: sn-skipera \"https://learning.servicenow.com/lxp/en/pages/learning-course?id=learning_course&course_id=...\" --browser")
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


def _auto_profile():
    for p in (
        Path.home() / ".zen" / "6bciciiq.Servicenow Profile",
        Path.home() / ".zen",
        Path.home() / ".mozilla" / "firefox",
        Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles",
        Path.home() / "sn-profile",
    ):
        if p.is_dir():
            if list(p.glob("*.sqlite")) or list(p.glob("places.sqlite*")):
                return str(p)
            # Check for default-release or similar inside
            subdirs = list(p.glob("*.default*")) + list(p.glob("*.default-release*"))
            if subdirs:
                return str(subdirs[0])
    return None


def _find_browser():
    env = os.environ.get("SN_BROWSER_BINARY")
    if env:
        return env
    for c in ("firefox", "zen-browser", "zen"):
        try:
            fb = subprocess.run(["which", c], capture_output=True, text=True).stdout.strip()
            if fb:
                return fb
        except Exception:
            continue
    return None


def _find_geckodriver():
    env = os.environ.get("SN_GECKODRIVER_PATH")
    if env:
        return env
    try:
        gd = subprocess.run(["which", "geckodriver"], capture_output=True, text=True).stdout.strip()
        if gd:
            return gd
    except Exception:
        pass
    return None


def browser_login(course_id, headless, delay, profile_path, debug=False):
    """Launch Firefox/Zen browser with your profile, auto-complete course items."""
    browser_bin = _find_browser()
    if not browser_bin:
        logger.error("No Firefox-based browser found. Install Firefox or set SN_BROWSER_BINARY.")
        return 0, 0

    geckodriver = _find_geckodriver()
    if not geckodriver:
        logger.error("geckodriver not found. Install it or set SN_GECKODRIVER_PATH.")
        logger.error("  Linux: wget https://github.com/mozilla/geckodriver/releases/...")
        logger.error("  macOS: brew install geckodriver")
        return 0, 0

    # Clean stale profile locks
    profile = Path(profile_path)
    for lock in (profile / "lock", profile / ".parentlock"):
        try:
            lock.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service
    except ImportError:
        logger.error("selenium not installed. Run: pip install selenium")
        return 0, 0

    click.echo("\n" + "=" * 60)
    click.echo("  Opening browser with your ServiceNow profile.")
    click.echo("  (Okta SSO will complete silently using your saved session.)")
    click.echo("=" * 60 + "\n")

    options = Options()
    options.binary_location = browser_bin
    options.add_argument("-profile")
    options.add_argument(str(profile))
    if headless:
        options.add_argument("-headless")

    service = Service(str(GECKODRIVER_PATH))
    driver = webdriver.Firefox(service=service, options=options)

    try:
        course_url = build_content_url(course_id)
        logger.info(f"Loading course page: {course_url}")
        driver.get(course_url)

        def _wait_items(driver, course_id, max_wait=30):
            for _ in range(max_wait):
                driver.execute_script("""
                    document.querySelectorAll('[aria-expanded="false"], summary, .accordion-header:not(.active), [class*="collapsed"]').forEach(function(el) {
                        try { el.click(); } catch(e) {}
                    });
                """)
                time.sleep(1)
                items = _angular_scope_items(driver, course_id)
                if items:
                    return items
            return None

        # Phase 1: Wait for course page items (up to 30s)
        items = _wait_items(driver, course_id, 30)
        if not items:
            # Page might have redirected to home / SSO. Wait for auth then re-navigate.
            logger.info("Course page not loaded — checking for SSO...")
            for _ in range(60):
                curl = driver.current_url
                title = driver.title
                if 'sign' in title.lower() or 'okta' in curl.lower():
                    logger.info("Okta SSO in progress...")
                try:
                    gck = driver.execute_script("try{return typeof g_ck!=='undefined'?g_ck:null}catch(e){return null}")
                    if gck and 'learning-course' in curl:
                        logger.success("SSO complete!")
                        break
                except Exception:
                    pass
                time.sleep(2)
            logger.info("Re-navigating to course page...")
            driver.get(course_url)
            items = _wait_items(driver, course_id, 30)

        if not items:
            logger.error("Could not load course page. Try re-authenticating in the browser.")
            driver.quit()
            return 0, 0

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

            # Only real approach: wait for video player, play through to end, wait for server
            ok = _wait_for_video_playback(driver)
            mark = "" if ok else "(fail)"

            if ok:
                logger.success(f"  ✓ {mark}")
                completed += 1
            else:
                logger.warning("  ✗ failed")
                failed += 1

            time.sleep(delay)
            # Refresh course page to get updated item states
            driver.get(build_content_url(course_id))
            time.sleep(5)

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





def _selenium_find_items(driver):
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
                if not item.get("href"):
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


def _wait_for_video_playback(driver):
    """Wait for video player to load, play last 15s, wait for end + 5s server buffer."""
    # Phase 1: wait for <video-js> + <video> element (up to 20s)
    found = False
    for _ in range(40):
        raw = driver.execute_script("""
            var vjs = document.querySelector('video-js');
            var vid = document.querySelector('video');
            if (!vid && vjs) vid = vjs.querySelector('video');
            if (vid) return '' + vid.readyState + '|' + vid.duration;
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {
                try {
                    var doc = iframes[i].contentDocument || iframes[i].contentWindow.document;
                    vid = doc.querySelector('video');
                    if (vid) return '' + vid.readyState + '|' + vid.duration;
                } catch(e) {}
            }
            return null;
        """)
        if raw:
            found = True
            parts = raw.split("|")
            rs = int(parts[0])
            dur = float(parts[1]) if parts[1] != "null" and parts[1] else 0
            if rs >= 1 and dur > 0 and dur != float("inf"):
                break
        time.sleep(0.5)
    if not found:
        return False

    # Phase 2: seek near end and play
    result = driver.execute_script("""
        var vjs = document.querySelector('video-js');
        var vid = document.querySelector('video');
        if (!vid && vjs) vid = vjs.querySelector('video');
        if (!vid) return 'NO_VIDEO';
        var dur = vid.duration;
        if (!dur || dur === Infinity || dur === 0) return 'NO_DUR';
        vid.muted = true;
        vid.currentTime = Math.max(0, dur - 15);
        vid.play();
        return 'OK:' + dur;
    """)
    if not result or not result.startswith("OK:"):
        return False

    dur = float(result.split(":")[1])

    # Phase 3: poll for video to reach near end (max 30s after seek)
    for _ in range(60):
        time.sleep(0.5)
        ended = driver.execute_script("""
            var v = document.querySelector('video');
            if (!v) {
                var vjs = document.querySelector('video-js');
                if (vjs) v = vjs.querySelector('video');
            }
            if (!v) return null;
            return JSON.stringify({ended: v.ended, ct: v.currentTime, dur: v.duration});
        """)
        if ended:
            import json as _json
            ed = _json.loads(ended)
            if ed["ended"] or ed["ct"] >= ed["dur"] - 1:
                break

    # Phase 4: wait 5s for server to register completion
    time.sleep(5)
    return True




@click.command(context_settings={"max_content_width":100})
@click.argument("course_url", metavar="COURSE_URL")
@click.option("--set-cookies", is_flag=True)
@click.option("--discover", is_flag=True)
@click.option("--browser", is_flag=True, help="Use browser automation")
@click.option("--headless", is_flag=True)
@click.option("--profile", default=None, help="Browser profile directory (default: auto-detect Firefox/Zen profile)")
@click.option("--debug", is_flag=True, help="Save page source for debugging")
@click.option("--delay", default=0.5)
def main(course_url, set_cookies, discover, browser, headless, debug, delay, profile):
    cfg = load_config()
    if set_cookies:
        cfg["cookies"] = prompt_cookies()
        save_config(cfg)
        click.echo(f"Saved to {CONFIG_FILE}")
        return
    cid = extract_course_id(course_url)
    if not cid:
        sys.exit(1)

    logger.info(f"Course ID: {cid}")

    if browser:
        if not profile:
            profile = os.environ.get("SN_PROFILE_PATH", _auto_profile())
        if not profile:
            logger.error("No browser profile found. Pass --profile or set SN_PROFILE_PATH.")
            sys.exit(1)
        browser_login(cid, headless, delay, profile, debug)
        return

    if not cfg.get("cookies"):
        click.echo("No cookies found. Run with --browser for browser-based auto-completion.")
        click.echo("Usage: sn-skipera \"FULL_COURSE_URL\" --browser")
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
