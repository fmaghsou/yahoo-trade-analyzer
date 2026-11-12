"""Keeps the Streamlit app awake: opens it in a hidden browser, and if it's asleep,
clicks the wake-up button. Run on a schedule by .github/workflows/keep-awake.yml."""
import os
import sys

from playwright.sync_api import sync_playwright

URL = os.environ.get("APP_URL", "https://fleeceguard.streamlit.app")
WAKE_TEXT = "get this app back up"        # text on Streamlit's "Zzzz" sleep page button

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(15_000)          # let the page finish loading

    button = page.get_by_role("button", name=WAKE_TEXT, exact=False)
    if button.count() > 0:
        print("App was asleep. Clicking the wake-up button.")
        button.first.click()
        try:
            # Waking can take a couple of minutes; wait for the sleep page to go away
            button.first.wait_for(state="detached", timeout=180_000)
            print("App is waking up.")
        except Exception:
            print("The wake-up button is still showing after 3 minutes.")
            browser.close()
            sys.exit(1)                     # mark the run as failed so it's visible
    else:
        print("App was already awake.")

    page.wait_for_timeout(30_000)          # stay on the page so the visit counts
    browser.close()
