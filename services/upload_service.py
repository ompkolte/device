"""
Background upload service for exam recordings.
Uploads recordings to device-management backend while exam continues.
"""

import os
import threading
import queue
import time
import requests
from typing import Optional

from config.settings import settings
from config.logging_config import get_logger

logger = get_logger("pi.upload")


class UploadService:
    """Background uploader with retry queue."""

    def __init__(self, assignment_id: str):
        self.assignment_id = assignment_id
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._upload_url = f"{settings.backend_url}/api/recordings/{assignment_id}"

    def start(self) -> None:
        """Start background upload thread."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        logger.info("Upload service started for assignment %s", self.assignment_id)

    def stop(self) -> None:
        """Stop background thread."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Upload service stopped")

    def queue_upload(self, q_num: int, file_path: str) -> None:
        """Add a recording to upload queue."""
        self._queue.put((q_num, file_path, 0))  # (q_num, path, retry_count)
        logger.debug("Queued q%d for upload", q_num)

    def _worker(self) -> None:
        """Background worker that uploads recordings."""
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            q_num, file_path, retries = item

            if not os.path.exists(file_path):
                logger.warning("File not found for upload: %s", file_path)
                continue

            success = self._upload_one(q_num, file_path)
            if success:
                logger.info("Uploaded q%d", q_num)
            else:
                if retries < 3:
                    # Re-queue with delay
                    logger.warning("Upload failed for q%d, retry %d", q_num, retries + 1)
                    time.sleep(2 ** retries)  # Exponential backoff
                    self._queue.put((q_num, file_path, retries + 1))
                else:
                    logger.error("Upload failed for q%d after 3 retries", q_num)

    def _upload_one(self, q_num: int, file_path: str) -> bool:
        """Upload a single recording."""
        try:
            # Determine MIME type from extension
            mime_type = "audio/ogg" if file_path.endswith(".ogg") else "audio/wav"
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f, mime_type)}
                resp = requests.post(
                    f"{self._upload_url}/{q_num}",
                    files=files,
                    timeout=60
                )
                resp.raise_for_status()
                return True
        except Exception as e:
            logger.error("Upload error: %s", e)
            return False

    def get_missing(self) -> list[int]:
        """Ask backend which recordings are missing."""
        try:
            resp = requests.get(f"{self._upload_url}/missing", timeout=10)
            resp.raise_for_status()
            return resp.json().get("missing", [])
        except Exception as e:
            logger.error("Failed to get missing list: %s", e)
            return []

    def upload_missing(self, answer_files: dict[int, str]) -> bool:
        """Upload only missing recordings (smart submission)."""
        missing = self.get_missing()
        if not missing:
            logger.info("All recordings uploaded")
            return True

        logger.info("Uploading %d missing recordings", len(missing))
        all_ok = True
        for q_num in missing:
            if q_num in answer_files:
                success = self._upload_one(q_num, answer_files[q_num])
                if not success:
                    all_ok = False
        return all_ok

    def mark_complete(self) -> bool:
        """Mark submission as complete only if all uploads succeeded."""
        try:
            resp = requests.post(
                f"{self._upload_url}/complete",
                timeout=10
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error("Failed to mark complete: %s", e)
            return False

    def finalize(self, answer_files: dict[int, str]) -> bool:
        """Upload missing and mark complete only if all uploads succeed."""
        if not self.upload_missing(answer_files):
            logger.error("Some uploads failed, not marking complete")
            return False
        return self.mark_complete()
