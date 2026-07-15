"""
Exam FSM — finite state machine for exam workflow.
States: QUESTION_MODE, RECORD_STATE, ANSWER_MODE
"""

import os
from enum import Enum, auto
from typing import Optional, Callable
from dataclasses import dataclass, field

from config.logging_config import get_logger

logger = get_logger("pi.fsm")

# Audio announcement files (in pi-client/audio/)
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "..", "audio")

ANNOUNCEMENTS = {
    "question_menu": os.path.join(AUDIO_DIR, "question_menu.ogg"),
    "answer_menu": os.path.join(AUDIO_DIR, "answer_menu.ogg"),
    "recording_started": os.path.join(AUDIO_DIR, "recording_started.ogg"),
    "recording_stopped": os.path.join(AUDIO_DIR, "recording_stopped.ogg"),
    "exam_start": os.path.join(AUDIO_DIR, "exam_start.ogg"),
    "exam_end": os.path.join(AUDIO_DIR, "exam_end.ogg"),
    "time_warning": os.path.join(AUDIO_DIR, "time_warning.ogg"),
    "time_expired": os.path.join(AUDIO_DIR, "time_expired.ogg"),
    "question_skipped": os.path.join(AUDIO_DIR, "question_skipped.ogg"),
}


class State(Enum):
    QUESTION_MODE = auto()
    RECORD_STATE = auto()
    ANSWER_MODE = auto()


# Button constants
BTN_RECORD = "record"   # R
BTN_RETRY = "retry"     # Y
BTN_NEXT = "next"       # N
BTN_REPEAT = "repeat"   # T


