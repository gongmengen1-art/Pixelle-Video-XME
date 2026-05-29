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
Asset-Based Video Pipeline

Generates marketing videos from user-provided assets (images/videos) rather than
AI-generated media. Ideal for small businesses with existing media libraries.

Workflow:
1. Analyze uploaded assets (images/videos)
2. Generate script based on user intent and available assets
3. Match assets to script scenes
4. Compose final video with narrations

Example:
    pipeline = AssetBasedPipeline(pixelle_video)
    result = await pipeline(
        assets=["/path/img1.jpg", "/path/img2.jpg"],
        video_title="Pet Store Year-End Sale",
        intent="Promote our pet store's year-end sale with a warm and friendly tone",
        duration=30
    )
"""

from typing import List, Dict, Any, Optional, Callable
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from pixelle_video.pipelines.linear import LinearVideoPipeline, PipelineContext
from pixelle_video.models.progress import ProgressEvent
from pixelle_video.utils.os_util import (
    create_task_output_dir,
    get_task_final_video_path
)

# Type alias for progress callback
ProgressCallback = Optional[Callable[[ProgressEvent], None]]


# ==================== Structured Output Models ====================

class SceneScript(BaseModel):
    """Single scene in the video script"""
    scene_number: int = Field(description="Scene number starting from 1")
    asset_path: str = Field(description="Path to the asset file for this scene")
    narrations: List[str] = Field(description="List of narration sentences for this scene (1-5 sentences)")
    duration: int = Field(description="Estimated duration in seconds for this scene")


class VideoScript(BaseModel):
    """Complete video script with scenes"""
    scenes: List[SceneScript] = Field(description="List of scenes in the video")


class AssetBasedPipeline(LinearVideoPipeline):
    """
    Asset-Based Video Pipeline
    
    Generates videos from user-provided assets instead of AI-generated media.
    """
    
    def __init__(self, core):
        """
        Initialize pipeline
        
        Args:
            core: PixelleVideoCore instance
        """
        super().__init__(core)
        self.asset_index: Dict[str, Any] = {}  # In-memory asset metadata
    
    async def __call__(
        self,
        assets: List[str],
        video_title: str = "",
        intent: Optional[str] = None,
        duration: int = 30,
        source: str = "runninghub",
        bgm_path: Optional[str] = None,
        bgm_volume: float = 0.2,
        bgm_mode: str = "loop",
        progress_callback: ProgressCallback = None,
        **kwargs
    ) -> PipelineContext:
        """
        Execute pipeline with user-provided assets
        
        Args:
            assets: List of asset file paths
            video_title: Video title
            intent: Video intent/purpose (defaults to video_title)
            duration: Target duration in seconds
            source: Workflow source ("runninghub" or "selfhost")
            bgm_path: Path to background music file (optional)
            bgm_volume: BGM volume (0.0-1.0, default 0.2)
            bgm_mode: BGM mode ("loop" or "once", default "loop")
            progress_callback: Optional callback for progress updates
            **kwargs: Additional parameters
        
        Returns:
            Pipeline context with generated video
        """
        from pixelle_video.pipelines.linear import PipelineContext
        
        # Store progress callback
        self._progress_callback = progress_callback
        
        # Create custom context with asset-specific parameters
        ctx = PipelineContext(
            input_text=intent or video_title,  # Use intent or title as input_text
            params={
                "assets": assets,
                "video_title": video_title,
                "intent": intent or video_title,
                "duration": duration,
                "source": source,
                "bgm_path": bgm_path,
                "bgm_volume": bgm_volume,
                "bgm_mode": bgm_mode,
                **kwargs
            }
        )
        
        # Store request parameters in context for easy access
        ctx.request = ctx.params
        
        try:
            # Execute pipeline lifecycle
            await self.setup_environment(ctx)
            await self.determine_title(ctx)
            await self.generate_content(ctx)
            await self.plan_visuals(ctx)
            await self.initialize_storyboard(ctx)
            await self.produce_assets(ctx)
            await self.post_production(ctx)
            await self.finalize(ctx)
            
            return ctx
            
        except Exception as e:
            await self.handle_exception(ctx, e)
            raise
    
    def _emit_progress(self, event: ProgressEvent):
        """Emit progress event to callback if available"""
        if self._progress_callback:
            self._progress_callback(event)
    
    async def setup_environment(self, context: PipelineContext) -> PipelineContext:
        """
        Analyze uploaded assets and build asset index
        
        Args:
            context: Pipeline context with assets list
        
        Returns:
            Updated context with asset_index
        """
        # Create isolated task directory
        task_dir, task_id = create_task_output_dir()
        context.task_id = task_id
        context.task_dir = Path(task_dir)  # Convert to Path for easier usage
        
        # Determine final video path
        context.final_video_path = get_task_final_video_path(task_id)
        
        logger.info(f"📁 Task directory created: {task_dir}")
        logger.info("🔍 Analyzing uploaded assets...")
        
        assets: List[str] = context.request.get("assets", [])
        if not assets:
            raise ValueError("No assets provided. Please upload at least one image or video.")
        
        total_assets = len(assets)
        logger.info(f"Found {total_assets} assets to analyze")
        
        # Emit initial progress (0-15% for asset analysis)
        self._emit_progress(ProgressEvent(
            event_type="analyzing_assets",
            progress=0.01,
            frame_current=0,
            frame_total=total_assets,
            extra_info="start"
        ))
        
        self.asset_index = {}
        
        for i, asset_path in enumerate(assets, 1):
            asset_path_obj = Path(asset_path)
            
            if not asset_path_obj.exists():
                logger.warning(f"Asset not found: {asset_path}")
                continue
            
            logger.info(f"Analyzing asset {i}/{total_assets}: {asset_path_obj.name}")
            
            # Emit progress for this asset
            progress = 0.01 + (i - 1) / total_assets * 0.14  # 1% - 15%
            self._emit_progress(ProgressEvent(
                event_type="analyzing_asset",
                progress=progress,
                frame_current=i,
                frame_total=total_assets,
                extra_info=asset_path_obj.name
            ))
            
            # Determine asset type
            asset_type = self._get_asset_type(asset_path_obj)
            
            if asset_type == "image":
                # Analyze image using ImageAnalysisService
                analysis_source = context.request.get("source", "runninghub")
                description = await self.core.image_analysis(asset_path, source=analysis_source)
                
                self.asset_index[asset_path] = {
                    "path": asset_path,
                    "type": "image",
                    "name": asset_path_obj.name,
                    "description": description
                }
                
                logger.info(f"✅ Image analyzed: {description[:50]}...")
            
            elif asset_type == "video":
                # Analyze video using VideoAnalysisService
                analysis_source = context.request.get("source", "runninghub")
                try:
                    description = await self.core.video_analysis(asset_path, source=analysis_source)
                    
                    self.asset_index[asset_path] = {
                        "path": asset_path,
                        "type": "video",
                        "name": asset_path_obj.name,
                        "description": description
                    }
                    
                    logger.info(f"✅ Video analyzed: {description[:50]}...")
                except Exception as e:
                    logger.warning(f"Video analysis failed for {asset_path_obj.name}: {e}, using fallback")
                    self.asset_index[asset_path] = {
                        "path": asset_path,
                        "type": "video",
                        "name": asset_path_obj.name,
                        "description": "Video asset (analysis failed)"
                    }
            
            else:
                logger.warning(f"Unknown asset type: {asset_path}")
        
        logger.success(f"✅ Asset analysis complete: {len(self.asset_index)} assets indexed")
        
        # Store asset index in context
        context.asset_index = self.asset_index
        
        # Emit completion of asset analysis
        self._emit_progress(ProgressEvent(
            event_type="analyzing_assets",
            progress=0.15,
            frame_current=total_assets,
            frame_total=total_assets,
            extra_info="complete"
        ))
        
        return context
    
    async def determine_title(self, context: PipelineContext) -> PipelineContext:
        """
        Use user-provided title if available, otherwise leave empty
        
        Args:
            context: Pipeline context
        
        Returns:
            Updated context with title (may be empty)
        """
        title = context.request.get("video_title")
        
        if title:
            context.title = title
            logger.info(f"📝 Video title: {title} (user-specified)")
        else:
            context.title = ""
            logger.info(f"📝 No video title specified (will be hidden in template)")
        
        return context
    
    async def generate_content(self, context: PipelineContext) -> PipelineContext:
        """
        Generate video script.

        Supports two modes controlled by context.request["script_mode"]:
        - "generate" (default): LLM creates the script from assets + intent
        - "fixed": user-supplied script is split and distributed across assets
        """
        script_mode = context.request.get("script_mode", "generate")
        if script_mode == "fixed":
            return await self._generate_content_from_fixed_script(context)

        # ── AI generation (original logic) ──────────────────────────────────
        from pixelle_video.prompts.asset_script_generation import build_asset_script_prompt
        
        logger.info("🤖 Generating video script with LLM...")
        
        # Emit progress for script generation (15% - 25%)
        self._emit_progress(ProgressEvent(
            event_type="generating_script",
            progress=0.16
        ))
        
        # Build prompt for LLM
        intent = context.request.get("intent", context.input_text)
        duration = context.request.get("duration", 30)
        title = context.title  # May be empty if user didn't provide one
        
        # Prepare asset descriptions with full paths for LLM to reference
        asset_info = []
        for asset_path, metadata in self.asset_index.items():
            asset_info.append(f"- Path: {asset_path}\n  Description: {metadata['description']}")
        
        assets_text = "\n".join(asset_info)
        
        # Build prompt using the centralized prompt function
        prompt = build_asset_script_prompt(
            intent=intent,
            duration=duration,
            assets_text=assets_text,
            title=title
        )
        
        # Call LLM with structured output
        script: VideoScript = await self.core.llm(
            prompt=prompt,
            response_type=VideoScript,
            temperature=0.8,
            max_tokens=4000
        )
        
        # Convert to dict format for compatibility with downstream code
        context.script = [scene.model_dump() for scene in script.scenes]
        
        # Validate asset paths exist
        for scene in context.script:
            asset_path = scene.get("asset_path")
            if asset_path not in self.asset_index:
                # Find closest match (in case LLM slightly modified the path)
                matched = False
                for known_path in self.asset_index.keys():
                    if Path(known_path).name == Path(asset_path).name:
                        scene["asset_path"] = known_path
                        matched = True
                        logger.warning(f"Corrected asset path: {asset_path} -> {known_path}")
                        break
                
                if not matched:
                    # Fallback to first available asset
                    fallback_path = list(self.asset_index.keys())[0]
                    logger.warning(f"Unknown asset path '{asset_path}', using fallback: {fallback_path}")
                    scene["asset_path"] = fallback_path
        
        logger.success(f"✅ Generated script with {len(context.script)} scenes")
        
        # Emit progress after script generation
        self._emit_progress(ProgressEvent(
            event_type="generating_script",
            progress=0.25,
            extra_info="complete"
        ))
        
        # Log script preview
        for scene in context.script:
            narrations = scene.get("narrations", [])
            if isinstance(narrations, str):
                narrations = [narrations]
            narration_preview = " | ".join([n[:30] + "..." if len(n) > 30 else n for n in narrations[:2]])
            asset_name = Path(scene.get("asset_path", "unknown")).name
            logger.info(f"Scene {scene['scene_number']} [{asset_name}]: {narration_preview}")
        
        return context
    
    async def plan_visuals(self, context: PipelineContext) -> PipelineContext:
        """
        Prepare matched scenes from LLM-generated script
        
        Since LLM already assigned asset_path in generate_content, this method
        simply converts the script format to matched_scenes format.
        
        Args:
            context: Pipeline context
        
        Returns:
            Updated context with matched_scenes
        """
        logger.info("🎯 Preparing scene-asset mapping...")
        
        # LLM already assigned asset_path to each scene in generate_content
        # Just convert to matched_scenes format for downstream compatibility
        context.matched_scenes = [
            {
                **scene,
                "matched_asset": scene["asset_path"]  # Alias for compatibility
            }
            for scene in context.script
        ]
        
        # Log asset usage summary
        asset_usage = {}
        for scene in context.matched_scenes:
            asset = scene["matched_asset"]
            asset_usage[asset] = asset_usage.get(asset, 0) + 1
        
        logger.info(f"📊 Asset usage summary:")
        for asset_path, count in asset_usage.items():
            logger.info(f"   {Path(asset_path).name}: {count} scene(s)")
        
        return context
    
    async def initialize_storyboard(self, context: PipelineContext) -> PipelineContext:
        """
        Initialize storyboard from matched scenes
        
        Args:
            context: Pipeline context
        
        Returns:
            Updated context with storyboard
        """
        from pixelle_video.models.storyboard import (
            Storyboard,
            StoryboardFrame, 
            StoryboardConfig
        )
        from datetime import datetime
        
        # Extract all narrations in order for compatibility
        all_narrations = []
        for scene in context.matched_scenes:
            narrations = scene.get("narrations", [scene.get("narration", "")])
            if isinstance(narrations, str):
                narrations = [narrations]
            all_narrations.extend(narrations)
        
        context.narrations = all_narrations
        
        # Get template dimensions
        # Use asset_default.html template which supports both image and video assets
        # (conditionally shows background image or provides transparent overlay)
        template_name = "1080x1920/asset_default.html"
        # Extract dimensions from template name (e.g., "1080x1920")
        try:
            dims = template_name.split("/")[0].split("x")
            media_width = int(dims[0])
            media_height = int(dims[1])
        except:
            # Default to 1080x1920
            media_width = 1080
            media_height = 1920
        
        # Build template_params: merge user-provided params with subtitle CSS vars
        from pixelle_video.utils.subtitle import build_subtitle_template_vars
        template_params: dict = dict(context.params.get("template_params") or {})
        template_params.update(build_subtitle_template_vars(context.params))

        # Create StoryboardConfig
        context.config = StoryboardConfig(
            task_id=context.task_id,
            n_storyboard=len(context.matched_scenes),  # Number of scenes
            min_narration_words=5,
            max_narration_words=50,
            video_fps=30,
            tts_inference_mode="local",
            voice_id=context.params.get("voice_id", "zh-CN-YunjianNeural"),
            tts_speed=context.params.get("tts_speed", 1.2),
            media_width=media_width,
            media_height=media_height,
            frame_template=template_name,
            template_params=template_params
        )
        
        # Create Storyboard
        context.storyboard = Storyboard(
            title=context.title,
            config=context.config,
            created_at=datetime.now()
        )
        
        # Create StoryboardFrames - one per scene
        for i, scene in enumerate(context.matched_scenes):
            # Get first narration for the frame (we'll combine audios later)
            narrations = scene.get("narrations", [scene.get("narration", "")])
            if isinstance(narrations, str):
                narrations = [narrations]
            
            # Use first narration as the main text (for subtitle)
            # We'll combine all narrations in the audio
            main_narration = " ".join(narrations)  # Combine for subtitle display
            
            frame = StoryboardFrame(
                index=i,
                narration=main_narration,
                image_prompt=None,  # We're using user assets, not generating images
                created_at=datetime.now()
            )
            
            # Get asset path and determine actual media type from asset_index
            asset_path = scene["matched_asset"]
            asset_metadata = self.asset_index.get(asset_path, {})
            asset_type = asset_metadata.get("type", "image")  # Default to image if not found
            
            # Set media type and path based on actual asset type
            if asset_type == "video":
                frame.media_type = "video"
                frame.video_path = asset_path
                logger.debug(f"Scene {i}: Using video asset: {Path(asset_path).name}")
            else:
                frame.media_type = "image"
                frame.image_path = asset_path
                logger.debug(f"Scene {i}: Using image asset: {Path(asset_path).name}")
            
            # Store scene info for later audio generation
            frame._scene_data = scene  # Temporary storage for multi-narration
            
            context.storyboard.frames.append(frame)
        
        logger.info(f"✅ Created storyboard with {len(context.storyboard.frames)} scenes")
        
        return context
    
    async def produce_assets(self, context: PipelineContext) -> PipelineContext:
        """
        Generate scene videos with per-chunk subtitle sync.

        For each scene:
          Phase 1 — Generate TTS audio + subtitle PNG for every chunk of every narration.
          Phase 2 — Build the scene video:
            • Video asset: single ffmpeg call (scale → overlay subtitles at time ranges →
              concat audio).  No clip-splitting, no concat = zero stutter at boundaries.
            • Image asset: one sub-segment per chunk, then concat (static image, no issue).
        """
        import shutil

        from pixelle_video.services.frame_html import HTMLFrameGenerator
        from pixelle_video.services.video import VideoService
        from pixelle_video.utils.template_util import resolve_template_path
        from pixelle_video.utils.subtitle import (
            build_subtitle_template_vars,
            format_subtitle_text,
            split_narration_into_subtitle_chunks,
        )

        logger.info("🎬 Producing scene videos (per-narration sync mode)...")

        storyboard = context.storyboard
        config = context.config
        total_frames = len(storyboard.frames)

        all_narrations_count = sum(
            len(f._scene_data.get("narrations", [f._scene_data.get("narration", "")]) or [])
            for f in storyboard.frames
        )
        narration_counter = 0

        base_progress = 0.30
        progress_range = 0.55

        global_style = context.params.get("subtitle_style", "simple_white")

        template_path = resolve_template_path(config.frame_template)
        html_generator = HTMLFrameGenerator(template_path)
        video_service = VideoService()

        frames_dir = Path(context.task_dir) / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        for i, frame in enumerate(storyboard.frames, 1):
            logger.info(f"Producing scene {i}/{total_frames}...")

            scene = frame._scene_data
            narrations = scene.get("narrations", [scene.get("narration", "")])
            if isinstance(narrations, str):
                narrations = [narrations]
            narration_styles: list = scene.get("narration_styles", [])

            # ── Phase 1: TTS + subtitle PNG for every chunk ───────────────────
            # chunks_info: [{audio, composed, duration, t_start, text}]
            chunks_info: list = []
            video_offset = 0.0

            for j, narration_text in enumerate(narrations, 1):
                narration_counter += 1
                progress = base_progress + narration_counter / max(all_narrations_count, 1) * progress_range

                narration_style = (
                    narration_styles[j - 1] if j - 1 < len(narration_styles) else global_style
                )
                sub_vars = build_subtitle_template_vars({
                    **context.params,
                    "subtitle_style": narration_style,
                })
                max_lines = int(sub_vars["_subtitle_max_lines"])
                chars_per_line = int(sub_vars["_subtitle_chars_per_line"])

                chunks = split_narration_into_subtitle_chunks(narration_text, max_lines, chars_per_line)
                logger.debug(f"  Scene {i} narration {j}: {len(chunks)} chunk(s)")

                for c, chunk_text in enumerate(chunks, 1):
                    self._emit_progress(ProgressEvent(
                        event_type="frame_step", progress=progress,
                        frame_current=i, frame_total=total_frames, step=j, action="audio",
                    ))

                    # 1. TTS
                    audio_path = str(frames_dir / f"{i:02d}_n{j}_c{c}.mp3")
                    await self.core.tts(
                        text=chunk_text, output_path=audio_path,
                        voice=config.voice_id, speed=config.tts_speed,
                    )

                    # 2. Get audio duration
                    import ffmpeg as _ffmpeg
                    try:
                        chunk_dur = float(_ffmpeg.probe(audio_path)['format']['duration'])
                    except Exception:
                        chunk_dur = 5.0

                    # 3. Render subtitle PNG
                    self._emit_progress(ProgressEvent(
                        event_type="frame_step", progress=min(progress + 0.01, 0.99),
                        frame_current=i, frame_total=total_frames, step=j, action="compose",
                    ))
                    composed_path = str(frames_dir / f"{i:02d}_n{j}_c{c}_composed.png")
                    ext: dict = {"index": frame.index + 1}
                    if config.template_params:
                        ext.update(config.template_params)
                    ext.update(sub_vars)
                    media_path = frame.video_path if frame.media_type == "video" else frame.image_path
                    await html_generator.generate_frame(
                        title=storyboard.title, text=format_subtitle_text(chunk_text, max_lines, chars_per_line),
                        image=media_path, ext=ext, output_path=composed_path,
                    )

                    chunks_info.append({
                        "audio": audio_path,
                        "composed": composed_path,
                        "duration": chunk_dur,
                        "t_start": video_offset,
                        "text": chunk_text,
                    })
                    video_offset += chunk_dur
                    logger.debug(
                        f"  Scene {i} n{j}/c{c}: {chunk_text[:30]}{'…' if len(chunk_text) > 30 else ''}"
                    )

            # ── Phase 2: Build scene video ────────────────────────────────────
            self._emit_progress(ProgressEvent(
                event_type="frame_step", progress=min(progress + 0.02, 0.99),
                frame_current=i, frame_total=total_frames, step=len(narrations), action="video",
            ))

            final_segment = str(frames_dir / f"{i:02d}_segment.mp4")

            if frame.media_type == "video":
                # Single ffmpeg call: scale + time-ranged subtitle overlays + audio concat.
                # No intermediate clip files, no segment concat → no frame-boundary stutter.
                self._build_video_scene(
                    source=frame.video_path,
                    chunks=chunks_info,
                    output=final_segment,
                )
            else:
                # Image asset: create one sub-segment per chunk, then concat.
                sub_paths = []
                for k, info in enumerate(chunks_info, 1):
                    sp = str(frames_dir / f"{i:02d}_sub_{k}.mp4")
                    video_service.create_video_from_image(
                        image=info["composed"], audio=info["audio"],
                        output=sp, fps=config.video_fps,
                    )
                    sub_paths.append(sp)

                if len(sub_paths) == 1:
                    shutil.copy(sub_paths[0], final_segment)
                else:
                    self._concat_segments(sub_paths, final_segment)

            frame.video_segment_path = final_segment
            logger.success(f"✅ Scene {i} complete ({len(chunks_info)} subtitle chunk(s))")

        self._emit_progress(ProgressEvent(
            event_type="processing_frame", progress=0.85,
            frame_current=total_frames, frame_total=total_frames,
        ))

        return context
    
    async def post_production(self, context: PipelineContext) -> PipelineContext:
        """
        Concatenate scene videos and add BGM
        
        Args:
            context: Pipeline context
        
        Returns:
            Updated context with final video path
        """
        logger.info("🎞️ Concatenating scenes...")
        
        # Emit progress for concatenation (85% - 95%)
        self._emit_progress(ProgressEvent(
            event_type="concatenating",
            progress=0.86
        ))
        
        # Collect video segments from storyboard frames
        scene_videos = [frame.video_segment_path for frame in context.storyboard.frames]
        
        # Generate filename: use title if provided, otherwise use task_id or default name
        if context.title:
            filename = f"{context.title}.mp4"
        else:
            filename = f"{context.task_id}.mp4"  # Use task_id as filename when title is empty
        
        final_video_path = Path(context.task_dir) / filename
        
        # Get BGM parameters
        bgm_path = context.request.get("bgm_path")
        bgm_volume = context.request.get("bgm_volume", 0.2)
        bgm_mode = context.request.get("bgm_mode", "loop")
        
        if bgm_path:
            logger.info(f"🎵 Adding BGM: {bgm_path} (volume={bgm_volume}, mode={bgm_mode})")
        
        self.core.video.concat_videos_with_transition(
            videos=scene_videos,
            output=str(final_video_path),
            bgm_path=bgm_path,
            bgm_volume=bgm_volume,
            bgm_mode=bgm_mode
        )
        
        context.final_video_path = str(final_video_path)
        context.storyboard.final_video_path = str(final_video_path)
        
        logger.success(f"✅ Final video: {final_video_path}")
        
        # Emit completion of concatenation
        self._emit_progress(ProgressEvent(
            event_type="concatenating",
            progress=0.95,
            extra_info="complete"
        ))
        
        return context
    
    async def finalize(self, context: PipelineContext) -> PipelineContext:
        """
        Finalize and return result
        
        Args:
            context: Pipeline context
        
        Returns:
            Final context
        """
        logger.success(f"🎉 Asset-based video generation complete!")
        logger.info(f"Video: {context.final_video_path}")

        # Handle cover image (copy to task output dir)
        await self._handle_cover(context)

        # Emit completion
        self._emit_progress(ProgressEvent(
            event_type="completed",
            progress=1.0
        ))

        # Persist metadata for history tracking
        await self._persist_task_data(context)

        return context

    async def _handle_cover(self, context: PipelineContext) -> None:
        """Copy user-uploaded cover to the task output directory."""
        import shutil
        source = context.request.get("cover_image_path")
        if not source:
            return
        source_path = Path(source)
        if not source_path.exists():
            logger.warning(f"Cover image not found: {source}")
            return
        ext = source_path.suffix or ".jpg"
        dest = context.task_dir / f"{context.task_id}_cover{ext}"
        shutil.copy2(source_path, dest)
        context.cover_path = str(dest)
        logger.info(f"🖼️ Cover saved: {dest}")
    
    async def _persist_task_data(self, ctx: PipelineContext):
        """
        Persist task metadata and storyboard to filesystem for history tracking
        """
        from pathlib import Path
        
        try:
            storyboard = ctx.storyboard
            task_id = ctx.task_id
            
            if not task_id:
                logger.warning("No task_id in context, skipping persistence")
                return
            
            # Get file size
            video_path_obj = Path(ctx.final_video_path)
            file_size = video_path_obj.stat().st_size if video_path_obj.exists() else 0
            
            # Build metadata
            input_params = {
                "text": ctx.input_text,
                "mode": "asset_based",
                "title": ctx.title or "",
                "n_scenes": len(storyboard.frames) if storyboard else 0,
                "assets": ctx.request.get("assets", []),
                "intent": ctx.request.get("intent"),
                "duration": ctx.request.get("duration"),
                "source": ctx.request.get("source"),
                "voice_id": ctx.request.get("voice_id"),
                "tts_speed": ctx.request.get("tts_speed"),
            }
            
            metadata = {
                "task_id": task_id,
                "created_at": storyboard.created_at.isoformat() if storyboard and storyboard.created_at else None,
                "completed_at": storyboard.completed_at.isoformat() if storyboard and storyboard.completed_at else None,
                "status": "completed",
                
                "input": input_params,
                
                "result": {
                    "video_path": ctx.final_video_path,
                    "cover_path": getattr(ctx, "cover_path", None),
                    "duration": storyboard.total_duration if storyboard else 0,
                    "file_size": file_size,
                    "n_frames": len(storyboard.frames) if storyboard else 0
                },
                
                "config": {
                    "llm_model": self.core.config.get("llm", {}).get("model", "unknown"),
                    "llm_base_url": self.core.config.get("llm", {}).get("base_url", "unknown"),
                    "source": ctx.request.get("source", "runninghub"),
                }
            }
            
            # Save metadata
            await self.core.persistence.save_task_metadata(task_id, metadata)
            logger.info(f"💾 Saved task metadata: {task_id}")
            
            # Save storyboard
            if storyboard:
                await self.core.persistence.save_storyboard(task_id, storyboard)
                logger.info(f"💾 Saved storyboard: {task_id}")
            
        except Exception as e:
            logger.error(f"Failed to persist task data: {e}")
            # Don't raise - persistence failure shouldn't break video generation
    
    async def _generate_content_from_fixed_script(
        self, context: PipelineContext
    ) -> PipelineContext:
        """
        Build context.script from a user-provided fixed script.

        Accepts either:
        - fixed_segments: List[str] — explicit segment list from the "+" UI
        - fixed_script + split_mode: raw text that is split automatically (legacy)

        Distributes narration segments sequentially and evenly across assets.
        Assets that receive no segments (when segments < assets) are skipped.
        """
        self._emit_progress(ProgressEvent(event_type="generating_script", progress=0.16))

        # Prefer explicit segment list from the "+" button UI
        fixed_segments = context.request.get("fixed_segments")
        narration_styles_flat: list = []
        if fixed_segments:
            if fixed_segments and isinstance(fixed_segments[0], dict):
                # New format: [{"text": "...", "subtitle_style": "card"}, ...]
                narrations = [s["text"].strip() for s in fixed_segments if s.get("text", "").strip()]
                narration_styles_flat = [
                    s.get("subtitle_style", "simple_white")
                    for s in fixed_segments if s.get("text", "").strip()
                ]
            else:
                # Legacy: plain list of strings
                narrations = [s.strip() for s in fixed_segments if s and s.strip()]
        else:
            # Legacy path: split raw text
            from pixelle_video.utils.content_generators import split_narration_script
            fixed_script = context.request.get("fixed_script", "")
            split_mode = context.request.get("split_mode", "paragraph")
            if not fixed_script or not fixed_script.strip():
                raise ValueError("固定文案不能为空，请填写视频文案内容。")
            narrations = await split_narration_script(fixed_script, split_mode=split_mode)

        if not narrations:
            raise ValueError("文案段落为空，请至少添加一段文案内容。")

        # 2. Distribute segments across assets (sequential equal-size bucketing)
        assets = list(self.asset_index.keys())
        n, m = len(narrations), len(assets)

        scenes = []
        for asset_idx, asset_path in enumerate(assets):
            start = asset_idx * n // m
            end = (asset_idx + 1) * n // m
            bucket = narrations[start:end]
            if not bucket:
                continue  # fewer segments than assets — skip this asset

            bucket_styles = narration_styles_flat[start:end] if narration_styles_flat else []
            scenes.append({
                "scene_number": len(scenes) + 1,
                "asset_path": asset_path,
                "narrations": bucket,
                "narration_styles": bucket_styles,
                "duration": len(bucket) * 5,  # rough estimate only
            })

        context.script = scenes

        self._emit_progress(
            ProgressEvent(
                event_type="generating_script",
                progress=0.25,
                extra_info="complete",
            )
        )
        logger.success(
            f"✅ Fixed script: {n} segments → {len(scenes)} scenes across {m} assets"
        )
        return context

    # Helper methods

    def _get_asset_type(self, path: Path) -> str:
        """Determine asset type from file extension"""
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

        ext = path.suffix.lower()

        if ext in image_exts:
            return "image"
        elif ext in video_exts:
            return "video"
        else:
            return "unknown"

    def _concat_segments(self, videos: list, output: str) -> None:
        """Concatenate sub-segments within a scene using ffmpeg concat filter (no transition)."""
        import subprocess
        n = len(videos)
        cmd = ["ffmpeg"]
        for v in videos:
            cmd.extend(["-i", v])
        filter_str = "".join(f"[{i}:v][{i}:a]" for i in range(n))
        filter_str += f"concat=n={n}:v=1:a=1[v][a]"
        cmd.extend([
            "-filter_complex", filter_str,
            "-map", "[v]", "-map", "[a]",
            "-vcodec", "libx264", "-acodec", "aac",
            "-pix_fmt", "yuv420p", "-crf", "23", "-preset", "medium",
            "-y", output,
        ])
        subprocess.run(cmd, check=True, capture_output=True)
        logger.debug(f"_concat_segments: {n} segments → {output}")

    def _build_video_scene(self, source: str, chunks: list, output: str) -> None:
        """
        Build a scene video for a VIDEO asset in a single ffmpeg call.

        The source video is scaled to 1080×1920 (contain / letterbox), then each
        subtitle PNG is overlaid only during its time window, and all TTS audio
        tracks are concatenated.  Because there is no clip-extraction or segment
        concat, playback is completely continuous — zero stutter at chunk boundaries.

        If the source video is SHORTER than the narration audio total, the source
        is looped (replicated via split) and the loop boundary is smoothed with a
        0.5s xfade dissolve, so the picture never freezes on the last frame.

        chunks: list of dicts with keys: audio, composed, duration, t_start
        """
        import math
        import subprocess
        import ffmpeg as _ffmpeg

        n = len(chunks)
        total_dur = sum(c["duration"] for c in chunks)
        W, H = 1080, 1920
        XFADE_DUR = 0.5  # seconds of crossfade at each loop boundary

        # Probe source duration to decide whether we need to loop the source
        try:
            src_dur = float(_ffmpeg.probe(source)["format"]["duration"])
        except Exception:
            logger.warning(f"Failed to probe duration for {source}; assuming no loop needed")
            src_dur = total_dur + 1.0

        # How many copies of the source do we need?
        #   total covered = n_copies * src_dur - (n_copies - 1) * XFADE_DUR
        #   require: covered >= total_dur
        #   => n_copies >= (total_dur - XFADE_DUR) / (src_dur - XFADE_DUR)
        if total_dur <= src_dur or src_dur <= XFADE_DUR * 2:
            n_copies = 1
        else:
            n_copies = max(2, math.ceil((total_dur - XFADE_DUR) / (src_dur - XFADE_DUR)))

        cmd = ["ffmpeg", "-y"]
        # [0] source video — full file, no pre-trimming
        cmd += ["-i", source]
        # [1 … n] subtitle PNGs, looped so the overlay filter can hold them
        # for their entire time window
        for c in chunks:
            cmd += ["-loop", "1", "-i", c["composed"]]
        # [n+1 … 2n] TTS audio clips
        for c in chunks:
            cmd += ["-i", c["audio"]]

        # ── filter_complex ──────────────────────────────────────────────────
        parts = []

        # Scale source video to template size (contain = letterbox black bars)
        scale_pad = (
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black"
        )

        if n_copies == 1:
            # Source is long enough — no loop, single pass
            parts.append(f"[0:v]{scale_pad}[scaled]")
        else:
            # Replicate the scaled source N times, then crossfade the seams
            parts.append(
                f"[0:v]{scale_pad},split={n_copies}"
                + "".join(f"[s{i}]" for i in range(n_copies))
            )
            current = "[s0]"
            for i in range(1, n_copies):
                is_last = i == n_copies - 1
                next_label = "[scaled]" if is_last else f"[xf{i}]"
                offset = i * (src_dur - XFADE_DUR)
                parts.append(
                    f"{current}[s{i}]xfade=transition=fade:"
                    f"duration={XFADE_DUR}:offset={offset:.4f}{next_label}"
                )
                current = next_label

        # Chain subtitle overlays; each is visible only during its time window
        vref = "[scaled]"
        for idx, c in enumerate(chunks):
            t0, t1 = c["t_start"], c["t_start"] + c["duration"]
            vout = f"[v{idx}]"
            parts.append(
                f"{vref}[{idx + 1}:v]overlay=enable='between(t,{t0:.4f},{t1:.4f})'{vout}"
            )
            vref = vout

        # Trim video to exact scene duration (prevents trailing source footage)
        parts.append(f"{vref}trim=duration={total_dur:.4f},setpts=PTS-STARTPTS[vout]")

        # Concatenate TTS audio tracks
        audio_inputs = "".join(f"[{n + 1 + idx}:a]" for idx in range(n))
        parts.append(f"{audio_inputs}concat=n={n}:v=0:a=1[aout]")

        cmd += ["-filter_complex", "; ".join(parts)]
        cmd += ["-map", "[vout]", "-map", "[aout]"]
        cmd += [
            "-c:v", "libx264", "-c:a", "aac",
            "-pix_fmt", "yuv420p", "-crf", "23", "-preset", "medium",
            output,
        ]

        subprocess.run(cmd, check=True, capture_output=True)
        logger.debug(
            f"_build_video_scene: {n} chunks, narration={total_dur:.2f}s, "
            f"src={src_dur:.2f}s, loops={n_copies} → {output}"
        )

