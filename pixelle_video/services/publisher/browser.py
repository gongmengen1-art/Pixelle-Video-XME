# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Browser session manager for the publishing feature.

Manages a shared *headful* Chromium instance (separate from the headless one
used by HTMLFrameGenerator) and produces login-state-aware browser contexts
for publishing automation.

Event-loop handling mirrors HTMLFrameGenerator._ensure_browser: Streamlit's
run_async helper may create a new event loop per call, so the shared browser
is recreated whenever the running loop changes.
"""

import asyncio
import os
from typing import Optional

from loguru import logger

# Chromium launch args: stable in containers + reduce the automation fingerprint
# (--disable-blink-features=AutomationControlled removes navigator.webdriver).
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-blink-features=AutomationControlled",
]


class PublishBrowser:
    """Shared headful Chromium for publishing automation."""

    _playwright = None
    _browser = None
    _browser_loop = None

    @classmethod
    async def ensure_browser(cls, headless: bool = False):
        """Lazily launch a shared Chromium, recreating it if the event loop changed."""
        current_loop = asyncio.get_running_loop()
        usable = (
            cls._browser is not None
            and cls._browser_loop is current_loop
            and cls._browser.is_connected()
        )
        if usable:
            return cls._browser

        if cls._browser is not None and cls._browser_loop is not current_loop:
            logger.warning(
                "Detected cross-loop publish browser reuse; recreating for current event loop"
            )

        cls._browser = None
        cls._playwright = None
        from playwright.async_api import async_playwright

        cls._playwright = await async_playwright().start()
        cls._browser = await cls._playwright.chromium.launch(
            headless=headless,
            args=_LAUNCH_ARGS,
        )
        cls._browser_loop = current_loop
        logger.debug(f"Initialized publish Chromium browser (headless={headless})")
        return cls._browser

    @classmethod
    async def new_context(cls, storage_state_path: Optional[str] = None, headless: bool = False):
        """Create a browser context, loading saved login state if the file exists."""
        browser = await cls.ensure_browser(headless)
        kwargs = {"viewport": {"width": 1280, "height": 800}}
        if storage_state_path and os.path.exists(storage_state_path):
            kwargs["storage_state"] = storage_state_path
            logger.debug(f"Loaded login state from {storage_state_path}")
        return await browser.new_context(**kwargs)

    @staticmethod
    async def apply_stealth(page) -> None:
        """Best-effort anti-detection. playwright-stealth is optional and its API
        differs across versions, so we try the 2.x API first, then the 1.x API,
        swallowing any failure (stealth is a nice-to-have, not required)."""
        try:
            from playwright_stealth import Stealth  # 2.x API

            await Stealth().apply_stealth_async(page)
            return
        except Exception:
            pass
        try:
            from playwright_stealth import stealth_async  # 1.x API

            await stealth_async(page)
        except Exception:
            pass

    @staticmethod
    async def save_state(context, path: str) -> None:
        """Persist context login state (cookies + localStorage) to disk."""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        await context.storage_state(path=path)
        logger.info(f"Saved publish login state to {path}")

    @classmethod
    async def close_browser(cls) -> None:
        """Shut down the shared browser (call on app teardown)."""
        if cls._browser:
            try:
                await cls._browser.close()
            except Exception as e:
                logger.warning(f"Error closing publish browser: {e}")
            cls._browser = None
            cls._browser_loop = None
        if cls._playwright:
            try:
                await cls._playwright.stop()
            except Exception as e:
                logger.warning(f"Error stopping playwright: {e}")
            cls._playwright = None
        logger.debug("Publish browser closed")
