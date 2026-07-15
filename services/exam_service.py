"""
Exam flow service — handles the complete exam-taking process.
Receives assignment via WebSocket, downloads assets, runs exam, uploads answers.
"""

import os
import json
import hashlib
import requests
import threading
from datetime import datetime
from typing import Optional

from config.settings import settings
from config.logging_config import get_logger
from hardware import get_hardware
from services.upload_service import UploadService
from services.health_check import run_health_checks
from services.exam_fsm import ExamFSM, ANNOUNCEMENTS
from utils.timezone import now_ist

logger = get_logger("pi.exam")

# Button constants
BTN_RECORD = "record"
BTN_RETRY = "retry"
BTN_NEXT = "next"
BTN_REPEAT = "repeat"


def _update_assignment_status(assignment_id: str, status: str) -> bool:
    """Update assignment status on backend."""
    try:
        resp = requests.patch(
            f"{settings.backend_url}/api/assignments/{assignment_id}/status",
            json={"status": status},
            timeout=10
        )
        resp.raise_for_status()
        logger.debug("Assignment status updated: %s", status)
        return True
    except Exception as e:
        logger.warning("Failed to update assignment status: %s", e)
        return False


def _update_download_status(assignment_id: str, status: str) -> bool:
    """Update download status (pending/downloading/ready)."""
    try:
        resp = requests.patch(
            f"{settings.backend_url}/api/assignments/{assignment_id}/download-status",
            json={"download_status": status},
            timeout=10
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning("Failed to update download status: %s", e)
        return False


def _report_exam_started(assignment_id: str, started_at: datetime) -> bool:
    """Report exam start time to backend."""
    try:
        resp = requests.patch(
            f"{settings.backend_url}/api/assignments/{assignment_id}/exam-started",
            json={"exam_started_at": started_at.isoformat()},
            timeout=10
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning("Failed to report exam start: %s", e)
        return False


class ExamService:
    def __init__(self):
        self.hw = get_hardware()
        self.assignment: Optional[dict] = None
        self.storage_dir = settings.storage_dir
        self.answers_dir: Optional[str] = None
        self.audio_dir: Optional[str] = None
        self.upload_service: Optional[UploadService] = None

    def set_assignment(self, package: dict) -> None:
        """Store assignment package received via WebSocket."""
        self.assignment = package
        self.answers_dir = os.path.join(
            self.storage_dir, 
            "answers", 
            f"{package['exam_code']}_{package['student_id']}"
        )
        self.audio_dir = os.path.join(
            self.storage_dir,
            "questions",
            str(package['exam_code'])
        )
        os.makedirs(self.answers_dir, exist_ok=True)
        os.makedirs(self.audio_dir, exist_ok=True)
        
        # Start upload service
        self.upload_service = UploadService(package['assignment_id'])
        self.upload_service.start()
        
        logger.info("Assignment set: %s for student %s", 
                   package['exam_name'], package['student_name'])

    def download_assets(self) -> bool:
        """Download all question audio files (.ogg)."""
        if not self.assignment:
            return False

        _update_assignment_status(self.assignment["assignment_id"], "downloading")
        _update_download_status(self.assignment["assignment_id"], "downloading")
        self.hw.display("डाउनलोड करत आहे...", "")
        questions = self.assignment.get("questions", [])
        
        for q in questions:
            audio_url = q.get("audio_url")
            if not audio_url:
                continue

            # Use .ogg extension
            dest = os.path.join(self.audio_dir, f"q{q['q_num']}.ogg")
            if os.path.exists(dest):
                q["_local_audio"] = dest
                continue

            full_url = f"{settings.main_backend_url}{audio_url}"
            try:
                r = requests.get(full_url, timeout=30)
                r.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(r.content)
                q["_local_audio"] = dest
                logger.info("Downloaded: q%d", q["q_num"])
            except Exception as e:
                logger.error("Failed to download q%d: %s", q["q_num"], e)
                q["_local_audio"] = None

        _update_assignment_status(self.assignment["assignment_id"], "ready")
        _update_download_status(self.assignment["assignment_id"], "ready")
        self.hw.display("डाउनलोड पूर्ण", "")
        return True

    def run_health_check(self) -> bool:
        """Run pre-exam health checks."""
        passed, failures = run_health_checks()
        if not passed:
            self.hw.display("तपासणी अयशस्वी", "पुन्हा प्रयत्न करा")
            logger.error("Health checks failed: %s", failures)
        return passed

    def run_exam(self) -> dict:
        """
        Run the exam flow using FSM.
        Returns dict of q_num → answer file path.
        """
        if not self.assignment:
            raise RuntimeError("No assignment set")

        questions = self.assignment.get("questions", [])
        exam_code = self.assignment["exam_code"]
        student_id = self.assignment["student_id"]
        duration_minutes = self.assignment.get("duration_minutes", 60)

        self.hw.display(self.assignment["exam_name"], "सुरू करण्यासाठी N दाबा")
        logger.info("Waiting for student to start exam")
        
        # Wait for START button (N = next)
        while True:
            btn = self.hw.wait_for_button()
            if btn == BTN_NEXT:
                break

        # Record start time and report to backend
        exam_started_at = now_ist()
        exam_end_time = exam_started_at.timestamp() + (duration_minutes * 60)
        time_warning_played = False
        
        logger.info("Exam started at %s, duration %d min", exam_started_at, duration_minutes)
        _update_assignment_status(self.assignment["assignment_id"], "in_progress")
        _report_exam_started(self.assignment["assignment_id"], exam_started_at)
        
        # Play exam start announcement
        if os.path.exists(ANNOUNCEMENTS.get("exam_start", "")):
            self.hw.play_audio(ANNOUNCEMENTS["exam_start"])
        self.hw.display("परीक्षा सुरू!", "")

        # Create FSM
        fsm = ExamFSM(
            questions=questions,
            answers_dir=self.answers_dir,
            exam_code=str(exam_code),
            student_id=str(student_id),
            play_audio=self.hw.play_audio,
            start_recording=self.hw.start_recording,
            stop_recording=self.hw.stop_recording,
            display=self.hw.display,
            wait_for_button=self._timed_wait_for_button(exam_end_time, fsm_ref=[None]),
            on_answer_saved=lambda q, p: self.upload_service.queue_upload(q, p) if self.upload_service else None,
        )
        # Store ref for timer callback
        fsm_ref = [fsm]
        
        # Timer thread for time warnings and expiry
        timer_stop = threading.Event()
        
        def timer_thread():
            nonlocal time_warning_played
            while not timer_stop.is_set():
                remaining = exam_end_time - now_ist().timestamp()
                
                # 5 minute warning
                if not time_warning_played and 0 < remaining <= 300:
                    time_warning_played = True
                    if os.path.exists(ANNOUNCEMENTS.get("time_warning", "")):
                        self.hw.play_audio(ANNOUNCEMENTS["time_warning"])
                
                # Time expired
                if remaining <= 0:
                    logger.info("Time expired, forcing finish")
                    fsm.force_finish()
                    break
                
                timer_stop.wait(5)  # Check every 5 seconds
        
        timer = threading.Thread(target=timer_thread, daemon=True)
        timer.start()
        
        # Run FSM
        answer_files = fsm.run()
        
        timer_stop.set()
        timer.join(timeout=1)

        # Show summary and wait for submit (or auto-submit if time expired)
        answered = len(answer_files)
        total = len(questions)
        time_expired = now_ist().timestamp() >= exam_end_time
        
        if time_expired:
            self.hw.display(f"उत्तरे: {answered}/{total}", "स्वयं-सबमिट...")
            logger.info("Time expired, auto-submitting %d/%d", answered, total)
        else:
            # Play exam end announcement
            if os.path.exists(ANNOUNCEMENTS.get("exam_end", "")):
                self.hw.play_audio(ANNOUNCEMENTS["exam_end"])
            self.hw.display(f"उत्तरे: {answered}/{total}", "R=सबमिट")
            logger.info("Exam complete: %d/%d answered", answered, total)
            # Wait for SUBMIT button
            while True:
                btn = self.hw.wait_for_button()
                if btn == BTN_RECORD:
                    break

        # Smart submission: upload only missing
        self.hw.display("सबमिट करत आहे...", "")
        submit_ok = True
        if self.upload_service:
            submit_ok = self.upload_service.finalize(answer_files)
            self.upload_service.stop()

        if submit_ok:
            self.hw.display("सबमिट यशस्वी!", "धन्यवाद")
        else:
            self.hw.display("सबमिट अयशस्वी", "पुन्हा प्रयत्न करा")
        logger.info("Submission complete")

        return answer_files
    
    def _timed_wait_for_button(self, exam_end_time: float, fsm_ref: list):
        """Create a wait_for_button wrapper that respects exam timer."""
        def wait():
            # ponytail: simple polling, event-based interrupts if latency matters
            while True:
                # Check time before waiting
                if now_ist().timestamp() >= exam_end_time:
                    if fsm_ref[0]:
                        fsm_ref[0].force_finish()
                    return BTN_NEXT  # Dummy return to exit FSM loop
                return self.hw.wait_for_button()
        return wait

    def get_local_manifest(self) -> dict:
        """Get manifest of local answer files."""
        if not self.answers_dir or not os.path.exists(self.answers_dir):
            return {}
        
        manifest = {}
        for fname in os.listdir(self.answers_dir):
            if fname.endswith(".ogg"):
                path = os.path.join(self.answers_dir, fname)
                manifest[fname] = {
                    "path": path,
                    "size": os.path.getsize(path),
                }
        return manifest
