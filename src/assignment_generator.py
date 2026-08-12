#!/usr/bin/env python3
"""
YouTube Video Assignment Generator

This script generates educational assignments based on YouTube video summaries.
It uses AI to create comprehensive, practical assignments that help students
apply and reinforce their learning from the video content.

Features:
- Parallel processing for multiple video assignments
- Structured assignment templates
- Difficulty-appropriate content generation
- Comprehensive educational assessment
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from dotenv import load_dotenv
from openai import OpenAI

from backend.storage.repository import RunRepository
from backend.storage.settings import get_settings
from src.utils import (
    atomic_write,
    get_config,
    get_worker_count,
    setup_logging,
    sha256_text,
)

load_dotenv()


class YouTubeAssignmentGenerator:
    """
    Generates educational assignments based on YouTube video summaries.

    This class processes video summaries and creates comprehensive assignments
    that help students apply the concepts they've learned from the videos.
    """

    def __init__(
        self,
        repository: Optional[object] = None,
        run_id: Optional[str] = None,
        pipeline_output_folder: Optional[str] = None,
        output_folder: Optional[str] = None,
        num_workers: Optional[int] = None,
        prompt_file: Optional[str] = None,
    ):
        """
        Initialize the assignment generator.

        Args:
            repository (object): RunRepository instance that owns all records.
            run_id (str): Identifier for this run.
            pipeline_output_folder (str): Kept for backwards compatibility; ignored
                when ``repository`` and ``run_id`` are provided.
            output_folder (Optional[str]): Path to save assignments. If None, uses
                ``repository.artifact_root / run_id / assignments``.
            num_workers (Optional[int]): Number of concurrent workers for parallel processing.
            prompt_file (Optional[str]): Path to the prompt YAML file. If None, uses default.
        """
        # Initialize configuration and logging
        setup_logging()

        if repository is None:
            raise ValueError("YouTubeAssignmentGenerator requires a RunRepository instance.")
        if run_id is None:
            if pipeline_output_folder:
                run_id = Path(pipeline_output_folder).name
            else:
                raise ValueError("run_id is required for the repository-backed generator.")
        if run_id is None or not run_id:
            raise ValueError("run_id must be a non-empty string.")

        self.repository = repository
        self.run_id = run_id
        self.pipeline_output_folder = repository.artifact_root / run_id
        self.summaries_folder = self.pipeline_output_folder / "summaries"

        # Set up assignments output folder
        if output_folder:
            self.assignments_folder = Path(output_folder)
        else:
            self.assignments_folder = self.pipeline_output_folder / "assignments"

        # Ensure assignments folder exists
        self.assignments_folder.mkdir(parents=True, exist_ok=True)

        # Configure OpenAI settings from config
        self.model = get_config("api.openai.model", "openai/gpt-4o-mini")
        self.timeout = get_config("api.openai.timeout", 180)

        # Determine number of workers for parallel processing
        self.num_workers = get_worker_count(num_workers)

        # Load prompts from YAML file
        if prompt_file:
            self.prompt_file = Path(prompt_file)
        else:
            # Use default prompt file in src/prompts
            script_dir = Path(__file__).parent
            self.prompt_file = script_dir / "prompts" / "assignment_generator.yaml"

        self.prompts = self._load_prompts()

        # Initialize OpenAI client
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable not found")

        self.openai_client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")

        print(f"[INIT] Assignment generator initialized")
        print(f"[INIT] Run: {self.run_id}")
        print(f"[INIT] Summaries folder: {self.summaries_folder}")
        print(f"[INIT] Assignments folder: {self.assignments_folder}")
        print(f"[INIT] Workers: {self.num_workers}")
        print(f"[INIT] Model: {self.model}")

    def _load_prompts(self) -> Dict:
        """Load prompts from YAML file."""
        try:
            with open(self.prompt_file, "r", encoding="utf-8") as f:
                prompts = yaml.safe_load(f)
            print(f"[PROMPTS] Loaded prompts from: {self.prompt_file}")
            return prompts
        except Exception as e:
            print(f"[PROMPTS] Error loading prompts from {self.prompt_file}: {e}")
            raise

    def load_video_metadata(self) -> Dict[str, Dict]:
        """
        Load video metadata from the repository.

        Returns:
            Dict[str, Dict]: Video metadata indexed by video ID.
        """
        print("[META] Loading video metadata from repository...")
        video_metadata = {}
        for video in self.repository.get_videos(self.run_id):
            video_id = video.get("video_id")
            if video_id:
                video_metadata[video_id] = video
        print(f"[META] Loaded metadata for {len(video_metadata)} videos")
        return video_metadata

    def load_summary_data(self) -> Dict[str, Dict]:
        """
        Load summary data from the repository.

        Returns:
            Dict[str, Dict]: Summary data indexed by video ID.
        """
        print("[SUMMARY] Loading summary data from repository...")
        summary_data = {}
        for record in self.repository.get_summaries(self.run_id):
            if record.get("status") != "succeeded":
                continue
            video_id = record.get("video_id")
            try:
                summary = json.loads(record["data"])
            except (KeyError, TypeError, json.JSONDecodeError):
                print(f"[SUMMARY] ✗ Error loading summary for: {video_id}")
                continue
            summary_data[video_id] = summary
            print(f"[SUMMARY] ✓ Loaded summary for: {video_id}")
        print(f"[SUMMARY] Successfully loaded {len(summary_data)} summaries")
        return summary_data

    def generate_assignments(
        self, video_metadata: Dict[str, Dict], summary_data: Dict[str, Dict]
    ) -> Dict[str, bool]:
        """
        Generate assignments for all videos with summaries.

        Args:
            video_metadata (Dict[str, Dict]): Video metadata indexed by video ID.
            summary_data (Dict[str, Dict]): Summary data indexed by video ID.

        Returns:
            Dict[str, bool]: Results of assignment generation for each video ID.
        """
        if not summary_data:
            print("[ASSIGNMENTS] No summaries available for assignment generation")
            return {}

        print(
            f"[ASSIGNMENTS] Starting assignment generation for {len(summary_data)} videos"
        )

        # Determine processing method
        num_workers = self.num_workers
        video_count = len(summary_data)

        if num_workers == 0 or video_count == 1:
            print(
                f"[ASSIGNMENTS] Using sequential processing for {video_count} video(s)"
            )
            assignment_results, records = self._generate_assignments_sequential(
                video_metadata, summary_data
            )
        else:
            print(
                f"[ASSIGNMENTS] Using parallel processing with {num_workers} workers for {video_count} videos"
            )
            assignment_results, records = self._generate_assignments_parallel(
                video_metadata, summary_data
            )

        # Persist repository records for generated assignments
        if records:
            self.repository.upsert_assignments(
                self.run_id,
                records,
                {"num_workers": self.num_workers, "model": self.model},
            )

        # Log results
        successful_assignments = sum(assignment_results.values())
        print(
            f"[ASSIGNMENTS] ✓ Completed: {successful_assignments}/{len(assignment_results)} assignments successful"
        )

        return assignment_results

    def _generate_assignments_parallel(
        self, video_metadata: Dict[str, Dict], summary_data: Dict[str, Dict]
    ) -> Tuple[Dict[str, bool], List[Dict]]:
        """Generate assignments in parallel, returning results and repository records."""
        assignment_results = {}
        records = []

        with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
            future_to_video_id = {}
            for video_id in summary_data.keys():
                video_meta = video_metadata.get(video_id, {})
                summary = summary_data.get(video_id, {})
                future = executor.submit(
                    self._generate_single_assignment,
                    video_id,
                    video_meta,
                    summary,
                )
                future_to_video_id[future] = video_id

            for future in as_completed(future_to_video_id):
                video_id = future_to_video_id[future]
                try:
                    success, record = future.result()
                    assignment_results[video_id] = success
                    if record:
                        records.append(record)
                except Exception as e:
                    print(f"[ASSIGNMENTS] Error processing {video_id}: {str(e)}")
                    assignment_results[video_id] = False

        return assignment_results, records

    def _generate_assignments_sequential(
        self, video_metadata: Dict[str, Dict], summary_data: Dict[str, Dict]
    ) -> Tuple[Dict[str, bool], List[Dict]]:
        """
        Generate assignments sequentially as fallback.

        Args:
            video_metadata (Dict): Video metadata.
            summary_data (Dict): Summary data.

        Returns:
            Tuple[Dict[str, bool], List[Dict]]: (results, repository records).
        """
        assignment_results = {}
        records = []

        for video_id in summary_data.keys():
            video_meta = video_metadata.get(video_id, {})
            summary = summary_data.get(video_id, {})

            success, record = self._generate_single_assignment(video_id, video_meta, summary)
            assignment_results[video_id] = success
            if record:
                records.append(record)

        print(
            f"[ASSIGNMENTS] Generated assignments for {len(assignment_results)} videos (sequential)"
        )
        return assignment_results, records

    def _generate_single_assignment(
        self, video_id: str, video_meta: Dict, summary: Dict
    ) -> Tuple[bool, Optional[Dict]]:
        """
        Generate an assignment for a single video.

        Args:
            video_id (str): Video ID.
            video_meta (Dict): Video metadata.
            summary (Dict): Video summary data.

        Returns:
            Tuple[bool, Optional[Dict]]: (success flag, repository record or None).
        """
        record = None
        try:
            # Get thread ID for logging
            import threading

            thread_id = threading.current_thread().name.split("-")[-1]

            title = video_meta.get("title", "Unknown Title")
            print(f"[ASSIGN-{thread_id}] Generating assignment for: {title[:50]}...")

            start_time = time.time()

            # Extract key information from summary
            high_level_overview = summary.get("high_level_overview", "")
            technical_breakdown = summary.get("technical_breakdown", [])
            insights = summary.get("insights", [])
            applications = summary.get("applications", [])

            # Determine difficulty level from video metadata or summary
            difficulty = "Intermediate"  # Default
            # You could extract difficulty from comparison results if available

            # Format technical breakdown for the prompt
            technical_text = ""
            if technical_breakdown:
                for item in technical_breakdown:
                    item_type = item.get("type", "")
                    if item_type == "tool":
                        name = item.get("name", "")
                        purpose = item.get("purpose", "")
                        technical_text += f"- **Tool: {name}** - {purpose}\n"
                    elif item_type == "process":
                        step_num = item.get("step_number", "")
                        description = item.get("description", "")
                        technical_text += f"- **Step {step_num}:** {description}\n"
                    elif item_type == "architecture":
                        description = item.get("description", "")
                        technical_text += f"- **Architecture:** {description}\n"

            # Create the user prompt using the template
            user_prompt = self.prompts["user_prompt_template"].format(
                title=title,
                channel=video_meta.get("channel", "Unknown Channel"),
                difficulty=difficulty,
                summary=high_level_overview,
                technical_breakdown=technical_text,
                insights="\n".join([f"- {insight}" for insight in insights]),
                applications="\n".join([f"- {app}" for app in applications]),
            )

            # Generate assignment using OpenAI
            api_start = time.time()
            response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.prompts["system_prompt"]},
                    {"role": "user", "content": user_prompt},
                ],
                timeout=self.timeout,
            )
            api_time = time.time() - api_start

            assignment_content = response.choices[0].message.content.strip()

            # Save assignment to file (atomically)
            assignment_filename = f"{video_id}_assignment.md"
            assignment_path = self.assignments_folder / assignment_filename

            # Create assignment metadata
            assignment_metadata = {
                "video_id": video_id,
                "video_title": title,
                "channel": video_meta.get("channel", "Unknown Channel"),
                "video_url": video_meta.get("url", ""),
                "difficulty_level": difficulty,
                "generated_at": datetime.now().isoformat(),
                "model_used": self.model,
                "generation_time_seconds": round(time.time() - start_time, 2),
                "api_time_seconds": round(api_time, 2),
            }

            # Combine metadata and content
            full_assignment = f"""---
