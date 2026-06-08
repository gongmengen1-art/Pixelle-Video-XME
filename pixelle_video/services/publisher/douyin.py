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
Douyin (抖音) creator-center publisher.

Automates the upload form on creator.douyin.com and stops before the final
"发布" click (semi-automatic).
"""

from loguru import logger

from pixelle_video.services.publisher.base import BasePublisher, FormField, PublishRequest


class DouyinPublisher(BasePublisher):
    platform = "douyin"
    display_name = "抖音"
    login_url = "https://creator.douyin.com/"
    upload_url = "https://creator.douyin.com/creator-micro/content/upload"
    # After publishing, Douyin redirects to the content management page.
    success_url_part = "creator-micro/content/manage"
    # Auth cookies planted only after a successful login (absent for guests).
    login_cookie_names = ("sessionid", "sessionid_ss")

    # Fillable fields for this platform (see BasePublisher.FORM_FIELDS).
    FORM_FIELDS = [
        FormField("title", "标题", "text", max_len=30),
        FormField("description", "简介", "textarea", max_len=1000),
        FormField("topics", "话题标签", "topics", help="空格分隔，不含 #"),
    ]

    # NOTE: Douyin's creator center changes its DOM frequently. These selectors
    # are sensible defaults based on the public upload flow and MUST be verified
    # against the live page during testing (plan: "选择器需实地调试"). When a
    # selector breaks, update it here — this is the single place to maintain.
    FILE_INPUT = 'input[type="file"]'
    TITLE_INPUT = 'input[placeholder*="标题"], input[placeholder*="作品"]'
    CAPTION_EDITOR = 'div.editor-kit-container [contenteditable="true"], div[contenteditable="true"]'

    # is_logged_in() inherited from BasePublisher (cookie/URL based, DOM-independent).

    async def fill_publish_form(self, page, req: PublishRequest) -> None:
        # 1) upload the video file (hidden + re-rendered input → locator-based helper)
        await self._set_file_input(page, self.FILE_INPUT, req.video_path)
        logger.info("[douyin] uploading video, waiting for the edit page...")

        # 2) wait for the edit page — the title field appears once upload is accepted
        title = await page.wait_for_selector(self.TITLE_INPUT, timeout=180000)

        # 3) title (Douyin title cap ~30 chars)
        await title.fill(req.title[:30])

        # 4) caption + topics into the rich-text editor
        caption = req.description or ""
        if req.topics:
            caption = (caption + " " + " ".join(f"#{t}" for t in req.topics)).strip()
        if caption:
            try:
                editor = await page.wait_for_selector(self.CAPTION_EDITOR, timeout=10000)
                await editor.click()
                await page.keyboard.type(caption)
            except Exception as e:
                logger.warning(f"[douyin] could not fill caption (selector may be stale): {e}")

        logger.success("[douyin] form filled — awaiting manual publish")
