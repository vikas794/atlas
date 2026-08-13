from __future__ import annotations

from fastapi import APIRouter, Depends

from src.application.dto.search import SearchInput, SearchOutput
from src.application.dto.transcripts import TranscriptGenerationInput, TranscriptGenerationOutput
from src.application.dto.summaries import SummaryGenerationInput, SummaryGenerationOutput
from src.application.dto.comparison import ComparisonGenerationInput, ComparisonGenerationOutput
from src.application.dto.assignments import AssignmentGenerationInput, AssignmentGenerationOutput
from src.application.use_cases import (
    SearchPipelineUseCase,
    GenerateTranscriptsUseCase,
    GenerateSummariesUseCase,
    GenerateComparisonUseCase,
    GenerateAssignmentsUseCase,
)
from src.transport.http.fastapi.dependencies import (
    get_search_use_case,
    get_transcripts_use_case,
    get_summaries_use_case,
    get_comparison_use_case,
    get_assignments_use_case,
)
from src.transport.http.fastapi.schemas.pipeline import (
    ArtifactGenerationRequest,
    PipelineActionResponse,
    SearchRequest,
)

router = APIRouter(prefix="/api", tags=["pipeline"])


def _to_search_input(request: SearchRequest) -> SearchInput:
    return SearchInput(
        query=request.query,
        max_videos=request.max_videos,
        transcript_language=request.transcript_language,
        num_workers=request.num_workers,
        use_env_keys=request.use_env_keys,
        openrouter_api_key=request.openrouter_api_key,
        youtube_api_key=request.youtube_api_key,
        prefer_cache=request.prefer_cache,
    )


def _to_transcript_input(run_id: str, request: ArtifactGenerationRequest) -> TranscriptGenerationInput:
    return TranscriptGenerationInput(
        run_id=run_id,
        refresh=request.refresh,
        num_workers=request.num_workers,
        transcript_language=request.transcript_language,
    )


def _to_summary_input(run_id: str, request: ArtifactGenerationRequest) -> SummaryGenerationInput:
    return SummaryGenerationInput(
        run_id=run_id,
        refresh=request.refresh,
        num_workers=request.num_workers,
        transcript_language=request.transcript_language,
    )


def _to_comparison_input(run_id: str, request: ArtifactGenerationRequest) -> ComparisonGenerationInput:
    return ComparisonGenerationInput(
        run_id=run_id,
        refresh=request.refresh,
        num_workers=request.num_workers,
        use_ai_insights=request.use_ai_insights,
    )


def _to_assignment_input(run_id: str, request: ArtifactGenerationRequest) -> AssignmentGenerationInput:
    return AssignmentGenerationInput(
        run_id=run_id,
        refresh=request.refresh,
        num_workers=request.num_workers,
    )


def _to_action_response(output) -> PipelineActionResponse:
    return PipelineActionResponse(
        run_id=output.run_id,
        source_folder=output.source_folder,
        status=output.status,
        detail=output.detail,
        transcripts_available=output.transcripts_available,
    )


@router.post("/pipeline/search", response_model=PipelineActionResponse)
async def search_pipeline(
    request: SearchRequest,
    use_case: SearchPipelineUseCase = Depends(get_search_use_case),
) -> PipelineActionResponse:
    input_dto = _to_search_input(request)
    output = await use_case.execute(input_dto)
    return _to_action_response(output)


@router.post("/runs/{run_id}/transcripts", response_model=PipelineActionResponse)
async def generate_transcripts(
    run_id: str,
    request: ArtifactGenerationRequest,
    use_case: GenerateTranscriptsUseCase = Depends(get_transcripts_use_case),
) -> PipelineActionResponse:
    input_dto = _to_transcript_input(run_id, request)
    output = await use_case.execute(input_dto)
    return _to_action_response(output)


@router.post("/runs/{run_id}/summaries", response_model=PipelineActionResponse)
async def generate_summaries(
    run_id: str,
    request: ArtifactGenerationRequest,
    use_case: GenerateSummariesUseCase = Depends(get_summaries_use_case),
) -> PipelineActionResponse:
    input_dto = _to_summary_input(run_id, request)
    output = await use_case.execute(input_dto)
    return _to_action_response(output)


@router.post("/runs/{run_id}/comparison", response_model=PipelineActionResponse)
async def generate_comparison(
    run_id: str,
    request: ArtifactGenerationRequest,
    use_case: GenerateComparisonUseCase = Depends(get_comparison_use_case),
) -> PipelineActionResponse:
    input_dto = _to_comparison_input(run_id, request)
    output = await use_case.execute(input_dto)
    return _to_action_response(output)


@router.post("/runs/{run_id}/assignments", response_model=PipelineActionResponse)
async def generate_assignments(
    run_id: str,
    request: ArtifactGenerationRequest,
    use_case: GenerateAssignmentsUseCase = Depends(get_assignments_use_case),
) -> PipelineActionResponse:
    input_dto = _to_assignment_input(run_id, request)
    output = await use_case.execute(input_dto)
    return _to_action_response(output)