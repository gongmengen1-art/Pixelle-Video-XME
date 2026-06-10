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
Asset-Based Pipeline UI

Implements the UI for generating videos from user-provided assets.
"""

import os
import time
from pathlib import Path
from typing import Any

import streamlit as st
from loguru import logger

from web.i18n import tr, get_language
from web.pipelines.base import PipelineUI, register_pipeline_ui
from web.components.content_input import render_bgm_section, render_version_info
from web.utils.async_helpers import run_async
from web.utils.streamlit_helpers import check_and_warn_selfhost_workflow
from pixelle_video.config import config_manager
from pixelle_video.models.progress import ProgressEvent


class AssetBasedPipelineUI(PipelineUI):
    """
    UI for the Asset-Based Video Generation Pipeline.
    Generates videos from user-provided assets (images/videos).
    """
    name = "custom_media"
    icon = "🎨"
    
    @property
    def display_name(self):
        return tr("pipeline.custom_media.name")
    
    @property
    def description(self):
        return tr("pipeline.custom_media.description")
    
    def render(self, pixelle_video: Any):
        # Three-column layout
        left_col, middle_col, right_col = st.columns([1, 1, 1])

        # ====================================================================
        # Left Column: Asset Upload & Video Info & Subtitle Config
        # ====================================================================
        with left_col:
            asset_params = self._render_asset_input()
            subtitle_params = self._render_subtitle_config()
            cover_params = self._render_cover_config()
            bgm_params = render_bgm_section(key_prefix="asset_")
            render_version_info()

        # ====================================================================
        # Middle Column: Video Configuration
        # ====================================================================
        with middle_col:
            config_params = self._render_video_config(pixelle_video)

        # ====================================================================
        # Right Column: Output Preview
        # ====================================================================
        with right_col:
            # Combine all parameters
            video_params = {
                "pipeline": self.name,
                **asset_params,
                **subtitle_params,
                **cover_params,
                **bgm_params,
                **config_params
            }

            self._render_output_preview(pixelle_video, video_params)
    
    def _render_asset_input(self) -> dict:
        """Render asset upload section"""
        with st.container(border=True):
            st.markdown(f"**{tr('asset_based.section.assets')}**")
            
            with st.expander(tr("help.feature_description"), expanded=False):
                st.markdown(f"**{tr('help.what')}**")
                st.markdown(tr("asset_based.assets.what"))
                st.markdown(f"**{tr('help.how')}**")
                st.markdown(tr("asset_based.assets.how"))
            
            # File uploader for multiple files
            uploaded_files = st.file_uploader(
                tr("asset_based.assets.upload"),
                type=["jpg", "jpeg", "png", "gif", "webp", "mp4", "mov", "avi", "mkv", "webm"],
                accept_multiple_files=True,
                help=tr("asset_based.assets.upload_help"),
                key="asset_files"
            )
            
            # Save uploaded files to temp directory with unique session ID
            asset_paths = []
            if uploaded_files:
                import uuid
                session_id = str(uuid.uuid4()).replace('-', '')[:12]
                temp_dir = Path(f"temp/assets_{session_id}")
                temp_dir.mkdir(parents=True, exist_ok=True)
                
                for idx, uploaded_file in enumerate(uploaded_files):
                    # Prefix with the upload index so that files sharing the same
                    # original name (e.g. several "videoplayback (1).mp4") don't
                    # overwrite each other on disk and don't collapse to the same
                    # path downstream. Without this, uploading N files whose names
                    # aren't all unique silently yields fewer than N assets.
                    file_path = temp_dir / f"{idx:02d}_{uploaded_file.name}"
                    with open(file_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    asset_paths.append(str(file_path.absolute()))
                
                st.success(tr("asset_based.assets.count", count=len(asset_paths)))
                
                # Preview uploaded assets
                with st.expander(tr("asset_based.assets.preview"), expanded=True):
                    # Show in a grid (3 columns)
                    cols = st.columns(3)
                    for i, (file, path) in enumerate(zip(uploaded_files, asset_paths)):
                        with cols[i % 3]:
                            # Check if image or video
                            ext = Path(path).suffix.lower()
                            if ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                                st.image(file, caption=file.name, use_container_width=True)
                            elif ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
                                st.video(file)
                                st.caption(file.name)
            else:
                st.info(tr("asset_based.assets.empty_hint"))
        
        # Video title & script mode
        with st.container(border=True):
            st.markdown(f"**{tr('asset_based.section.video_info')}**")

            # Script mode toggle
            script_mode = st.radio(
                "script_mode",
                options=["generate", "fixed"],
                horizontal=True,
                format_func=lambda x: tr(f"asset_based.script_mode.{x}"),
                key="asset_script_mode",
                label_visibility="collapsed",
            )

            video_title = st.text_input(
                tr("asset_based.video_title"),
                placeholder=tr("asset_based.video_title_placeholder"),
                help=tr("asset_based.video_title_help"),
                key="asset_video_title",
            )

            if script_mode == "generate":
                intent = st.text_area(
                    tr("asset_based.intent"),
                    placeholder=tr("asset_based.intent_placeholder"),
                    help=tr("asset_based.intent_help"),
                    height=100,
                    key="asset_intent",
                )
                fixed_segments = None
            else:
                intent = None
                fixed_segments = self._render_script_segments()

        return {
            "assets": asset_paths,
            "video_title": video_title,
            "intent": intent if intent else None,
            "script_mode": script_mode,
            "fixed_segments": fixed_segments,
        }
    
    def _render_script_segments(self) -> list:
        """
        Render '+' button segment list for custom script mode.
        Returns list of dicts: [{"text": "...", "subtitle_style": "simple_white"}, ...]
        """
        SEG_KEY = "asset_script_segments"
        DEFAULT_STYLE = "simple_white"

        if SEG_KEY not in st.session_state:
            st.session_state[SEG_KEY] = [{"text": "", "subtitle_style": DEFAULT_STYLE}]

        segments: list = st.session_state[SEG_KEY]
        n = len(segments)

        # Initialise per-segment widget keys from stored list
        for i, seg in enumerate(segments):
            if f"asset_seg_{i}" not in st.session_state:
                st.session_state[f"asset_seg_{i}"] = seg.get("text", "")
            if f"asset_seg_style_{i}" not in st.session_state:
                st.session_state[f"asset_seg_style_{i}"] = seg.get("subtitle_style", DEFAULT_STYLE)

        from pixelle_video.utils.subtitle import SUBTITLE_STYLE_CSS
        style_options = {
            key: tr(f"asset_based.subtitle.style.{key}", fallback=key)
            for key in SUBTITLE_STYLE_CSS
        }

        delete_idx = None
        for i in range(n):
            col_text, col_style, col_del = st.columns([5, 3, 1])
            with col_text:
                st.text_area(
                    label=tr("asset_based.script.segment_label", n=i + 1),
                    key=f"asset_seg_{i}",
                    height=80,
                    label_visibility="collapsed",
                )
            with col_style:
                st.selectbox(
                    label=f"style_{i}",
                    options=list(style_options.keys()),
                    format_func=lambda x, _o=style_options: _o[x],
                    key=f"asset_seg_style_{i}",
                    label_visibility="collapsed",
                )
            with col_del:
                st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
                if st.button(tr("asset_based.script.delete_segment"), key=f"del_seg_{i}", disabled=(n <= 1)):
                    delete_idx = i
                st.markdown("</div>", unsafe_allow_html=True)

        # Handle delete
        if delete_idx is not None:
            new_segs = [
                {"text": st.session_state.get(f"asset_seg_{j}", ""),
                 "subtitle_style": st.session_state.get(f"asset_seg_style_{j}", DEFAULT_STYLE)}
                for j in range(n)
            ]
            new_segs.pop(delete_idx)
            for j in range(n):
                st.session_state.pop(f"asset_seg_{j}", None)
                st.session_state.pop(f"asset_seg_style_{j}", None)
            st.session_state[SEG_KEY] = new_segs
            st.rerun()

        # Add segment button
        if st.button(tr("asset_based.script.add_segment"), key="asset_add_seg"):
            current = [
                {"text": st.session_state.get(f"asset_seg_{j}", ""),
                 "subtitle_style": st.session_state.get(f"asset_seg_style_{j}", DEFAULT_STYLE)}
                for j in range(n)
            ]
            current.append({"text": "", "subtitle_style": DEFAULT_STYLE})
            for j in range(n):
                st.session_state.pop(f"asset_seg_{j}", None)
                st.session_state.pop(f"asset_seg_style_{j}", None)
            st.session_state[SEG_KEY] = current
            st.rerun()

        # Collect non-empty segments
        result = [
            {"text": st.session_state.get(f"asset_seg_{i}", "").strip(),
             "subtitle_style": st.session_state.get(f"asset_seg_style_{i}", DEFAULT_STYLE)}
            for i in range(n)
            if st.session_state.get(f"asset_seg_{i}", "").strip()
        ]
        if not result:
            st.caption(tr("asset_based.script.no_segments"))
        return result

    def _render_cover_config(self) -> dict:
        """Render cover image upload section."""
        with st.container(border=True):
            st.markdown(f"**{tr('asset_based.section.cover')}**")

            uploaded_cover = st.file_uploader(
                tr("asset_based.cover.upload"),
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=False,
                help=tr("asset_based.cover.upload_help"),
                key="asset_cover_file",
            )

            cover_image_path = None
            if uploaded_cover is not None:
                import uuid
                from pathlib import Path as _Path
                session_id = str(uuid.uuid4()).replace("-", "")[:12]
                temp_dir = _Path(f"temp/cover_{session_id}")
                temp_dir.mkdir(parents=True, exist_ok=True)
                cover_path = temp_dir / uploaded_cover.name
                with open(cover_path, "wb") as f:
                    f.write(uploaded_cover.getbuffer())
                cover_image_path = str(cover_path.absolute())
                st.success(tr("asset_based.cover.uploaded", name=uploaded_cover.name))
                st.image(uploaded_cover, use_container_width=True)
            else:
                st.caption(tr("asset_based.cover.empty_hint"))

        return {"cover_image_path": cover_image_path}

    def _render_subtitle_config(self) -> dict:
        """Render global subtitle configuration section."""
        with st.container(border=True):
            st.markdown(f"**{tr('asset_based.section.subtitle')}**")

            # Row 1: style + position
            col_style, col_pos = st.columns([3, 2])
            with col_style:
                from pixelle_video.utils.subtitle import SUBTITLE_STYLE_CSS
                style_options = {
                    key: tr(f"asset_based.subtitle.style.{key}", fallback=key)
                    for key in SUBTITLE_STYLE_CSS
                }
                subtitle_style = st.selectbox(
                    tr("asset_based.subtitle.style"),
                    options=list(style_options.keys()),
                    format_func=lambda x: style_options[x],
                    key="asset_subtitle_style",
                )
            with col_pos:
                position_options = {
                    "top":    tr("asset_based.subtitle.position.top"),
                    "middle": tr("asset_based.subtitle.position.middle"),
                    "bottom": tr("asset_based.subtitle.position.bottom"),
                }
                subtitle_position = st.radio(
                    tr("asset_based.subtitle.position"),
                    options=list(position_options.keys()),
                    format_func=lambda x: position_options[x],
                    index=2,
                    horizontal=True,
                    key="asset_subtitle_position",
                    label_visibility="collapsed",
                )

            # Row 2: lines + chars
            col_lines, col_chars = st.columns([2, 3])
            with col_lines:
                lines_options = {
                    1: tr("asset_based.subtitle.max_lines.1"),
                    2: tr("asset_based.subtitle.max_lines.2"),
                }
                subtitle_max_lines = st.radio(
                    tr("asset_based.subtitle.max_lines"),
                    options=[1, 2],
                    format_func=lambda x: lines_options[x],
                    index=1,
                    horizontal=True,
                    key="asset_subtitle_max_lines",
                    label_visibility="collapsed",
                )
            with col_chars:
                subtitle_chars_per_line = st.slider(
                    tr("asset_based.subtitle.chars_per_line"),
                    min_value=10,
                    max_value=30,
                    value=18,
                    step=1,
                    key="asset_subtitle_chars",
                )
                st.caption(tr("asset_based.subtitle.chars_per_line_label", n=subtitle_chars_per_line))

        return {
            "subtitle_style": subtitle_style,
            "subtitle_position": subtitle_position,
            "subtitle_max_lines": subtitle_max_lines,
            "subtitle_chars_per_line": subtitle_chars_per_line,
        }

    def _render_video_config(self, pixelle_video: Any) -> dict:
        """Render video configuration section"""
        # Duration configuration
        with st.container(border=True):
            st.markdown(f"**{tr('video.title')}**")
            
            # Duration slider
            duration = st.slider(
                tr("asset_based.duration"),
                min_value=15,
                max_value=120,
                value=30,
                step=5,
                help=tr("asset_based.duration_help"),
                key="asset_duration"
            )
            st.caption(tr("asset_based.duration_label", seconds=duration))
        
        # Workflow source selection
        with st.container(border=True):
            st.markdown(f"**{tr('asset_based.section.source')}**")
            
            with st.expander(tr("help.feature_description"), expanded=False):
                st.markdown(f"**{tr('help.what')}**")
                st.markdown(tr("asset_based.source.what"))
                st.markdown(f"**{tr('help.how')}**")
                st.markdown(tr("asset_based.source.how"))
            
            source_options = {
                "runninghub": tr("asset_based.source.runninghub"),
                "selfhost": tr("asset_based.source.selfhost")
            }
            
            # Check if RunningHub API key is configured
            comfyui_config = config_manager.get_comfyui_config()
            has_runninghub = bool(comfyui_config.get("runninghub_api_key"))
            has_selfhost = bool(comfyui_config.get("comfyui_url"))
            
            # Default to runninghub always
            default_source_index = 0
            
            source = st.radio(
                tr("asset_based.source.select"),
                options=list(source_options.keys()),
                format_func=lambda x: source_options[x],
                index=default_source_index,
                horizontal=True,
                key="asset_source",
                label_visibility="collapsed"
            )
            
            # Show hint based on selection
            if source == "runninghub":
                if not has_runninghub:
                    st.warning(tr("asset_based.source.runninghub_not_configured"))
                else:
                    st.info(tr("asset_based.source.runninghub_hint"))
            else:
                if not has_selfhost:
                    st.warning(tr("asset_based.source.selfhost_not_configured"))
                else:
                    st.info(tr("asset_based.source.selfhost_hint"))
                    # Check and warn for selfhost mode (auto popup if not confirmed)
                    # Use analyse_image.json as representative workflow
                    check_and_warn_selfhost_workflow("selfhost/analyse_image.json")
        
        # TTS configuration
        with st.container(border=True):
            st.markdown(f"**{tr('section.tts')}**")
            
            # Import voice configuration
            from pixelle_video.tts_voices import EDGE_TTS_VOICES, get_voice_display_name
            
            # Get saved voice from config
            comfyui_config = config_manager.get_comfyui_config()
            tts_config = comfyui_config.get("tts", {})
            local_config = tts_config.get("local", {})
            saved_voice = local_config.get("voice", "zh-CN-YunjianNeural")
            saved_speed = local_config.get("speed", 1.2)
            
            # Build voice options with i18n
            voice_options = []
            voice_ids = []
            default_voice_index = 0
            
            for idx, voice_config in enumerate(EDGE_TTS_VOICES):
                voice_id = voice_config["id"]
                display_name = get_voice_display_name(voice_id, tr, get_language())
                voice_options.append(display_name)
                voice_ids.append(voice_id)
                
                if voice_id == saved_voice:
                    default_voice_index = idx
            
            # Two-column layout
            voice_col, speed_col = st.columns([1, 1])
            
            with voice_col:
                selected_voice_display = st.selectbox(
                    tr("tts.voice_selector"),
                    voice_options,
                    index=default_voice_index,
                    key="asset_tts_voice"
                )
                selected_voice_index = voice_options.index(selected_voice_display)
                voice_id = voice_ids[selected_voice_index]
            
            with speed_col:
                tts_speed = st.slider(
                    tr("tts.speed"),
                    min_value=0.5,
                    max_value=2.0,
                    value=saved_speed,
                    step=0.1,
                    format="%.1fx",
                    key="asset_tts_speed"
                )
                st.caption(tr("tts.speed_label", speed=f"{tts_speed:.1f}"))
        
        return {
            "duration": duration,
            "source": source,
            "voice_id": voice_id,
            "tts_speed": tts_speed
        }
    
    def _render_output_preview(self, pixelle_video: Any, video_params: dict):
        """Render output preview section"""
        with st.container(border=True):
            st.markdown(f"**{tr('section.video_generation')}**")
            
            # Check configuration
            if not config_manager.validate():
                st.warning(tr("settings.not_configured"))
            
            # Check if assets are provided
            assets = video_params.get("assets", [])
            if not assets:
                st.info(tr("asset_based.output.no_assets"))
                st.button(
                    tr("btn.generate"),
                    type="primary",
                    use_container_width=True,
                    disabled=True,
                    key="asset_generate_disabled"
                )
                return
            
            # Show asset summary
            st.info(tr("asset_based.output.ready", count=len(assets)))
            
            # Generate button
            if st.button(tr("btn.generate"), type="primary", use_container_width=True, key="asset_generate"):
                # Validate
                if not config_manager.validate():
                    st.error(tr("settings.not_configured"))
                    st.stop()
                
                # Show progress
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                start_time = time.time()
                
                try:
                    # Import pipeline
                    from pixelle_video.pipelines.asset_based import AssetBasedPipeline
                    
                    # Create pipeline
                    pipeline = AssetBasedPipeline(pixelle_video)
                    
                    # Progress callback
                    def update_progress(event: ProgressEvent):
                        if event.event_type == "analyzing_assets":
                            if event.extra_info == "start":
                                message = tr("asset_based.progress.analyzing_start", total=event.frame_total)
                            else:
                                message = tr("asset_based.progress.analyzing_complete", count=event.frame_total)
                        elif event.event_type == "analyzing_asset":
                            message = tr(
                                "asset_based.progress.analyzing_asset",
                                current=event.frame_current,
                                total=event.frame_total,
                                name=event.extra_info or ""
                            )
                        elif event.event_type == "generating_script":
                            if event.extra_info == "complete":
                                message = tr("asset_based.progress.script_complete")
                            else:
                                message = tr("asset_based.progress.generating_script")
                        elif event.event_type == "frame_step":
                            action_key = f"progress.step_{event.action}"
                            action_text = tr(action_key)
                            message = tr(
                                "progress.frame_step",
                                current=event.frame_current,
                                total=event.frame_total,
                                step=event.step,
                                action=action_text
                            )
                        elif event.event_type == "processing_frame":
                            message = tr(
                                "progress.frame",
                                current=event.frame_current,
                                total=event.frame_total
                            )
                        elif event.event_type == "concatenating":
                            if event.extra_info == "complete":
                                message = tr("asset_based.progress.concat_complete")
                            else:
                                message = tr("progress.concatenating")
                        elif event.event_type == "completed":
                            message = tr("progress.completed")
                        else:
                            message = tr(f"progress.{event.event_type}")
                        
                        status_text.text(message)
                        progress_bar.progress(min(int(event.progress * 100), 99))
                    
                    # Execute pipeline with progress callback
                    ctx = run_async(pipeline(
                        assets=video_params["assets"],
                        video_title=video_params.get("video_title", ""),
                        intent=video_params.get("intent"),
                        duration=video_params.get("duration", 30),
                        source=video_params.get("source", "runninghub"),
                        bgm_path=video_params.get("bgm_path"),
                        bgm_volume=video_params.get("bgm_volume", 0.2),
                        bgm_mode=video_params.get("bgm_mode", "loop"),
                        voice_id=video_params.get("voice_id", "zh-CN-YunjianNeural"),
                        tts_speed=video_params.get("tts_speed", 1.2),
                        script_mode=video_params.get("script_mode", "generate"),
                        fixed_script=video_params.get("fixed_script"),
                        fixed_segments=video_params.get("fixed_segments"),
                        split_mode=video_params.get("split_mode", "paragraph"),
                        subtitle_style=video_params.get("subtitle_style", "simple_white"),
                        subtitle_position=video_params.get("subtitle_position", "bottom"),
                        subtitle_max_lines=video_params.get("subtitle_max_lines", 2),
                        subtitle_chars_per_line=video_params.get("subtitle_chars_per_line", 18),
                        cover_image_path=video_params.get("cover_image_path"),
                        progress_callback=update_progress
                    ))
                    
                    total_time = time.time() - start_time

                    progress_bar.progress(100)
                    status_text.text(tr("status.success"))

                    # Persist the result into session_state so it survives the
                    # reruns triggered by any later DOM interaction (adding a
                    # paragraph, tweaking duration, etc.). A single fixed key is
                    # overwritten on every generation, so a second run naturally
                    # replaces the first and we always show the latest video.
                    if os.path.exists(ctx.final_video_path):
                        st.session_state["asset_result"] = {
                            "video_path": ctx.final_video_path,
                            "video_title": video_params.get("video_title", ""),
                            "total_time": total_time,
                            "n_scenes": len(ctx.storyboard.frames) if ctx.storyboard else 0,
                            "file_size_mb": os.path.getsize(ctx.final_video_path) / (1024 * 1024),
                            "cover_path": getattr(ctx, "cover_path", None),
                        }
                    else:
                        st.session_state.pop("asset_result", None)
                        st.error(tr("status.video_not_found", path=ctx.final_video_path))

                except Exception as e:
                    status_text.text("")
                    progress_bar.empty()
                    st.error(tr("status.error", error=str(e)))
                    logger.exception(e)
                    st.stop()
                finally:
                    # Clear the transient progress widgets; the result below is
                    # rendered from session_state and persists across reruns.
                    progress_bar.empty()
                    status_text.empty()

            # Render the most recent result (if any). Lives OUTSIDE the generate
            # button block so it re-renders on every rerun rather than vanishing
            # the moment another widget triggers one.
            self._render_result()

    def _render_result(self):
        """Render the latest generated video from session_state (rerun-safe)."""
        result = st.session_state.get("asset_result")
        if not result:
            return

        video_path = result.get("video_path")
        if not video_path or not os.path.exists(video_path):
            # Output file was cleaned up / moved since generation.
            st.session_state.pop("asset_result", None)
            return

        st.success(tr("status.video_generated", path=video_path))
        st.markdown("---")

        info_text = (
            f"⏱️ {tr('info.generation_time')} {result.get('total_time', 0):.1f}s   "
            f"📦 {result.get('file_size_mb', 0):.2f}MB   "
            f"🎬 {result.get('n_scenes', 0)}{tr('info.scenes_unit')}"
        )
        st.caption(info_text)
        st.markdown("---")

        # Video preview
        st.video(video_path)

        # Download button
        with open(video_path, "rb") as video_file:
            video_bytes = video_file.read()
            video_filename = os.path.basename(video_path)
            st.download_button(
                label="⬇️ 下载视频" if get_language() == "zh_CN" else "⬇️ Download Video",
                data=video_bytes,
                file_name=video_filename,
                mime="video/mp4",
                use_container_width=True,
                key="download_video",
            )

        # Quick entry to the Publish tab (semi-auto publish).
        # st.tabs can't be switched programmatically, so we stash the video
        # path in session_state and ask the user to click the Publish tab.
        if st.button(
            tr("asset_based.go_publish", fallback="📤 去发布到抖音/小红书"),
            use_container_width=True,
            key="asset_go_publish",
        ):
            st.session_state["publish_video_path"] = video_path
            st.session_state["publish_title"] = result.get("video_title", "")
            st.toast(tr(
                "asset_based.go_publish_hint",
                fallback="已暂存视频，请点击上方【📤 一键发布】标签页继续",
            ))

        # Cover preview + download (if a cover was generated)
        cover_path = result.get("cover_path")
        if cover_path and os.path.exists(cover_path):
            st.markdown("---")
            st.image(cover_path, caption=tr("asset_based.section.cover"), use_container_width=True)
            with open(cover_path, "rb") as cover_file:
                cover_ext = os.path.splitext(cover_path)[1] or ".jpg"
                cover_filename = os.path.basename(cover_path)
                st.download_button(
                    label=tr("asset_based.cover.download"),
                    data=cover_file.read(),
                    file_name=cover_filename,
                    mime=f"image/{cover_ext.lstrip('.').replace('jpg', 'jpeg')}",
                    use_container_width=True,
                    key="download_cover",
                )


# Register self
register_pipeline_ui(AssetBasedPipelineUI)

