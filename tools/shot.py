#!/home/pi/dashboard_webapp/.venv/bin/python
"""Full-page screenshot on the BEELINK with Playwright's bundled Chromium.

Use this for agent UI review loops instead of SSHing into display-pi. The kitchen
display is a throttle-prone Pi 4 that also runs the live kiosk, and agent runs there
(about 30 a day) left hundreds of Chromium temp profiles in its RAM-backed /tmp
(cleanup 2026-10-26). Use display-pi only when the real kiosk/touchscreen itself
is what you're testing.

Same flags as ev-planner/tools/shot.py:

  shot.py <url> <out.png> [--wait S] [--size W,H] [--mobile] [--dark|--light]
          [--click SELECTOR[;;SELECTOR...]] [--then URL2 [--block PATTERNS]] [--viewport-only]
          [--until JS_EXPRESSION [--until-timeout S]]

--until waits (up to 180 s) for a JS expression to be truthy before the --wait pause,
e.g. "window.rw && window.rw.frames > 3" for the ride-world pages.

For 3D/WebGL pages use tools/gx10shot.sh, which runs this same script on the GX10's
GPU (SHOT_GPU=1 below); on the beelink WebGL falls back to software, ~1 frame/s.

--mobile = 412x915 @2x with touch; the default size is 412,915 (pass --size 1920,1080
for the kitchen display). Prints scrollWidth vs innerWidth to catch sideways overflow,
plus any page errors.
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright


def main() -> int:
    url, out, args = sys.argv[1], sys.argv[2], sys.argv[3:]

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    wait = float(opt("--wait", "4"))
    width, height = (int(v) for v in opt("--size", "412,915").split(","))
    mobile = "--mobile" in args
    scheme = "dark" if "--dark" in args else "light" if "--light" in args else None
    errors: list[str] = []
    with sync_playwright() as p:
        if os.environ.get("SHOT_GPU") == "1":   # GX10 container: full Chromium on the NVIDIA GPU
            browser = p.chromium.launch(channel="chromium", args=[
                "--use-gl=angle", "--use-angle=gl-egl", "--ignore-gpu-blocklist", "--enable-gpu"])
        else:
            browser = p.chromium.launch()
        try:
            ctx = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=2 if mobile else 1,
                is_mobile=mobile, has_touch=mobile, color_scheme=scheme)
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append((getattr(e, "stack", None) or str(e))[:600]))
            page.on("console", lambda m: errors.append("console." + m.type + ": " + m.text) if m.type in ("error", "warning") else None)
            page.goto(url, wait_until="load", timeout=30000)
            if opt("--until"):
                try:
                    page.wait_for_function(opt("--until"), timeout=float(opt("--until-timeout", "90")) * 1000)
                except Exception:   # still screenshot, and say why the page never got there
                    print("** --until condition not met; page errors so far:")
                    for e in errors:
                        print("  ", e)
            time.sleep(wait)
            if opt("--then"):   # second page in the same browser, e.g. what a reopen restores
                if opt("--block"):   # "*/api/*" makes the second load see a dead server
                    for pat in opt("--block").split(","):
                        page.route(pat, lambda r: r.abort())
                page.goto(opt("--then"), wait_until="load", timeout=30000)
                time.sleep(wait)
            if opt("--click"):
                for sel in opt("--click").split(";;"):
                    page.click(sel, timeout=10000)
                    time.sleep(1.0)
            sw, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            page.screenshot(path=out, full_page="--viewport-only" not in args, timeout=120000)
            print(f"saved {out}  scrollWidth={sw} innerWidth={iw}"
                  + ("  ** SIDEWAYS OVERFLOW **" if sw > iw else ""))
            for e in errors:
                print("pageerror:", e)
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
