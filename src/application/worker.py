"""Background worker for the playlist processing pipeline (PRD section 15, 16, M2).

Runs two kinds of work off the request path, so no HTTP request ever blocks on long work:

* **Video jobs** -- queued videos (including ``transcript_ready`` ones whose task died, and
  "regenerate" requests, which re-queue the video) and transiently failed videos whose
  scheduled ``next_retry_at`` has arrived, executed through ``ProcessVideoUseCase`` with
  bounded concurrency. A transient failure whose retries are exhausted has no
  ``next_retry_at`` and is never picked up again (manual retry only).
* **Publish jobs** -- ``enqueue_publish`` queues a playlist for ``PublishPlaylistUseCase.run``;
  its progress is the playlist's ``status`` (``publishing`` -> ``published`` / ``publish_failed``).

On startup it performs crash recovery: in-flight video stages are reset (PRD section 15) and
playlists left ``publishing`` are re-queued (publishing is idempotent, so resuming is safe).
"""

from __future__ import annotations

import asyncio
import logging

from src.application.use_cases.playlist.process_video import ProcessVideoUseCase
from src.application.use_cases.playlist.publish import PublishPlaylistUseCase
from src.domain.interfaces.playlist_repository import PlaylistRepositoryPort
from src.domain.models.playlist import Stage

logger = logging.getLogger(__name__)


