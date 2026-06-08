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
Base abstractions for the semi-automatic publishing feature.

A publisher automates the *form-filling* part of posting a video (open upload
page, upload file, fill title/topics) and then waits for the user to review and
click "发布" themselves. Keeping a human in the loop both lowers the risk of
tripping platform risk-control and avoids posting mistakes.

Event-loop note: the web UI bridges async via run_async() == asyncio.run(),
which closes the loop when the coroutine returns. Playwright objects are bound
to that loop, so we must NOT hand a live context/page back across calls. Instead
each flow (login / publish) runs start-to-finish inside a single call and only
returns once the user has finished in the browser (window closed / success URL)
or a timeout elapses.
"""

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger

from pixelle_video.services.publisher.browser import PublishBrowser


@dataclass
class PublishRequest:
    """A request to publish one video to one platform.

    Holds the superset of fillable fields across all platforms; each publisher
    reads only the attributes it supports (declared in its FORM_FIELDS).
    """
    video_path: str
    title: str = ""              # 标题 (douyin / xiaohongshu)
    short_title: str = ""        # 短标题 (wechat channels)
    description: str = ""         # 描述 / 正文
    topics: List[str] = field(default_factory=list)   # hashtag topics, without leading '#'
    cover_path: Optional[str] = None


@dataclass
class PublishResult:
    """Outcome of a publish attempt.

    `status` is the canonical outcome; `success` is kept as a convenience mirror
    (True only for "success") for any caller that checks it directly.
      success   — publish confirmed (success URL detected)
      cancelled — user closed the browser without publishing
      timeout   — waited but no publish/close within the time budget
      failed    — an error prevented filling the form (incl. not logged in)
    """
    success: bool
    platform: str
    detail: str = ""
    publish_url: Optional[str] = None
    status: str = "failed"


@dataclass
class PreparedPublish:
    """A platform whose form has been filled and is awaiting manual publish.

    Either `page`/`context` are set (ready to await via wait_for_user), or
    `result` holds a terminal PublishResult produced while opening/filling
    (not logged in, cancelled mid-fill, or an error)."""
    platform: str
    context: object = None
    page: object = None
    result: Optional["PublishResult"] = None

    @property
    def ready(self) -> bool:
        return self.result is None


@dataclass
class FormField:
    """Declarative spec for one fillable publish-form field.

    Publishers expose a FORM_FIELDS list; the UI merges these across the
    selected platforms (dedup by `key`, mark platform-specific ones) to render
    the publish-info form. `key` maps to a PublishRequest attribute.
    """
    key: str                      # PublishRequest attribute name
    label: str                    # display label (platform's own wording)
    kind: str = "text"            # "text" | "textarea" | "topics"
    max_len: Optional[int] = None  # platform char cap (informational + truncation)
    help: str = ""                # short hint shown under the field


class BasePublisher(ABC):
    """Base class for a single-platform, semi-automatic publisher."""

    platform: str = "base"
    display_name: str = "Base"
    login_url: str = ""
    upload_url: str = ""
    # URL fragment present after a successful publish (used as an optional
    # auto-detection signal; may be left empty — completion also triggers when
    # the user closes the browser window). Verify against the live platform.
    success_url_part: str = ""
    # Declarative spec of this platform's fillable publish-form fields. The UI
    # merges FORM_FIELDS across the selected platforms to render the form.
    FORM_FIELDS: List["FormField"] = []
    # Auth cookie names set by the platform only after a successful QR login.
    # This is the PRIMARY, DOM-independent login signal: as soon as the scan
    # succeeds the platform plants these cookies, regardless of which page the
    # browser lands on. Pick cookies that are absent for a guest session (e.g.
    # douyin `sessionid`, xiaohongshu `web_session` — NOT visitor cookies like
    # `a1`/`webId`). Verify against the live platform.
    login_cookie_names: tuple = ()

    def __init__(self, session_file: str):
        self.session_file = session_file

    # ------------------------------------------------------------------ #
    # Platform-specific behaviour (must override)
    # ------------------------------------------------------------------ #
    @abstractmethod
    async def fill_publish_form(self, page, req: "PublishRequest") -> None:
        """Upload the video and fill title/topics/description, WITHOUT clicking Publish."""

    # ------------------------------------------------------------------ #
    # Login-state validation (shared, DOM-independent default)
    # ------------------------------------------------------------------ #
    async def is_logged_in(self, page) -> bool:
        """Return True if the page indicates a usable logged-in creator session.

        Deliberately DOM-independent by default — DOM selectors on the upload
        page are fragile (e.g. the file input is often visually hidden, which a
        default `state="visible"` wait never matches, causing a valid session to
        read as expired). The robust signals are, in order:
          1. Redirected to a login/passport page  ⇒ NOT logged in (true expiry).
          2. Auth cookie present (and not bounced) ⇒ logged in.
          3. Fallback: the platform's readiness selector is *attached* to the DOM
             (visible or not) — keeps working when cookie names drift.
        Platforms may override for special cases.
        """
        # Let any post-load auth redirect settle before reading the URL.
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        if self._is_login_url(page.url or ""):
            return False

        try:
            if await self._has_login_cookie(page.context):
                return True
        except Exception:
            pass

        ready_selector = getattr(self, "FILE_INPUT", "")
        if ready_selector:
            try:
                await page.wait_for_selector(ready_selector, timeout=5000, state="attached")
                return True
            except Exception:
                return False
        return False

    # ------------------------------------------------------------------ #
    # Shared form helpers
    # ------------------------------------------------------------------ #
    async def _set_file_input(self, page, selector: str, path: str, timeout: float = 30000) -> None:
        """Upload a file into a (possibly hidden / re-rendered) file input.

        Uses the Locator API rather than a pre-fetched ElementHandle: SPA upload
        pages (e.g. WeChat Channels) replace the <input type="file"> node after
        mount, so a handle grabbed earlier goes stale and set_input_files raises
        "Cannot set input files to detached element". A locator re-resolves the
        element at action time and retries on detach; .first picks the first
        match; set_input_files works on hidden inputs (no visibility required).
        """
        await page.locator(selector).first.set_input_files(path, timeout=timeout)

    # ------------------------------------------------------------------ #
    # Shared flows (each runs start-to-finish within one event loop)
    # ------------------------------------------------------------------ #
    async def login(self, wait_timeout: float = 180.0, poll_interval: float = 2.0) -> bool:
        """Open the login page headful, wait for the user to scan the QR code,
        then persist the login state. Returns True on success, False on timeout.

        Login is detected by the auth cookie appearing (primary, DOM-independent)
        or, as a fallback, the browser navigating away from the login/passport
        page after having been on it. We deliberately do NOT reuse is_logged_in()
        here: that checks for the upload page's file input, which is absent on the
        creator home page the browser lands on after a QR scan — so it would never
        fire during login and the scan would always read as a timeout.
        """
        context = await PublishBrowser.new_context()  # no prior state
        page = await context.new_page()
        await PublishBrowser.apply_stealth(page)
        try:
            await page.goto(self.login_url, wait_until="domcontentloaded")
            logger.info(f"[{self.platform}] 请在弹出的浏览器中扫码登录...")
            elapsed = 0.0
            seen_login_url = False  # only trust the URL fallback after we've been on the login page
            while elapsed < wait_timeout:
                try:
                    cur_url = page.url or ""
                    if self._is_login_url(cur_url):
                        seen_login_url = True

                    # Primary signal: the platform planted its auth cookie.
                    if await self._has_login_cookie(context):
                        await PublishBrowser.save_state(context, self.session_file)
                        logger.success(f"[{self.platform}] 登录成功（cookie），已保存登录态")
                        return True

                    # Fallback: navigated off the login page after having been on it.
                    if seen_login_url and cur_url and not self._is_login_url(cur_url):
                        await PublishBrowser.save_state(context, self.session_file)
                        logger.success(f"[{self.platform}] 登录成功（已离开登录页），已保存登录态")
                        return True
                except Exception as e:
                    logger.debug(f"[{self.platform}] login check retry: {e}")
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
            logger.warning(f"[{self.platform}] 登录等待超时（{wait_timeout}s）")
            return False
        finally:
            try:
                await context.close()
            except Exception:
                pass

    async def _has_login_cookie(self, context) -> bool:
        """True if any configured auth cookie is present with a non-empty value."""
        if not self.login_cookie_names:
            return False
        try:
            cookies = await context.cookies()
        except Exception:
            return False
        wanted = set(self.login_cookie_names)
        for c in cookies:
            if c.get("name") in wanted and (c.get("value") or "").strip():
                return True
        return False

    @staticmethod
    def _is_login_url(url: str) -> bool:
        """Heuristic: is this a login / passport / sign-in page URL?"""
        u = (url or "").lower()
        return any(marker in u for marker in ("login", "passport", "signin", "sso"))

    async def open_and_fill(self, req: "PublishRequest") -> "PreparedPublish":
        """Open the upload page and fill the form WITHOUT waiting for publish.

        Returns a PreparedPublish: on success it carries the live context/page
        (await it later via wait_for_user); otherwise `result` holds a terminal
        PublishResult. Never raises — closing the window mid-fill is reported as
        a 'cancelled' status, other errors as 'failed'."""
        if not os.path.exists(self.session_file):
            return PreparedPublish(self.platform, result=PublishResult(
                False, self.platform, status="failed", detail="未登录，请先扫码登录"))
        if not os.path.exists(req.video_path):
            return PreparedPublish(self.platform, result=PublishResult(
                False, self.platform, status="failed", detail=f"视频文件不存在: {req.video_path}"))

        context = await PublishBrowser.new_context(self.session_file)
        page = await context.new_page()
        await PublishBrowser.apply_stealth(page)
        try:
            await page.goto(self.upload_url, wait_until="domcontentloaded")
            if not await self.is_logged_in(page):
                await self._safe_close(context)
                return PreparedPublish(self.platform, result=PublishResult(
                    False, self.platform, status="failed", detail="登录态已失效，请重新扫码登录"))

            await self.fill_publish_form(page, req)
            logger.info(f"[{self.platform}] 表单已填好，等待用户在浏览器中点击【发布】...")
            return PreparedPublish(self.platform, context=context, page=page)
        except Exception as e:
            await self._safe_close(context)
            if self._is_closed_error(e):
                logger.info(f"[{self.platform}] 用户在填写阶段关闭了浏览器，视为取消发布")
                return PreparedPublish(self.platform, result=PublishResult(
                    False, self.platform, status="cancelled", detail="已取消发布（浏览器已关闭）"))
            logger.exception(f"[{self.platform}] fill form failed")
            return PreparedPublish(self.platform, result=PublishResult(
                False, self.platform, status="failed", detail=f"{type(e).__name__}: {e}"))

    async def wait_for_user(
        self, page, confirm_timeout: float = 300.0, poll_interval: float = 3.0
    ) -> "PublishResult":
        """Wait for the user to finish in the browser, then map the outcome to a
        PublishResult. Closing the window without publishing is a cancellation."""
        outcome = await self._wait_for_completion(page, confirm_timeout, poll_interval)
        if outcome == "success":
            return PublishResult(True, self.platform, status="success",
                                 detail="检测到发布成功", publish_url=self._safe_url(page))
        if outcome == "closed":
            return PublishResult(False, self.platform, status="cancelled",
                                 detail="已取消发布（浏览器已关闭）")
        return PublishResult(
            False, self.platform, status="timeout",
            detail=f"等待发布超时（{int(confirm_timeout)}s）。若已发布请忽略，否则请重试")

    async def publish_semi_auto(
        self, req: "PublishRequest", confirm_timeout: float = 300.0, poll_interval: float = 3.0
    ) -> "PublishResult":
        """Single-platform flow: open the upload page, fill the form, then wait
        for the user to click Publish. Thin wrapper over open_and_fill +
        wait_for_user (the multi-platform path orchestrates those two directly)."""
        prepared = await self.open_and_fill(req)
        if not prepared.ready:
            return prepared.result
        try:
            return await self.wait_for_user(prepared.page, confirm_timeout, poll_interval)
        finally:
            await self._safe_close(prepared.context)

    async def _wait_for_completion(self, page, timeout: float, poll_interval: float) -> str:
        """Poll until the success URL appears ('success'), the user closes the
        page ('closed'), or the timeout elapses ('timeout'). Success is checked
        first so a publish-then-immediately-close isn't misread as a cancel."""
        elapsed = 0.0
        while elapsed < timeout:
            try:
                if self.success_url_part and self.success_url_part in (page.url or ""):
                    return "success"
                if page.is_closed():
                    return "closed"
            except Exception:
                # page/context likely torn down by the user closing the window
                return "closed"
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        return "timeout"

    @staticmethod
    async def _safe_close(context) -> None:
        try:
            await context.close()
        except Exception:
            pass

    @staticmethod
    def _is_closed_error(e: Exception) -> bool:
        """Heuristic: did this error come from the user closing the page/window?"""
        return "closed" in f"{type(e).__name__} {e}".lower()

    @staticmethod
    def _safe_url(page) -> Optional[str]:
        try:
            return page.url
        except Exception:
            return None
