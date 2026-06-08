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
WeChat Channels (微信视频号) creator-platform publisher.

Automates the upload form on channels.weixin.qq.com and stops before the final
"发表" click (semi-automatic).
"""

from loguru import logger

from pixelle_video.services.publisher.base import BasePublisher, FormField, PublishRequest


class WechatChannelsPublisher(BasePublisher):
    platform = "wechat_channels"
    display_name = "微信视频号"
    login_url = "https://channels.weixin.qq.com/platform"
    upload_url = "https://channels.weixin.qq.com/platform/post/create"
    # After publishing, Channels redirects to the post-management list.
    success_url_part = "platform/post/list"
    # Auth cookies planted only after a successful login (absent for guests).
    # Channels sets a `sessionid`; `wxuin` is included as a secondary candidate.
    login_cookie_names = ("sessionid", "wxuin")

    # Fillable fields. Channels uses a 短标题 (6–16 chars) distinct from the main
    # 描述 — surfaced as a platform-specific field rather than the shared 标题.
    FORM_FIELDS = [
        FormField("short_title", "短标题", "text", max_len=16, help="6–16 字，视频号特有"),
        FormField("description", "描述", "textarea", max_len=1000),
        FormField("topics", "话题标签", "topics", help="空格分隔，不含 #"),
    ]

    # NOTE: Channels changes its DOM periodically. These selectors are sensible
    # defaults based on the public upload flow and MUST be verified against the
    # live page during testing (plan: "选择器需实地调试"). When a selector breaks,
    # update it here — this is the single place to maintain.
    FILE_INPUT = 'input[type="file"]'
    # Channels has a short "标题" input plus a contenteditable description editor.
    TITLE_INPUT = 'input[placeholder*="标题"], input[placeholder*="概括视频主要内容"]'
    DESC_EDITOR = 'div.input-editor[contenteditable="true"], div[contenteditable="true"]'

    # is_logged_in() inherited from BasePublisher (cookie/URL based, DOM-independent).

    async def fill_publish_form(self, page, req: PublishRequest) -> None:
        # 1) upload the video file (hidden + re-rendered input → locator-based helper)
        await self._set_file_input(page, self.FILE_INPUT, req.video_path)
        logger.info("[wechat_channels] uploading video, waiting for the edit page...")

        # 2) wait for the edit page — the description editor appears once upload is accepted
        editor = await page.wait_for_selector(self.DESC_EDITOR, timeout=180000)

        # 3) short title (Channels 短标题, ~16 chars); fall back to the shared title
        short_title = req.short_title or req.title
        if short_title:
            try:
                title = await page.wait_for_selector(self.TITLE_INPUT, timeout=10000)
                await title.fill(short_title[:16])
            except Exception as e:
                logger.warning(f"[wechat_channels] could not fill title (selector may be stale): {e}")

        # 4) description + topics into the rich-text editor
        body = req.description or ""
        if req.topics:
            body = (body + " " + " ".join(f"#{t}" for t in req.topics)).strip()
        if body:
            try:
                await editor.click()
                await page.keyboard.type(body)
            except Exception as e:
                logger.warning(f"[wechat_channels] could not fill description (selector may be stale): {e}")

        logger.success("[wechat_channels] form filled — awaiting manual publish")
