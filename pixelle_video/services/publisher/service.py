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
PublisherService — facade over per-platform publishers.

Owns the mapping platform -> publisher class and resolves per-account
login-state files. Each login/publish call runs start-to-finish in one event
loop and the shared browser is torn down afterwards (see base.py event-loop note).
"""

import os
from typing import Dict, List, Optional

from loguru import logger

from pixelle_video.config import config_manager
from pixelle_video.services.publisher.base import (
    BasePublisher,
    PublishRequest,
    PublishResult,
)
from pixelle_video.services.publisher.browser import PublishBrowser
from pixelle_video.services.publisher.douyin import DouyinPublisher
from pixelle_video.services.publisher.xiaohongshu import XhsPublisher


class PublisherService:
    """Facade: owns per-platform publishers and resolves login-state files."""

    PUBLISHER_CLASSES: Dict[str, type] = {
        "douyin": DouyinPublisher,
        "xiaohongshu": XhsPublisher,
    }

    def __init__(self, config: Optional[dict] = None):
        # `config` accepted for signature parity with other services; the
        # sessions directory is read dynamically from config_manager so config
        # edits take effect without re-initialising the service.
        pass

    # ------------------------------------------------------------------ #
    # Config / login-state helpers
    # ------------------------------------------------------------------ #
    @property
    def sessions_dir(self) -> str:
        return config_manager.config.publish.sessions_dir

    def session_file(self, platform: str, account_label: str = "default") -> str:
        return os.path.join(self.sessions_dir, f"{platform}_{account_label}.json")

    def supported_platforms(self) -> List[str]:
        return list(self.PUBLISHER_CLASSES.keys())

    def display_name(self, platform: str) -> str:
        cls = self.PUBLISHER_CLASSES.get(platform)
        return getattr(cls, "display_name", platform) if cls else platform

    def has_login(self, platform: str, account_label: str = "default") -> bool:
        return os.path.exists(self.session_file(platform, account_label))

    def _make_publisher(self, platform: str, account_label: str = "default") -> BasePublisher:
        cls = self.PUBLISHER_CLASSES.get(platform)
        if not cls:
            raise ValueError(f"Unsupported platform: {platform}")
        return cls(self.session_file(platform, account_label))

    # ------------------------------------------------------------------ #
    # Flows (each opens a fresh browser and closes it when done)
    # ------------------------------------------------------------------ #
    async def login(
        self, platform: str, account_label: str = "default", wait_timeout: float = 180.0
    ) -> bool:
        """Open a headful browser for QR login and persist the state on success."""
        try:
            pub = self._make_publisher(platform, account_label)
            return await pub.login(wait_timeout=wait_timeout)
        finally:
            await PublishBrowser.close_browser()

    async def publish(
        self,
        platform: str,
        req: PublishRequest,
        account_label: str = "default",
        confirm_timeout: float = 300.0,
    ) -> PublishResult:
        """Fill the publish form, then wait for manual confirmation in the browser.

        Never raises for expected errors — returns a PublishResult whose
        `success` / `detail` describe the outcome."""
        try:
            pub = self._make_publisher(platform, account_label)
            return await pub.publish_semi_auto(req, confirm_timeout=confirm_timeout)
        except Exception as e:
            logger.exception(f"[{platform}] publish failed")
            return PublishResult(False, platform, detail=f"{type(e).__name__}: {e}")
        finally:
            await PublishBrowser.close_browser()

    async def close_all(self) -> None:
        """Shut down the shared browser (app teardown safety net)."""
        await PublishBrowser.close_browser()
