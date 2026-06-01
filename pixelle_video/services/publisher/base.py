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
    """A request to publish one video to one platform."""
    video_path: str
    title: str
    description: str = ""
    topics: List[str] = field(default_factory=list)   # hashtag topics, without leading '#'
    cover_path: Optional[str] = None


@dataclass
class PublishResult:
    """Outcome of a publish attempt."""
    success: bool
    platform: str
    detail: str = ""
    publish_url: Optional[str] = None


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

    def __init__(self, session_file: str):
        self.session_file = session_file

    # ------------------------------------------------------------------ #
    # Platform-specific behaviour (must override)
    # ------------------------------------------------------------------ #
    @abstractmethod
    async def is_logged_in(self, page) -> bool:
        """Return True if the current page indicates a logged-in creator session."""

    @abstractmethod
    async def fill_publish_form(self, page, req: "PublishRequest") -> None:
        """Upload the video and fill title/topics/description, WITHOUT clicking Publish."""

    # ------------------------------------------------------------------ #
    # Shared flows (each runs start-to-finish within one event loop)
    # ------------------------------------------------------------------ #
    async def login(self, wait_timeout: float = 180.0, poll_interval: float = 2.0) -> bool:
        """Open the login page headful, wait for the user to scan the QR code,
        then persist the login state. Returns True on success, False on timeout."""
        context = await PublishBrowser.new_context()  # no prior state
        page = await context.new_page()
        await PublishBrowser.apply_stealth(page)
        try:
            await page.goto(self.login_url, wait_until="domcontentloaded")
            logger.info(f"[{self.platform}] 请在弹出的浏览器中扫码登录...")
            elapsed = 0.0
            while elapsed < wait_timeout:
                try:
                    if await self.is_logged_in(page):
                        await PublishBrowser.save_state(context, self.session_file)
                        logger.success(f"[{self.platform}] 登录成功，已保存登录态")
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

    async def publish_semi_auto(
        self, req: "PublishRequest", confirm_timeout: float = 300.0, poll_interval: float = 3.0
    ) -> "PublishResult":
        """Open the upload page, fill the form, then wait for the user to click
        Publish in the browser. Returns once the success URL appears, the user
        closes the window, or the timeout elapses."""
        if not os.path.exists(self.session_file):
            return PublishResult(False, self.platform, detail="未登录，请先扫码登录")
        if not os.path.exists(req.video_path):
            return PublishResult(False, self.platform, detail=f"视频文件不存在: {req.video_path}")

        context = await PublishBrowser.new_context(self.session_file)
        page = await context.new_page()
        await PublishBrowser.apply_stealth(page)
        try:
            await page.goto(self.upload_url, wait_until="domcontentloaded")
            if not await self.is_logged_in(page):
                return PublishResult(False, self.platform, detail="登录态已失效，请重新扫码登录")

            await self.fill_publish_form(page, req)
            logger.info(f"[{self.platform}] 表单已填好，等待用户在浏览器中点击【发布】...")

            outcome = await self._wait_for_completion(page, confirm_timeout, poll_interval)
            if outcome == "success":
                return PublishResult(True, self.platform, detail="检测到发布成功", publish_url=self._safe_url(page))
            if outcome == "closed":
                return PublishResult(True, self.platform, detail="浏览器已关闭，请到平台确认发布结果")
            return PublishResult(
                False,
                self.platform,
                detail=f"等待发布超时（{int(confirm_timeout)}s）。若已发布请忽略，否则请重试",
            )
        except Exception as e:
            logger.exception(f"[{self.platform}] publish failed")
            return PublishResult(False, self.platform, detail=f"{type(e).__name__}: {e}")
        finally:
            try:
                await context.close()
            except Exception:
                pass

    async def _wait_for_completion(self, page, timeout: float, poll_interval: float) -> str:
        """Poll until the user closes the page ('closed'), the success URL appears
        ('success'), or the timeout elapses ('timeout'). The close signal lets the
        user drive completion even when success-URL detection isn't reliable."""
        elapsed = 0.0
        while elapsed < timeout:
            try:
                if page.is_closed():
                    return "closed"
                if self.success_url_part and self.success_url_part in (page.url or ""):
                    return "success"
            except Exception:
                # page/context likely torn down by the user closing the window
                return "closed"
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        return "timeout"

    @staticmethod
    def _safe_url(page) -> Optional[str]:
        try:
            return page.url
        except Exception:
            return None
