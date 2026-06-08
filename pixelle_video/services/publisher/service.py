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

import asyncio
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
from pixelle_video.services.publisher.wechat_channels import WechatChannelsPublisher
from pixelle_video.services.publisher.xiaohongshu import XhsPublisher


class PublisherService:
    """Facade: owns per-platform publishers and resolves login-state files."""

    PUBLISHER_CLASSES: Dict[str, type] = {
        "douyin": DouyinPublisher,
        "xiaohongshu": XhsPublisher,
        "wechat_channels": WechatChannelsPublisher,
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

    def form_fields(self, platform: str) -> List:
        """The fillable-field specs declared by one platform."""
        cls = self.PUBLISHER_CLASSES.get(platform)
        return list(getattr(cls, "FORM_FIELDS", [])) if cls else []

    def merged_form_fields(self, platforms: List[str]) -> List[Dict]:
        """Merge FORM_FIELDS across the selected platforms for the publish form.

        Fields are deduped by `key` (so a shared field like 描述 / 话题标签 is
        shown once), preserving first-seen order across the platform selection.
        Each merged entry records which platforms use it (to mark platform-
        specific fields) and the per-platform label / char cap.

        Returns ordered dicts:
          {key, kind, labels: [distinct labels], platforms: [platform keys],
           is_common: bool, max_by_platform: {platform: max_len}, help}
        """
        selected = [p for p in platforms if p in self.PUBLISHER_CLASSES]
        merged: Dict[str, Dict] = {}
        order: List[str] = []
        for platform in selected:
            for fld in self.form_fields(platform):
                entry = merged.get(fld.key)
                if entry is None:
                    entry = {
                        "key": fld.key,
                        "kind": fld.kind,
                        "labels": [],
                        "platforms": [],
                        "max_by_platform": {},
                        "help": fld.help,
                    }
                    merged[fld.key] = entry
                    order.append(fld.key)
                if fld.label not in entry["labels"]:
                    entry["labels"].append(fld.label)
                entry["platforms"].append(platform)
                if fld.max_len is not None:
                    entry["max_by_platform"][platform] = fld.max_len
                if not entry["help"] and fld.help:
                    entry["help"] = fld.help

        total = len(selected)
        result = []
        for key in order:
            entry = merged[key]
            entry["is_common"] = len(entry["platforms"]) == total and total > 1
            result.append(entry)
        return result

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
        """Fill one platform's publish form, then wait for manual confirmation.

        Never raises for expected errors — returns a PublishResult whose
        `status` / `detail` describe the outcome."""
        results = await self.publish_many(
            [platform], req, account_label=account_label, confirm_timeout=confirm_timeout
        )
        return results[0]

    async def publish_many(
        self,
        platforms: List[str],
        req: PublishRequest,
        account_label: str = "default",
        confirm_timeout: float = 300.0,
    ) -> List[PublishResult]:
        """One-click publish to several platforms in a single event loop.

        Phase 1 opens and fills each platform's form sequentially (windows pop up
        and get pre-filled one after another). Phase 2 waits for all the opened
        windows concurrently, so the user can review and hit 发布 in each at their
        own pace. Per-platform failures/cancellations are isolated; results are
        returned in the same order as `platforms`. Never raises."""
        results: Dict[str, PublishResult] = {}
        prepared_ready: List = []  # PreparedPublish with a live page
        try:
            # Phase 1: open + fill each platform in turn.
            for platform in platforms:
                if platform not in self.PUBLISHER_CLASSES:
                    results[platform] = PublishResult(
                        False, platform, status="failed", detail="不支持的平台")
                    continue
                try:
                    pub = self._make_publisher(platform, account_label)
                    prepared = await pub.open_and_fill(req)
                except Exception as e:
                    logger.exception(f"[{platform}] open/fill failed")
                    results[platform] = PublishResult(
                        False, platform, status="failed", detail=f"{type(e).__name__}: {e}")
                    continue
                if prepared.ready:
                    prepared_ready.append((pub, prepared))
                else:
                    results[platform] = prepared.result

            # Phase 2: wait for all opened windows concurrently.
            if prepared_ready:
                waited = await asyncio.gather(
                    *[pub.wait_for_user(p.page, confirm_timeout) for pub, p in prepared_ready],
                    return_exceptions=True,
                )
                for (pub, p), outcome in zip(prepared_ready, waited):
                    if isinstance(outcome, Exception):
                        logger.exception(f"[{pub.platform}] wait failed")
                        status = "cancelled" if pub._is_closed_error(outcome) else "failed"
                        detail = ("已取消发布（浏览器已关闭）" if status == "cancelled"
                                  else f"{type(outcome).__name__}: {outcome}")
                        results[pub.platform] = PublishResult(
                            False, pub.platform, status=status, detail=detail)
                    else:
                        results[pub.platform] = outcome
                    await pub._safe_close(p.context)
        finally:
            await PublishBrowser.close_browser()

        return [
            results.get(p, PublishResult(False, p, status="failed", detail="未处理"))
            for p in platforms
        ]

    async def close_all(self) -> None:
        """Shut down the shared browser (app teardown safety net)."""
        await PublishBrowser.close_browser()