@dataclass
class ExamFSM:
    """
    Finite state machine for exam workflow.
    
    Hardware callbacks must be injected:
    - play_audio(path): play .ogg file
    - start_recording(): start mic
    - stop_recording() -> path: stop mic, return .ogg path
    - display(line1, line2): show on OLED
    - wait_for_button() -> str: block until button press
    """
    questions: list
    answers_dir: str
    exam_code: str
    student_id: str
    
    # Hardware callbacks (injected)
    play_audio: Callable[[str], None] = None
    start_recording: Callable[[], None] = None
    stop_recording: Callable[[], str] = None
    display: Callable[[str, str], None] = None
    wait_for_button: Callable[[], str] = None
    on_answer_saved: Callable[[int, str], None] = None  # q_num, path -> queue upload
    
    # State
    state: State = field(default=State.QUESTION_MODE, init=False)
    question_index: int = field(default=0, init=False)
    current_recording: Optional[str] = field(default=None, init=False)
    answer_files: dict = field(default_factory=dict, init=False)
    exam_finished: bool = field(default=False, init=False)
    
    # ─────────────────────────────────────────────────────────────────────
    # Announcements
    # ─────────────────────────────────────────────────────────────────────
    def _announce(self, key: str) -> None:
        """Play announcement .ogg file if exists."""
        path = ANNOUNCEMENTS.get(key)
        if path and os.path.exists(path):
            self.play_audio(path)
        else:
            logger.warning("Announcement not found: %s", key)
    
    def _play_question(self) -> None:
        """Play current question audio."""
        q = self.questions[self.question_index]
        audio_path = q.get("_local_audio")
        if audio_path and os.path.exists(audio_path):
            self.play_audio(audio_path)
        else:
            logger.warning("Question audio not found: q%d", q["q_num"])
    
    def _play_current_recording(self) -> None:
        """Play the current recorded answer."""
        if self.current_recording and os.path.exists(self.current_recording):
            self.play_audio(self.current_recording)
    
    # ─────────────────────────────────────────────────────────────────────
    # State entry actions
    # ─────────────────────────────────────────────────────────────────────
    def _enter_question_mode(self) -> None:
        """Entry: announce question + question menu."""
        self.state = State.QUESTION_MODE
        q = self.questions[self.question_index]
        q_num = q["q_num"]
        total = len(self.questions)
        
        self.display(f"प्रश्न {q_num}/{total}", "ऐकत आहे...")
        logger.info("QUESTION_MODE: q%d", q_num)
        
        self._play_question()
        self._announce("question_menu")
        self.display(f"प्रश्न {q_num}/{total}", "R|T|N")
    
    def _enter_record_state(self) -> None:
        """Entry: start mic + announce recording started."""
        self.state = State.RECORD_STATE
        q_num = self.questions[self.question_index]["q_num"]
        
        self.display(f"प्रश्न {q_num}", "रेकॉर्डिंग...")
        logger.info("RECORD_STATE: recording q%d", q_num)
        
        self.start_recording()
        self._announce("recording_started")
    
    def _enter_answer_mode(self) -> None:
        """Entry: announce answer menu."""
        self.state = State.ANSWER_MODE
        q_num = self.questions[self.question_index]["q_num"]
        
        self.display(f"प्रश्न {q_num}", "उत्तर R|T|Y|N")
        logger.info("ANSWER_MODE: q%d", q_num)
        
        self._announce("answer_menu")
    
    # ─────────────────────────────────────────────────────────────────────
    # Button handlers per state
    # ─────────────────────────────────────────────────────────────────────
    def _handle_question_mode(self, btn: str) -> None:
        """Handle button in QUESTION_MODE."""
        if btn == BTN_RECORD:
            # R → Record State
            self._enter_record_state()
        
        elif btn == BTN_REPEAT:
            # T → Repeat question (re-enter Question Mode)
            self._enter_question_mode()
        
        elif btn == BTN_NEXT:
            # N → Skip question, go to next
            q_num = self.questions[self.question_index]["q_num"]
            logger.info("Skipped q%d", q_num)
            self._announce("question_skipped")
            self._next_question()
        
        elif btn == BTN_RETRY:
            # Y → Nothing happens
            pass
    
    def _handle_record_state(self, btn: str) -> None:
        """Handle button in RECORD_STATE (only R stops)."""
        if btn == BTN_RECORD:
            # R → Stop recording, save, play back, enter Answer Mode
            saved_path = self.stop_recording()
            
            # Move to final location as .ogg
            q_num = self.questions[self.question_index]["q_num"]
            final_path = os.path.join(
                self.answers_dir,
                f"{self.exam_code}_{self.student_id}_q{q_num}.ogg"
            )
            os.replace(saved_path, final_path)
            self.current_recording = final_path
            
            logger.info("Recording saved: %s", final_path)
            self._announce("recording_stopped")
            
            # Play back recorded answer
            self._play_current_recording()
            
            # Enter Answer Mode
            self._enter_answer_mode()
        # All other buttons ignored during recording
    
    def _handle_answer_mode(self, btn: str) -> None:
        """Handle button in ANSWER_MODE."""
        if btn == BTN_RECORD:
            # R → Re-record (enter Record State, return to Answer Mode)
            self._enter_record_state()
        
        elif btn == BTN_REPEAT:
            # T → Play question + play answer + announce menu
            self._play_question()
            self._play_current_recording()
            self._announce("answer_menu")
        
        elif btn == BTN_RETRY:
            # Y → Discard recording, restart question (enter Question Mode)
            if self.current_recording and os.path.exists(self.current_recording):
                os.remove(self.current_recording)
                logger.info("Discarded recording")
            self.current_recording = None
            self._enter_question_mode()
        
        elif btn == BTN_NEXT:
            # N → Submit answer, go to next question
            self._submit_answer()
            self._next_question()
    
    def _submit_answer(self) -> None:
        """Submit current recording and queue for upload."""
        if self.current_recording and os.path.exists(self.current_recording):
            q_num = self.questions[self.question_index]["q_num"]
            self.answer_files[q_num] = self.current_recording
            
            if self.on_answer_saved:
                self.on_answer_saved(q_num, self.current_recording)
            
            logger.info("Submitted q%d: %s", q_num, self.current_recording)
        self.current_recording = None
    
    def _next_question(self) -> None:
        """Advance to next question or finish exam."""
        self.question_index += 1
        self.current_recording = None
        
        if self.question_index >= len(self.questions):
            self.exam_finished = True
            logger.info("All questions completed")
        else:
            self._enter_question_mode()
    
    # ─────────────────────────────────────────────────────────────────────
    # Main loop
    # ─────────────────────────────────────────────────────────────────────
    def handle_button(self, btn: str) -> None:
        """Route button press to current state handler."""
        if self.state == State.QUESTION_MODE:
            self._handle_question_mode(btn)
        elif self.state == State.RECORD_STATE:
            self._handle_record_state(btn)
        elif self.state == State.ANSWER_MODE:
            self._handle_answer_mode(btn)
    
    def run(self) -> dict:
        """
        Run exam loop. Returns {q_num: answer_path}.
        Caller handles timer and exam start/end.
        """
        if not self.questions:
            logger.warning("No questions to run")
            return {}
        
        # Start with first question
        self._enter_question_mode()
        
        # Main loop
        while not self.exam_finished:
            btn = self.wait_for_button()
            self.handle_button(btn)
        
        return self.answer_files
    
    def force_finish(self) -> None:
        """Force-finish exam (time expired). Submit any in-progress recording."""
        if self.state == State.RECORD_STATE:
            # Stop recording if in progress
            saved_path = self.stop_recording()
            q_num = self.questions[self.question_index]["q_num"]
            final_path = os.path.join(
                self.answers_dir,
                f"{self.exam_code}_{self.student_id}_q{q_num}.ogg"
            )
            os.replace(saved_path, final_path)
            self.current_recording = final_path
        
        # Submit if we have a recording
        if self.current_recording:
            self._submit_answer()
        
        self.exam_finished = True
        self._announce("time_expired")