class PlaylistWorker:
    """Background queue worker consuming video and publish jobs."""

    def __init__(
        self,
        repo: PlaylistRepositoryPort,
        process_video: ProcessVideoUseCase,
        publish: PublishPlaylistUseCase | None = None,
        max_concurrent_videos: int = 2,
        poll_interval_seconds: float = 2.0,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        self.repo = repo
        self.process_video = process_video
        self.publish = publish
        self.max_concurrent_videos = max(1, max_concurrent_videos)
        # Transcript fetches are serialized and paced by the provider anyway.
        self.max_concurrent_fetches = 1
        self.poll_interval_seconds = poll_interval_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds

        self._wake_event = asyncio.Event()
        self._stopping = False
        self._loop_task: asyncio.Task | None = None
        self._active_tasks: set[asyncio.Task] = set()  # notes pool
        self._fetch_tasks: set[asyncio.Task] = set()  # transcript pool
        self._publish_tasks: set[asyncio.Task] = set()
        self._in_flight_video_ids: set[int] = set()
        self._publish_queue: list[int] = []
        self._publishing_ids: set[int] = set()

    async def start(self) -> None:
        """Start the worker and perform startup crash recovery."""
        self._wake_event = asyncio.Event()
        logger.info("Starting PlaylistWorker (concurrency=%d)", self.max_concurrent_videos)
        recovered = await self.repo.recover_in_flight_videos()
        if recovered > 0:
            logger.info("Crash recovery reset %d in-flight videos to queued", recovered)
        if self.publish is not None:
            for playlist_pk in await self.repo.list_publishing_playlists():
                logger.info(
                    "Crash recovery re-queued an interrupted publish",
                    extra={"playlist_id": playlist_pk},
                )
                self._enqueue(playlist_pk)

        self._stopping = False
        self._loop_task = asyncio.create_task(self._run_loop(), name="playlist-worker-loop")

    async def stop(self) -> None:
        """Stop the worker: cancel the loop, give active jobs a short grace period, cancel the rest.

        A job cancelled mid-flight leaves its video in an active stage; the next startup's
        crash recovery re-queues it, and the stored transcript (if any) is reused.
        """
        logger.info("Stopping PlaylistWorker...")
        self._stopping = True
        self._wake_event.set()

        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        running = {
            t
            for t in (self._active_tasks | self._fetch_tasks | self._publish_tasks)
            if not t.done()
        }
        if running:
            logger.info(
                "Waiting up to %.1fs for %d active job(s)...",
                self.shutdown_timeout_seconds,
                len(running),
            )
            _done, pending = await asyncio.wait(running, timeout=self.shutdown_timeout_seconds)
            for task in pending:
                task.cancel()
            if pending:
                logger.warning("Cancelled %d job(s) that did not finish in time", len(pending))
                await asyncio.gather(*pending, return_exceptions=True)

        logger.info("PlaylistWorker stopped.")

    def wake(self) -> None:
        """Notify the worker that new work was queued, so it looks immediately."""
        self._wake_event.set()

    def enqueue_publish(self, playlist_pk: int) -> None:
        """Queue a background publish for ``playlist_pk`` and wake the worker."""
        self._enqueue(playlist_pk)
        self.wake()

    def _enqueue(self, playlist_pk: int) -> None:
        if playlist_pk not in self._publish_queue and playlist_pk not in self._publishing_ids:
            self._publish_queue.append(playlist_pk)

    async def _run_loop(self) -> None:
        while not self._stopping:
            try:
                processed_any = await self._tick()
                if not processed_any:
                    try:
                        await asyncio.wait_for(
                            self._wake_event.wait(), timeout=self.poll_interval_seconds
                        )
                    except TimeoutError:
                        pass
                    finally:
                        self._wake_event.clear()
                else:
                    # Brief yield if work was dispatched
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error in playlist worker loop: %s", exc)
                await asyncio.sleep(self.poll_interval_seconds)

    async def _tick(self) -> bool:
        """Dispatch whatever is runnable. Returns True when something was started."""
        self._active_tasks = {t for t in self._active_tasks if not t.done()}
        self._fetch_tasks = {t for t in self._fetch_tasks if not t.done()}
        self._publish_tasks = {t for t in self._publish_tasks if not t.done()}
        dispatched = self._dispatch_publishes()

        # Notes pool first: transcript_ready videos wait here for an AI slot. The fetch
        # pool below never waits on it, so transcripts keep downloading while notes run.
        notes_slots = self.max_concurrent_videos - len(self._active_tasks)
        if notes_slots > 0:
            ready = await self.repo.list_queued_videos(
                limit=notes_slots + len(self._in_flight_video_ids),
                stages=(Stage.TRANSCRIPT_READY,),
            )
            dispatched += self._start(ready, "notes", self._active_tasks, notes_slots)

        # Fetch pool: queued videos and due retries (a retry re-queues, skips a stored
        # transcript and hands over to the notes pool).
        fetch_slots = self.max_concurrent_fetches - len(self._fetch_tasks)
        if fetch_slots > 0:
            # Over-fetch by the number in flight: they can still appear in the queries
            # and must not starve runnable videos behind them.
            fetch_limit = fetch_slots + len(self._in_flight_video_ids)
            due_videos = await self.repo.list_transient_due(limit=fetch_limit)
            queued = await self.repo.list_queued_videos(
                limit=fetch_limit, stages=(Stage.QUEUED,)
            )
            dispatched += self._start(due_videos + queued, "fetch", self._fetch_tasks, fetch_slots)

        return dispatched > 0

    def _start(self, videos: list, phase: str, tasks: set[asyncio.Task], slots: int) -> int:
        started = 0
        for video in videos:
            if started >= slots:
                break
            if video.id in self._in_flight_video_ids:
                continue
            self._in_flight_video_ids.add(video.id)
            tasks.add(
                asyncio.create_task(
                    self._dispatch(video.id, phase), name=f"process-video-{phase}-{video.id}"
                )
            )
            started += 1
        return started

    def _dispatch_publishes(self) -> int:
        started = 0
        if self.publish is None:
            self._publish_queue.clear()
            return 0
        while self._publish_queue:
            playlist_pk = self._publish_queue.pop(0)
            if playlist_pk in self._publishing_ids:
                continue
            self._publishing_ids.add(playlist_pk)
            task = asyncio.create_task(
                self._dispatch_publish(playlist_pk), name=f"publish-playlist-{playlist_pk}"
            )
            self._publish_tasks.add(task)
            started += 1
        return started

    async def _dispatch(self, video_id: int, phase: str = "all") -> None:
        try:
            await self.process_video.execute(video_id, phase)
        except Exception as exc:
            logger.exception(
                "Unhandled error processing video %d in worker: %s", video_id, exc
            )
        finally:
            self._in_flight_video_ids.discard(video_id)
            # A slot just freed up: refill it now instead of after the idle poll interval.
            self._wake_event.set()

    async def _dispatch_publish(self, playlist_pk: int) -> None:
        try:
            assert self.publish is not None
            await self.publish.run(playlist_pk)
        except Exception as exc:
            logger.exception("Unhandled error publishing playlist %d: %s", playlist_pk, exc)
        finally:
            self._publishing_ids.discard(playlist_pk)
