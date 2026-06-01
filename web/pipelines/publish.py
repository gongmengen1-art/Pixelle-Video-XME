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
Publish Pipeline UI

A standalone tab to semi-automatically publish a generated video to
Douyin / Xiaohongshu: scan-to-login account management, pick a video,
fill title/topics, then the form is auto-filled and the user clicks
"发布" in the opened browser.
"""

import os
import uuid
from pathlib import Path
from typing import Any, List, Optional, Tuple

import streamlit as st
from loguru import logger

from pixelle_video.services.publisher import PublishRequest
from web.i18n import tr
from web.pipelines.base import PipelineUI, register_pipeline_ui
from web.utils.async_helpers import run_async


class PublishPipelineUI(PipelineUI):
    """UI for one-click semi-automatic publishing."""

    name = "publish"
    icon = "📤"

    @property
    def display_name(self):
        return tr("pipeline.publish.name", fallback="一键发布")

    @property
    def description(self):
        return tr("pipeline.publish.description", fallback="把视频半自动发布到抖音 / 小红书")

    # ------------------------------------------------------------------ #
    def render(self, pixelle_video: Any):
        publisher = getattr(pixelle_video, "publisher", None)
        if publisher is None:
            st.error(tr("publish.unavailable", fallback="发布服务未初始化"))
            return

        st.caption(tr(
            "publish.intro",
            fallback="⚠️ 半自动发布：程序会打开浏览器并自动填好内容，最后的【发布】按钮请你手动确认点击。"
                     "需本地运行；首次使用请先扫码登录。",
        ))

        left_col, right_col = st.columns([1, 1])
        with left_col:
            self._render_account_section(publisher)
            video_path = self._render_video_section()
        with right_col:
            self._render_publish_section(publisher, video_path)

    # ------------------------------------------------------------------ #
    # Account login management
    # ------------------------------------------------------------------ #
    def _render_account_section(self, publisher):
        with st.container(border=True):
            st.markdown(f"**{tr('publish.section.accounts', fallback='账号登录')}**")
            for platform in publisher.supported_platforms():
                name = publisher.display_name(platform)
                logged = publisher.has_login(platform)
                col_status, col_btn = st.columns([2, 1])
                with col_status:
                    if logged:
                        st.success(f"{name} · {tr('publish.logged_in', fallback='已登录')}")
                    else:
                        st.warning(f"{name} · {tr('publish.not_logged_in', fallback='未登录')}")
                with col_btn:
                    btn_label = (
                        tr("publish.relogin_btn", fallback="重新登录")
                        if logged else tr("publish.login_btn", fallback="扫码登录")
                    )
                    if st.button(btn_label, key=f"pub_login_{platform}", use_container_width=True):
                        with st.spinner(tr(
                            "publish.logging_in",
                            fallback="已打开浏览器，请在窗口中扫码登录（最多等待 3 分钟）...",
                        )):
                            ok = run_async(publisher.login(platform))
                        if ok:
                            st.success(tr("publish.login_success", fallback="登录成功，登录态已保存"))
                            st.rerun()
                        else:
                            st.error(tr("publish.login_failed", fallback="登录失败或超时，请重试"))

    # ------------------------------------------------------------------ #
    # Video selection
    # ------------------------------------------------------------------ #
    def _render_video_section(self) -> Optional[str]:
        with st.container(border=True):
            st.markdown(f"**{tr('publish.section.video', fallback='选择视频')}**")

            options: List[Tuple[str, str]] = []
            prefill = st.session_state.get("publish_video_path", "")
            if prefill and os.path.exists(prefill):
                options.append((tr("publish.video.from_gen", fallback="📌 刚生成的视频"), prefill))
            for label, path in self._list_output_videos():
                # avoid duplicating the prefilled one
                if path != prefill:
                    options.append((label, path))

            selected_path: Optional[str] = None
            if options:
                labels = [o[0] for o in options]
                idx = st.selectbox(
                    tr("publish.video.select", fallback="从已生成的视频中选择"),
                    range(len(labels)),
                    format_func=lambda i: labels[i],
                    key="pub_video_sel",
                )
                selected_path = options[idx][1]
            else:
                st.info(tr("publish.video.none", fallback="output/ 下暂无成片，可在下方上传"))

            uploaded = st.file_uploader(
                tr("publish.video.upload", fallback="或上传一个视频文件"),
                type=["mp4", "mov", "webm", "mkv", "avi"],
                key="pub_video_upload",
            )
            if uploaded is not None:
                selected_path = self._save_upload(uploaded)

            if selected_path and os.path.exists(selected_path):
                st.video(selected_path)
                st.caption(selected_path)

            return selected_path

    def _list_output_videos(self, limit: int = 10) -> List[Tuple[str, str]]:
        out = Path("output")
        if not out.exists():
            return []
        try:
            vids = sorted(
                out.glob("*/final.mp4"), key=lambda p: p.stat().st_mtime, reverse=True
            )
        except Exception:
            return []
        return [(f"{p.parent.name}/final.mp4", str(p.resolve())) for p in vids[:limit]]

    def _save_upload(self, uploaded) -> str:
        session_id = str(uuid.uuid4()).replace("-", "")[:12]
        temp_dir = Path(f"temp/publish_{session_id}")
        temp_dir.mkdir(parents=True, exist_ok=True)
        dest = temp_dir / uploaded.name
        with open(dest, "wb") as f:
            f.write(uploaded.getbuffer())
        return str(dest.resolve())

    # ------------------------------------------------------------------ #
    # Publish info + actions
    # ------------------------------------------------------------------ #
    def _render_publish_section(self, publisher, video_path: Optional[str]):
        with st.container(border=True):
            st.markdown(f"**{tr('publish.section.info', fallback='发布信息')}**")

            title = st.text_input(
                tr("publish.title", fallback="标题"),
                value=st.session_state.get("publish_title", ""),
                key="pub_title",
            )
            topics_str = st.text_input(
                tr("publish.topics", fallback="话题标签（空格分隔，不含 #）"),
                key="pub_topics",
            )
            description = st.text_area(
                tr("publish.description", fallback="描述 / 正文"),
                height=100,
                key="pub_desc",
            )
            platforms = st.multiselect(
                tr("publish.platforms", fallback="发布平台"),
                options=publisher.supported_platforms(),
                format_func=publisher.display_name,
                key="pub_platforms",
            )

            st.caption(tr(
                "publish.semi_auto_hint",
                fallback="发布时会逐个平台打开浏览器并自动填好，请在浏览器中核对并手动点击【发布】。",
            ))

            if not video_path:
                st.info(tr("publish.need_video", fallback="请先在左侧选择或上传一个视频"))
            if not platforms:
                st.info(tr("publish.need_platform", fallback="请至少选择一个发布平台"))

            topics = [t.lstrip("#") for t in topics_str.split() if t.strip()]
            base_ready = bool(video_path and title and platforms)

            for platform in platforms:
                name = publisher.display_name(platform)
                if not publisher.has_login(platform):
                    st.warning(tr(
                        "publish.platform_need_login",
                        fallback="{name} 未登录，请先在左侧扫码登录",
                        name=name,
                    ))
                disabled = not base_ready or not publisher.has_login(platform)
                if st.button(
                    tr("publish.publish_btn", fallback="📤 发布到 {name}", name=name),
                    key=f"pub_do_{platform}",
                    type="primary",
                    use_container_width=True,
                    disabled=disabled,
                ):
                    self._do_publish(publisher, platform, name, video_path, title, description, topics)

    def _do_publish(self, publisher, platform, name, video_path, title, description, topics):
        req = PublishRequest(
            video_path=video_path,
            title=title,
            description=description,
            topics=topics,
        )
        with st.spinner(tr(
            "publish.publishing",
            fallback="正在打开浏览器并填写 {name} 的发布表单，请在浏览器中核对并点击【发布】...",
            name=name,
        )):
            try:
                result = run_async(publisher.publish(platform, req))
            except Exception as e:
                logger.exception("publish failed")
                st.error(f"{name}: {e}")
                return

        if result.success:
            st.success(f"{name} · {result.detail}")
        else:
            st.error(f"{name} · {result.detail}")


# Register self
register_pipeline_ui(PublishPipelineUI)
