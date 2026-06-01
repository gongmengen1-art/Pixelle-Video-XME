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
Xiaohongshu (小红书) creator-center publisher.

Automates the publish form on creator.xiaohongshu.com and stops before the
final "发布" click (semi-automatic).
"""

from loguru import logger

from pixelle_video.services.publisher.base import BasePublisher, PublishRequest


class XhsPublisher(BasePublisher):
    platform = "xiaohongshu"
    display_name = "小红书"
    login_url = "https://creator.xiaohongshu.com/"
    upload_url = "https://creator.xiaohongshu.com/publish/publish?source=official"
    # Xiaohongshu's post-publish redirect varies; leave empty and rely on the
    # user closing the window as the completion signal (set if found stable).
    success_url_part = ""

    # NOTE: verify against the live page. Xiaohongshu requires selecting the
    # "上传视频" tab before the file input appears. Update selectors here when
    # the platform changes its DOM (plan: "选择器需实地调试").
    VIDEO_TAB = 'text=上传视频'
    FILE_INPUT = 'input[type="file"]'
    TITLE_INPUT = 'input[placeholder*="标题"]'
    CONTENT_EDITOR = 'div[contenteditable="true"], textarea[placeholder*="正文"]'

    async def is_logged_in(self, page) -> bool:
        if "login" in (page.url or ""):
            return False
        try:
            await page.wait_for_selector(f"{self.VIDEO_TAB}, {self.FILE_INPUT}", timeout=5000)
            return True
        except Exception:
            return False

    async def fill_publish_form(self, page, req: PublishRequest) -> None:
        # 1) ensure the "upload video" tab is active
        try:
            tab = await page.wait_for_selector(self.VIDEO_TAB, timeout=8000)
            await tab.click()
        except Exception:
            logger.debug("[xiaohongshu] video tab not found (maybe already selected)")

        # 2) upload the video file
        file_input = await page.wait_for_selector(self.FILE_INPUT, timeout=30000)
        await file_input.set_input_files(req.video_path)
        logger.info("[xiaohongshu] uploading video, waiting for the edit page...")

        # 3) title (Xiaohongshu title cap ~20 chars)
        title = await page.wait_for_selector(self.TITLE_INPUT, timeout=180000)
        await title.fill(req.title[:20])

        # 4) body content + topics
        body = req.description or ""
        if req.topics:
            body = (body + " " + " ".join(f"#{t}" for t in req.topics)).strip()
        if body:
            try:
                editor = await page.wait_for_selector(self.CONTENT_EDITOR, timeout=10000)
                await editor.click()
                await page.keyboard.type(body)
            except Exception as e:
                logger.warning(f"[xiaohongshu] could not fill content (selector may be stale): {e}")

        logger.success("[xiaohongshu] form filled — awaiting manual publish")
