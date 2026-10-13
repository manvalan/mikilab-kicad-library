"""
eda_download.py
===============

Browser-driven download of KiCad zips from SnapEDA and Ultra Librarian,
used by fetch_components.py.

Neither site offers a public download API, so this drives a real
(Chromium, via Playwright) browser with a persistent profile:

  1. log in with the credentials from credentials.json (only when the
     site shows a login form -- the profile keeps the session);
  2. open the part's search page, follow the first result for the MPN;
  3. try to click the KiCad download;
  4. in any case wait for a download to start in the window. If a step
     in 1-3 fails (site layout changed, captcha, format picker) the user
     completes it by hand in the visible window and the download is
     still captured. Captchas are never solved automatically.

Playwright is an optional dependency:
    python3 -m pip install playwright && python3 -m playwright install chromium
(without the bundled Chromium the installed Google Chrome is used;
"browser_channel": "chrome" in credentials.json forces it).
Without it fetch_components.py falls back to opening the pages in the
default browser and picking the zips up from download_dir.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import quote

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    HAVE_PLAYWRIGHT = True
except ImportError:  # optional dependency
    HAVE_PLAYWRIGHT = False

SNAPEDA_LOGIN = "https://www.snapeda.com/account/login/"
SNAPEDA_SEARCH = "https://www.snapeda.com/search/?q={q}"
UL_SEARCH = "https://app.ultralibrarian.com/search?queryText={q}"

SHORT = 8000  # ms, for every automatic step that may legitimately be absent


def search_url(source: str, mpn: str) -> str:
    template = SNAPEDA_SEARCH if source == "snapeda" else UL_SEARCH
    return template.format(q=quote(mpn, safe=""))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class BrowserDownloader:
    """One browser session reused for the whole list."""

    def __init__(self, config: dict, log=print):
        if not HAVE_PLAYWRIGHT:
            raise RuntimeError("playwright not installed")
        self.config = config
        self.log = log
        self.timeout_s = int(config.get("download_timeout_seconds", 180))
        profile = Path(config.get("browser_profile_dir", "~/.cache/mikilab/browser")).expanduser()
        profile.mkdir(parents=True, exist_ok=True)

        self._pw = sync_playwright().start()
        options = dict(headless=bool(config.get("headless", False)), accept_downloads=True)
        channel = config.get("browser_channel")  # e.g. "chrome": the installed Google Chrome
        try:
            self.context = self._pw.chromium.launch_persistent_context(
                str(profile), **options, **({"channel": channel} if channel else {}))
        except Exception:
            if channel:
                raise
            # Playwright's own Chromium not installed: use Google Chrome.
            self.context = self._pw.chromium.launch_persistent_context(str(profile), **options, channel="chrome")
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self._downloads: list = []
        # A plain function: recent Playwright cannot wrap builtins like list.append.
        def on_download(download):
            self._downloads.append(download)
        for p in self.context.pages:
            p.on("download", on_download)
        self.context.on("page", lambda p: p.on("download", on_download))
        self._snapeda_logged_in = False

    def close(self):
        try:
            self.context.close()
        finally:
            self._pw.stop()

    # ------------------------------------------------------------------ utils

    def _try(self, label: str, fn) -> bool:
        try:
            fn()
            return True
        except Exception as e:  # selectors are best effort by design
            detail = (str(e).splitlines() or [""])[0][:120]
            self.log(f"      (auto) {label}: non riuscito ({type(e).__name__}: {detail})")
            return False

    def _wait_download(self, target: Path) -> Path | None:
        self.log(f"      in attesa del download (max {self.timeout_s}s) -- se serve, completalo a mano nella finestra del browser")
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            if self._downloads:
                dl = self._downloads.pop(0)
                target.parent.mkdir(parents=True, exist_ok=True)
                dl.save_as(str(target))
                self.log(f"      scaricato: {dl.suggested_filename} -> {target}")
                return target
            self.page.wait_for_timeout(500)
        return None

    def _fill_login(self, username: str, password: str) -> bool:
        """Fill the first visible password field and the text/email field
        right before it, then submit. Returns False if no login form."""
        pwd = self.page.locator("input[type=password]:visible").first
        if pwd.count() == 0:
            return False
        user = self.page.locator(
            "input[name=username]:visible, input[type=email]:visible, "
            "input[name*=user i]:visible, input[name*=mail i]:visible"
        ).first
        user.fill(username)
        pwd.fill(password)
        pwd.press("Enter")
        self.page.wait_for_load_state("load", timeout=30000)
        return True

    # --------------------------------------------------------------- snapeda

    def _snapeda_login(self, creds: dict):
        if self._snapeda_logged_in or not creds.get("username"):
            return
        self.page.goto(SNAPEDA_LOGIN, wait_until="load")
        if self.page.locator("input[name=username]:visible").count():
            self.page.fill("input[name=username]", creds["username"])
            self.page.fill("input[type=password]", creds["password"])
            # The "Log in" button is unreliable; submitting the form works.
            self.page.eval_on_selector("input[name=username]", "e => e.form.submit()")
            self.page.wait_for_load_state("load", timeout=30000)
            if "/account/login" in self.page.url:
                self.log("      SnapEDA: login non confermato (credenziali o captcha?) -- completalo nella finestra")
        self._snapeda_logged_in = True

    def fetch_snapeda(self, mpn: str, url: str | None, creds: dict, target: Path) -> Path | None:
        self._try("login SnapEDA", lambda: self._snapeda_login(creds))
        self.page.goto(url or search_url("snapeda", mpn), wait_until="load")

        def open_part():
            links = self.page.locator('a[href*="/parts/"][href*="view-part"]')
            n = links.count()
            want = _norm(mpn)
            for i in range(n):
                href = links.nth(i).get_attribute("href") or ""
                if want and want in _norm(href.split("/parts/", 1)[-1].split("/", 1)[0]):
                    links.nth(i).click()
                    break
            else:
                links.first.click()
            self.page.wait_for_load_state("load")

        def click_download():
            self.page.get_by_text(re.compile(r"Download Symbol and Footprint", re.I)).first.click(timeout=SHORT)
            # Optional dialogs: multi-section warning, then format picker.
            ok = self.page.get_by_text(re.compile(r"Ok, download this part", re.I))
            if ok.count():
                ok.first.click(timeout=SHORT)
            kicad = self.page.get_by_text(re.compile(r"KiCad\s*(V6|6|\+)", re.I))
            if kicad.count():
                kicad.first.click(timeout=SHORT)

        if self._try("apertura pagina parte", open_part):
            self._try("click Download Symbol and Footprint", click_download)
        return self._wait_download(target)

    # -------------------------------------------------------- ultralibrarian

    def _ul_login(self, creds: dict) -> None:
        """app.ultralibrarian.com redirects to sso.ultralibrarian.com/Account/Login
        before any page; the persistent profile keeps the session afterwards."""
        if "sso.ultralibrarian.com" not in self.page.url:
            return
        try:  # a remembered SSO session signs in and redirects back by itself
            self.page.wait_for_url(re.compile(r"https://app\.ultralibrarian\.com/"), timeout=6000)
            return
        except Exception:
            pass
        if not creds.get("username"):
            self.log("      Ultra Librarian: nessuna credenziale -- accedi a mano nella finestra")
            return
        self.page.fill("input#Username", creds["username"], timeout=SHORT)
        self.page.fill("input#Password", creds["password"], timeout=SHORT)
        remember = self.page.locator("input#RememberLogin")
        if remember.count() and not remember.is_checked():
            remember.check()
        self.page.get_by_role("button", name=re.compile(r"^\s*Login\s*$", re.I)).click(timeout=SHORT)
        try:
            self.page.wait_for_url(re.compile(r"https://app\.ultralibrarian\.com/"), timeout=30000)
        except Exception:
            self.log("      Ultra Librarian: login non confermato (credenziali o captcha?) -- completalo nella finestra")

    def fetch_ultralibrarian(self, mpn: str, url: str | None, creds: dict, target: Path) -> Path | None:
        self.page.goto(url or search_url("ultralibrarian", mpn), wait_until="load")
        self._try("login Ultra Librarian", lambda: self._ul_login(creds))
        if "sso.ultralibrarian.com" not in self.page.url and "/search" not in self.page.url:
            self.page.goto(url or search_url("ultralibrarian", mpn), wait_until="load")

        def open_part():
            # Prefer the result whose name is exactly the MPN (UL lists variants too).
            links = self.page.locator('a[href*="/details/"]')
            links.first.wait_for(timeout=SHORT)
            want = _norm(mpn)
            for i in range(links.count()):
                if _norm(links.nth(i).inner_text()) == want:
                    links.nth(i).click(timeout=SHORT)
                    break
            else:
                links.first.click(timeout=SHORT)
            self.page.wait_for_load_state("load")

        def click_download():
            self.page.locator("#export-selection-btn").click(timeout=SHORT)
            for group, option in (("KiCAD", "KiCADv6"), ("3D CAD", "ThreeDModel")):
                # The checkbox itself is hidden (custom control): use its label.
                label = self.page.locator(f"label[for={option}]")
                if not label.is_visible():
                    self.page.locator("a.accordion-toggle", has_text=re.compile(group, re.I)).first.click(timeout=SHORT)
                    label.wait_for(state="visible", timeout=SHORT)
                if not self.page.locator(f"input#{option}").is_checked():
                    label.click(timeout=SHORT)
            # reCAPTCHA and manufacturer data-sharing consents are left to the
            # user: never solved or ticked here.
            submit = self.page.locator("#submit-export")
            submit.scroll_into_view_if_needed(timeout=SHORT)
            needs = []
            if self.page.locator("iframe[src*=recaptcha]").count():
                needs.append("il captcha")
            if self.page.locator("input.consentRequest").count():
                needs.append("il consenso del produttore (se vuoi darlo)")
            if needs:
                self.log(f"      Ultra Librarian: nella finestra completa {' e '.join(needs)}, poi premi Download Now")
            else:
                submit.click(timeout=SHORT)

        if self._try("apertura pagina parte", open_part):
            self._try("export KiCad v6+ e STEP", click_download)
        return self._wait_download(target)

    def fetch(self, source: str, mpn: str, url: str | None, creds: dict, target: Path) -> Path | None:
        if source == "snapeda":
            return self.fetch_snapeda(mpn, url, creds, target)
        return self.fetch_ultralibrarian(mpn, url, creds, target)
