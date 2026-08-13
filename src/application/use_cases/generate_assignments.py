from __future__ import annotations

import json
from typing import Optional

from src.application.dto.assignments import AssignmentGenerationInput, AssignmentGenerationOutput
from src.application.ports.provider_ports import (
    RunRepositoryPort,
    AssignmentGeneratorPort,
    CachePort,
    UsageLedgerPort,
)
from src.domain.services.hash_computer import RunHashComputer
from src.domain.exceptions import DomainError, ProviderError
from src.infrastructure.llm.openai.adapter import OpenAIAssignmentAdapter
from src.infrastructure.llm.base import SettingsLoader


class GenerateAssignmentsUseCase:
    """Orchestrates assignment generation for a pipeline run.

    This use case formalizes the assignment orchestration previously
    embedded in backend.services.pipeline_service.PipelineService.generate_assignments().
    """

    def __init__(
        self,
        run_repository: RunRepositoryPort,
        cache: CachePort,
        settings: SettingsLoader,
        usage_ledger: Optional[UsageLedgerPort] = None,
    ) -> None:
        self._repo = run_repository
        self._cache = cache
        self._settings = settings
        self._usage = usage_ledger

    async def execute(self, input: AssignmentGenerationInput) -> AssignmentGenerationOutput:
        run = await self._ensure_run(input.run_id)

        assignment_settings = self._assignment_settings(input)
        settings_key = self._settings_hash(assignment_settings)

        if not input.refresh:
            assignments = await self._repo.get_assignments(input.run_id)
            input_hash = await self._repo.derived_input_hash(input.run_id)
            all_fresh = (
                bool(assignments)
                and all(
                    record["status"] == "succeeded"
                    and record["input_hash"] == input_hash
                    and record["settings_hash"] == settings_key
                    for record in assignments
                )
            )
            if all_fresh:
                return AssignmentGenerationOutput(
                    run_id=input.run_id,
                    source_folder=run["source_folder"],
                    status="cached",
                    detail="Reused existing assignments from the run folder.",
                )

        video_metadata = {video["video_id"]: video for video in await self._repo.get_videos(input.run_id)}
        summary_data: dict[str, dict] = {}
        for record in await self._repo.get_summaries(input.run_id):
            if record["status"] != "succeeded":
                continue
            try:
                summary_data[record["video_id"]] = json.loads(record["data"])
            except (TypeError, json.JSONDecodeError):
                continue

        await self._repo.start_job(input.run_id, "assignments", assignment_settings)

        try:
            assignment_generator = OpenAIAssignmentAdapter(
                settings=self._settings,
                usage_ledger=self._usage,
            )

            videos = await self._repo.get_videos(input.run_id)
            video_dicts = [{"video_id": v["video_id"], "title": v.get("title", ""), "channel": v.get("channel", "")} for v in videos]
            summaries = [summary_data.get(v["video_id"], {}) for v in video_dicts if v["video_id"] in summary_data]

            results = await assignment_generator.generate_assignments_batch(
                video_dicts,
                summaries,
                prompt_version=self._settings("prompts.assignment.version", "v1"),
                model=self._settings("api.openai.model", "openai/gpt-5-mini"),
            )

            assignment_records = []
            for result in results:
                assignment_records.append({
                    "video_id": result.video_id,
                    "artifact_path": result.artifact_path,
                    "content_hash": result.content_hash,
                    "byte_size": result.byte_size,
                    "markdown": result.markdown,
                    "sections": result.sections,
                    "checklist": result.checklist,
                    "metadata": result.metadata,
                    "display_metadata": result.display_metadata,
                    "status": "succeeded",
                    "error": None,
                })

        except Exception as exc:
            await self._repo.fail_job(input.run_id, "assignments", str(exc))
            raise ProviderError(f"Assignment generation failed: {exc}", provider="openai") from exc

        await self._repo.upsert_assignments(
            input.run_id,
            assignment_records,
            assignment_settings,
        )
        await self._repo.finish_job(input.run_id, "assignments")

        return AssignmentGenerationOutput(
            run_id=input.run_id,
            source_folder=run["source_folder"],
            status="updated",
            detail=f"Assignment generation completed for {len(assignment_records)} summaries.",
        )

    async def _ensure_run(self, run_id: str) -> dict:
        run = await self._repo.get_run(run_id)
        if run is None:
            raise DomainError(f"Run '{run_id}' not found.")
        return run

    def _assignment_settings(self, input: AssignmentGenerationInput) -> dict:
        return {
            "num_workers": self._get_worker_count(input.num_workers),
            "model": self._settings("api.openai.model", "openai/gpt-5-mini"),
        }

    def _settings_hash(self, settings: dict) -> str:
        import hashlib
        import json
        payload = json.dumps(settings, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _get_worker_count(self, num_workers: Optional[int]) -> int:
        if num_workers is not None:
            return max(0, num_workers)

        workers_config = self._settings("processing.workers", {})
        if not workers_config.get("auto_detect", True):
            return workers_config.get("min_workers", 2)

        import os
        cpu_count = os.cpu_count() or 1
        cpu_ratio = workers_config.get("cpu_ratio", 0.5)
        min_workers = workers_config.get("min_workers", 2)
        max_workers = workers_config.get("max_workers", 16)

        return max(min_workers, min(max_workers, int(cpu_count * cpu_ratio)))