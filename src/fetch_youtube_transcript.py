import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from yt_dlp import YoutubeDL

from src.utils import ensure_output_folder, get_config, get_worker_count, setup_logging


class YouTubeTranscriptFetcher:
    """A class to fetch transcripts from YouTube videos.

    This class provides functionality to download transcripts (both human-generated
    and automatic) from YouTube videos using yt-dlp library.
    """

    def __init__(
        self,
        output_folder: Optional[str] = None,
        language: Optional[str] = None,
        num_workers: Optional[int] = None,
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
        self.max_retries = get_config("playlist_quiz.transcript_max_retries", 3)
        self.delay_between_downloads = get_config("playlist_quiz.transcript_delay_between_requests", 4)
        self.failure_reasons: Dict[str, str] = {}

        # Ensure output folder exists
        self.output_folder = ensure_output_folder(self.output_folder)

    def _get_ydl_opts(self) -> dict:
        """Get the yt-dlp options configuration.

        Returns:
            dict: Configuration options for yt-dlp.
        """
        return {
            "skip_download": get_config("download.skip_download", True),
            "writesubtitles": get_config(
                "download.write_subtitles", True
            ),  # human captions
            "writeautomaticsub": get_config(
                "download.write_automatic_sub", True
            ),  # auto captions
            "subtitleslangs": [self.language],
            "subtitlesformat": get_config("download.subtitles_format", "srt"),
            "outtmpl": os.path.join(
                self.output_folder,
                get_config("download.output_template", "%(id)s.%(ext)s"),
            ),
            # Additional options to improve subtitle fetching reliability
            "ignoreerrors": False,  # Don't ignore errors to get proper feedback
            "retries": self.max_retries,
            "sleep_interval": 1,
            "max_sleep_interval": 3,
        }

    def fetch_transcript(self, url: str) -> bool:
        """Fetch transcript for a single YouTube video.

        Args:
            url (str): YouTube video URL.

        Returns:
            bool: True if transcript was successfully downloaded, False otherwise.
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                with YoutubeDL(self._get_ydl_opts()) as ydl:
                    ydl.download([url])
                self.failure_reasons.pop(url, None)
                return True
            except Exception as error:
                detail = str(error)
                normalized_detail = detail.lower()
                if "members-only" in normalized_detail or "members only" in normalized_detail:
                    reason = "Members-only video; subtitles require authorised access."
                elif "429" in detail:
                    reason = "YouTube rate-limited subtitle downloads."
                elif "no subtitles" in normalized_detail or "subtitle" in normalized_detail and "not available" in normalized_detail:
                    reason = "No English subtitles are available for this video."
                else:
                    reason = "Transcript download failed; see the backend log for details."

                self.failure_reasons[url] = reason
                print(f"Transcript download failed for {url} (attempt {attempt}/{self.max_retries}): {detail}")
                if attempt < self.max_retries and "429" in detail:
                    wait_time = self.delay_between_downloads * (2 ** (attempt - 1)) + random.uniform(0, 1)
                    print(f"YouTube rate limit detected. Retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
                else:
                    break
        return False

    def _fetch_transcripts_sequential(self, urls: List[str]) -> dict:
        """Fetch transcripts sequentially (one at a time).

        Args:
            urls (List[str]): List of YouTube video URLs.

        Returns:
            dict: Dictionary with URLs as keys and success status as values.
        """
        results = {}
        for index, url in enumerate(urls):
            print(f"Fetching transcript for: {url}")
            results[url] = self.fetch_transcript(url)
            if index < len(urls) - 1:
                time.sleep(self.delay_between_downloads + random.uniform(0, 1))
        return results

    def _fetch_transcripts_parallel(self, urls: List[str]) -> dict:
        """Fetch transcripts in parallel using ThreadPoolExecutor.

        Args:
            urls (List[str]): List of YouTube video URLs.

        Returns:
            dict: Dictionary with URLs as keys and success status as values.
        """
        results = {}

        with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
            # Submit all download tasks
            future_to_url = {
                executor.submit(self.fetch_transcript, url): url for url in urls
            }

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

    def fetch_transcripts(self, urls: List[str]) -> dict:
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