# Assignment Metadata
{yaml.dump(assignment_metadata, default_flow_style=False)}
---

{assignment_content}
"""

            atomic_write(assignment_path, full_assignment)

            record = {
                "video_id": video_id,
                "artifact_path": str(assignment_path),
                "content_hash": sha256_text(full_assignment),
                "byte_size": len(full_assignment.encode("utf-8")),
                "metadata": assignment_metadata,
                "display_metadata": {
                    key: str(value)
                    for key, value in assignment_metadata.items()
                    if key in ("difficulty_level", "model_used", "channel", "video_id", "video_title", "video_url")
                    and value not in (None, "")
                },
                "status": "succeeded",
                "error": None,
            }

            total_time = time.time() - start_time
            print(
                f"[ASSIGN-{thread_id}] ✓ Generated assignment for {video_id} in {total_time:.2f}s (API: {api_time:.2f}s)"
            )
            print(f"[ASSIGN-{thread_id}] ✓ Saved: {assignment_filename}")

            return True, record

        except Exception as e:
            total_time = time.time() - start_time
            print(
                f"[ASSIGN-{thread_id}] ✗ Error generating assignment for {video_id} after {total_time:.2f}s: {str(e)}"
            )
            return False, record

    def run_assignment_generation(self) -> Dict:
        """
        Run the complete assignment generation process.

        Returns:
            Dict: Results including counts and file paths.
        """
        start_time = time.time()
        print("🚀 Starting YouTube Assignment Generation")
        print("=" * 60)

        # Step 1: Load video metadata
        print("\n📊 STEP 1: Loading Video Metadata")
        print("-" * 40)
        video_metadata = self.load_video_metadata()

        # Step 2: Load summary data
        print("\n📄 STEP 2: Loading Summary Data")
        print("-" * 40)
        summary_data = self.load_summary_data()

        if not summary_data:
            print(
                "❌ No summaries found. Cannot generate assignments without summaries."
            )
            return {
                "success": False,
                "error": "No summaries available",
                "assignments_generated": 0,
                "total_videos": len(video_metadata),
            }

        # Step 3: Generate assignments
        print("\n📝 STEP 3: Generating Assignments")
        print("-" * 40)
        assignment_results = self.generate_assignments(video_metadata, summary_data)

        # Calculate final results
        end_time = time.time()
        duration = end_time - start_time
        successful_assignments = sum(assignment_results.values())

        # Create results summary
        results = {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": round(duration, 2),
            "total_videos": len(video_metadata),
            "videos_with_summaries": len(summary_data),
            "assignments_generated": successful_assignments,
            "assignments_folder": str(self.assignments_folder),
            "assignment_results": assignment_results,
        }

        # Print final summary
        print("\n" + "=" * 80)
        print("ASSIGNMENT GENERATION COMPLETED")
        print("=" * 80)
        print(f"[ASSIGNMENTS] ✓ Total Videos: {len(video_metadata)}")
        print(f"[ASSIGNMENTS] ✓ Videos with Summaries: {len(summary_data)}")
        print(f"[ASSIGNMENTS] ✓ Assignments Generated: {successful_assignments}")
        print(
            f"[ASSIGNMENTS] ✓ Success Rate: {successful_assignments/len(summary_data)*100:.1f}%"
        )
        print(f"[ASSIGNMENTS] ✓ Total Duration: {duration:.2f} seconds")
        print(f"[ASSIGNMENTS] ✓ Assignments Folder: {self.assignments_folder}")
        print("=" * 80)

        return results


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate educational assignments from YouTube video summaries",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python assignment_generator.py --pipeline-folder pipeline_output
  python assignment_generator.py --pipeline-folder custom_output --workers 4
  python assignment_generator.py --pipeline-folder output --assignments-folder my_assignments
        """,
    )

    parser.add_argument(
        "--pipeline-folder",
        "-p",
        type=str,
        default="pipeline_output",
        help="Path to pipeline output folder containing summaries (default: pipeline_output)",
    )

    parser.add_argument(
        "--assignments-folder",
        "-a",
        type=str,
        help="Path to save assignments (default: pipeline_folder/assignments)",
    )

    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        help="Number of concurrent workers for parallel processing",
    )

    parser.add_argument(
        "--prompt-file",
        type=str,
        help="Path to custom prompt YAML file",
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    return parser.parse_args()


def main():
    """Main function for the assignment generator."""
    args = parse_arguments()

    print("📝 YouTube Assignment Generator")
    print("=" * 80)

    # Validate OpenRouter API key
    if not os.getenv("OPENROUTER_API_KEY"):
        print("❌ Error: OPENROUTER_API_KEY environment variable not found")
        print("\nSetup Instructions:")
        print("1. Get OpenRouter API key: https://openrouter.ai/keys")
        print("2. Set environment variable: export OPENROUTER_API_KEY='your_key_here'")
        print("3. Or create a .env file with: OPENROUTER_API_KEY=your_key_here")
        sys.exit(1)

    try:
        # Initialize assignment generator (repository-backed)
        print(f"🔧 Initializing assignment generator...")
        settings = get_settings()
        repository = RunRepository(settings["database_path"], settings["artifact_root"])
        generator = YouTubeAssignmentGenerator(
            repository=repository,
            pipeline_output_folder=args.pipeline_folder,
            output_folder=args.assignments_folder,
            num_workers=args.workers,
            prompt_file=args.prompt_file,
        )

        # Run assignment generation
        print(f"🚀 Starting assignment generation...")
        results = generator.run_assignment_generation()

        if results["success"]:
            print(f"\n✅ Assignment generation completed successfully!")
            print(f"📁 Check assignments in: {results['assignments_folder']}")
        else:
            print(
                f"\n❌ Assignment generation failed: {results.get('error', 'Unknown error')}"
            )
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n🛑 Assignment generation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Assignment generation failed with error: {str(e)}")
        if args.verbose:
            import traceback

            print("\nDetailed error:")
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
