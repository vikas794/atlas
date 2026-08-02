import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from yt_dlp import YoutubeDL

from src.utils import ensure_output_folder, get_config, get_worker_count, setup_logging


class YouTubeTranscriptFetcher:
    """A class to fetch transcripts from YouTube videos.

    This class provides functionality to download transcripts (both human-generated
    and automatic) from YouTube videos using yt-dlp library.
    """

    def __init__(
        self,
        output_folder: str | None = None,
        language: str | None = None,
        num_workers: int | None = None,
    ):
        """Initialize the YouTubeTranscriptFetcher.

        Args:
            output_folder (Optional[str]): Directory where transcripts will be saved.
                If None, uses config default.
            language (Optional[str]): Language code for subtitles. If None, uses config default.
            num_workers (Optional[int]): Number of concurrent workers for parallel processing.
                If None, auto-detects based on config and CPU count.
        """
        # Initialize configuration and logging
        setup_logging()

        # Set configuration values
        self.output_folder = output_folder or get_config(
            "processing.transcripts.output_folder", "transcripts"
        )
        self.language = language or get_config("processing.transcripts.language", "en")
        self.num_workers = get_worker_count(num_workers)
        self.retry_wait_seconds = get_config(
            "playlist_quiz.transcript_retry_wait_seconds", [15, 30, 60, 120]
        )
        self.retry_jitter_seconds = float(
            get_config("playlist_quiz.transcript_retry_jitter_seconds", 5)
        )
        self.min_delay_between_videos = float(
            get_config("playlist_quiz.transcript_min_delay_between_videos", 15)
        )
        self.rate_limit_cooldown_seconds = float(
            get_config("playlist_quiz.transcript_rate_limit_cooldown_seconds", 120)
        )
        self.max_retries = len(self.retry_wait_seconds)
        self.delay_between_downloads = get_config(
            "playlist_quiz.transcript_delay_between_requests", 4
        )
        self.failure_reasons: dict[str, str] = {}
        self.statuses: dict[str, str] = {}
        self._cooldown_until: float = 0.0

        # Ensure output folder exists
        self.output_folder = ensure_output_folder(self.output_folder)

    def _get_ydl_opts(self) -> dict:
        """Get the yt-dlp options configuration.

        Returns:
            dict: Configuration options for yt-dlp.
        """
        return {
            "skip_download": get_config("download.skip_download", True),
            "writesubtitles": get_config("download.write_subtitles", True),  # human captions
            "writeautomaticsub": get_config("download.write_automatic_sub", True),  # auto captions
            "subtitleslangs": [self.language],
            "subtitlesformat": get_config("download.subtitles_format", "srt"),
            "outtmpl": os.path.join(
                self.output_folder,
                get_config("download.output_template", "%(id)s.%(ext)s"),
            ),
            # Additional options to improve subtitle fetching reliability
            "ignoreerrors": False,  # Don't ignore errors to get proper feedback
            "retries": 0,  # The fetcher owns retry behavior; disable yt-dlp retries
            "fragment_retries": 0,
            "extractor_retries": 0,
            "sleep_interval": 1,
            "max_sleep_interval": 3,
        }

    def _classify_failure(self, detail: str) -> str:
        """Classify a yt-dlp failure into a terminal reason category."""
        normalized = detail.lower()
        if (
            "members-only" in normalized
            or "members only" in normalized
            or "channel's members" in normalized
            or "channel’s members" in normalized
        ):
            return "members_only"
        if "no subtitles" in normalized or (
            "subtitle" in normalized and "not available" in normalized
        ):
            return "no_subtitles"
        if "429" in detail or "rate limit" in normalized or "too many requests" in normalized:
            return "rate_limited"
        return "failed"

    @staticmethod
    def _safe_diagnostic(detail: str) -> str:
        """Return a short, log-safe diagnostic for an unexpected failure."""
        stripped = detail.strip()
        first_line = stripped.splitlines()[0] if stripped else "unknown error"
        return first_line[:200]

    @staticmethod
    def _video_id(url: str) -> str | None:
        """Extract the 11-char YouTube video id from a watch/short URL."""
        match = re.search(
            r"(?:v=([\w-]{11})|youtu\.be/([\w-]{11})|/shorts/([\w-]{11}))",
            url,
        )
        if not match:
            return None
        return match.group(1) or match.group(2) or match.group(3)

    def _subtitle_file_exists(self, url: str) -> bool:
        """Check whether any subtitle file was written for the URL's video."""
        video_id = self._video_id(url)
        if not video_id:
            return True  # cannot verify the file; trust the download result
        return any(
            os.path.exists(os.path.join(self.output_folder, f"{video_id}.{self.language}.{ext}"))
            for ext in ("srt", "vtt")
        )

    def fetch_transcript(self, url: str) -> bool:
        """Fetch transcript for a single YouTube video.

        Args:
            url (str): YouTube video URL.

        Returns:
            bool: True if transcript was successfully downloaded, False otherwise.
        """
        retry_waits = self.retry_wait_seconds
        total_attempts = len(retry_waits) + 1
        for attempt in range(1, total_attempts + 1):
            try:
                with YoutubeDL(self._get_ydl_opts()) as ydl:
                    ydl.download([url])
                if not self._subtitle_file_exists(url):
                    self.failure_reasons[url] = "No English subtitles are available for this video."
                    self.statuses[url] = "no_subtitles"
                    print(f"Transcript skipped for {url}: no subtitles were written")
                    return False

                self.failure_reasons.pop(url, None)
                self.statuses[url] = "success"
                self._cooldown_until = 0.0
                return True
            except Exception as error:
                detail = str(error)
                category = self._classify_failure(detail)

                if category == "members_only":
                    self.failure_reasons[url] = "Members-only video; authentication unavailable."
                    self.statuses[url] = "members_only"
                    print(
                        f"Transcript skipped for {url}: members-only video "
                        f"(authentication unavailable)"
                    )
                    return False

                if category == "no_subtitles":
                    self.failure_reasons[url] = "No English subtitles are available for this video."
                    self.statuses[url] = "no_subtitles"
                    print(f"Transcript skipped for {url}: no subtitles available")
                    return False

                if category == "rate_limited":
                    self._cooldown_until = max(
                        self._cooldown_until,
                        time.time() + self.rate_limit_cooldown_seconds,
                    )
                    if attempt >= total_attempts:
                        self.failure_reasons[url] = (
                            "YouTube rate-limited subtitle downloads (retries exhausted)."
                        )
                        self.statuses[url] = "rate_limited"
                        print(
                            f"Transcript download failed for {url} "
                            f"(attempt {attempt}/{total_attempts}): {detail}"
                        )
                        return False
                    wait_time = retry_waits[attempt - 1] + random.uniform(
                        0, self.retry_jitter_seconds
                    )
                    print(
                        f"YouTube rate limit detected for {url}. "
                        f"Retrying in {wait_time:.1f}s "
                        f"(attempt {attempt}/{total_attempts})..."
                    )
                    time.sleep(wait_time)
                    continue

                self.failure_reasons[url] = (
                    f"Transcript download failed: {self._safe_diagnostic(detail)}"
                )
                self.statuses[url] = "failed"
                print(
                    f"Transcript download failed for {url} "
                    f"(attempt {attempt}/{total_attempts}): {detail}"
                )
                return False
        return False

    def _fetch_transcripts_sequential(self, urls: list[str]) -> dict:
        """Fetch transcripts sequentially (one at a time).

        Args:
            urls (List[str]): List of YouTube video URLs.

        Returns:
            dict: Dictionary with URLs as keys and success status as values.
        """
        results = {}
        last_video_end = 0.0
        for index, url in enumerate(urls):
            if index > 0:
                self._wait_until_next_video(last_video_end)
            print(f"Fetching transcript for: {url}")
            results[url] = self.fetch_transcript(url)
            last_video_end = time.time()
        return results

    def _wait_until_next_video(self, last_video_end: float) -> None:
        """Enforce inter-video pacing and the pipeline-wide rate-limit cooldown."""
        now = time.time()
        inter_video_gate = last_video_end + max(
            self.delay_between_downloads, self.min_delay_between_videos
        )
        gate = max(inter_video_gate, self._cooldown_until)
        remaining = gate - now
        if remaining > 0:
            print(f"Pacing next subtitle download in {remaining:.1f}s...")
            time.sleep(remaining)

    def _fetch_transcripts_parallel(self, urls: list[str]) -> dict:
        """Fetch transcripts in parallel using ThreadPoolExecutor.

        Args:
            urls (List[str]): List of YouTube video URLs.

        Returns:
            dict: Dictionary with URLs as keys and success status as values.
        """
        results = {}

        with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
            # Submit all download tasks
            future_to_url = {executor.submit(self.fetch_transcript, url): url for url in urls}

            # Process completed tasks
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    success = future.result()
                    results[url] = success
                    status = "Success" if success else "Failed"
                    print(f"Completed {url}: {status}")
                except Exception as e:
                    results[url] = False
                    print(f"Error processing {url}: {str(e)}")

        return results

    def fetch_transcripts(self, urls: list[str]) -> dict:
        """Fetch transcripts for multiple YouTube videos with automatic parallel/sequential fallback.

        Args:
            urls (List[str]): List of YouTube video URLs.

        Returns:
            dict: Dictionary with URLs as keys and success status as values.
        """
        if not urls:
            return {}

        print(f"Using paced sequential subtitle downloads for {len(urls)} URL(s)")
        return self._fetch_transcripts_sequential(urls)


# Example usage
if __name__ == "__main__":
    # Example URLs
    urls = [
        "https://www.youtube.com/watch?v=UV81LAb3x2g",
        # "https://www.youtube.com/watch?v=q6kJ71tEYqM",
        # "https://www.youtube.com/watch?v=gpz6C_2l5jI",
    ]

    # Initialize the fetcher
    fetcher = YouTubeTranscriptFetcher(output_folder="transcripts")

    # Fetch transcripts
    results = fetcher.fetch_transcripts(urls)

    # Print results
    for url, success in results.items():
        status = "Success" if success else "Failed"
        print(f"{url}: {status}")
