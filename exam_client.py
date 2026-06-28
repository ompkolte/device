"""
Exam client — hardware-agnostic exam flow.
Run: python exam_client.py
     HARDWARE=simulator BACKEND_URL=http://localhost:8000 python exam_client.py
"""

import os
import sys
import requests

from config import get_hardware, BACKEND_URL

# Button constants
BTN_RECORD = "record"
BTN_RETRY = "retry"
BTN_NEXT = "next"
BTN_REPEAT = "repeat"


def _api(path: str) -> str:
    return f"{BACKEND_URL}{path}"


def _download_audio(url: str, dest_path: str) -> bool:
    """Download a question audio file. Returns False if it fails."""
    if url.startswith("/"):
        url = f"{BACKEND_URL}{url}"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        print(f"[WARN] Could not download audio: {e}")
        return False


def run_exam():
    hw = get_hardware()

    # ── 1. Authenticate ──────────────────────────────────────────────────────
    hw.display("परीक्षा प्रणाली", "सुरू करत आहे...")

    print("\n=== परीक्षा सुरू ===")
    exam_code = int(input("परीक्षा कोड प्रविष्ट करा: ").strip())
    student_id = int(input("विद्यार्थी ID प्रविष्ट करा: ").strip())
    dob = input("जन्मतारीख (DDMMYYYY): ").strip()

    hw.display("सत्यापन करत आहे...", "")

    try:
        r = requests.post(_api("/device/auth"), json={
            "exam_code": exam_code,
            "student_id": student_id,
            "dob": dob,
        }, timeout=15)
        r.raise_for_status()
        auth_data = r.json()
    except requests.HTTPError as e:
        hw.display("प्रवेश नाकारला", str(e))
        print(f"[ERROR] Auth failed: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        hw.display("जोडणी त्रुटी", "")
        print(f"[ERROR] {e}")
        sys.exit(1)

    exam_id = auth_data["exam_id"]
    hw.display(f"स्वागत!", auth_data["exam_name"])
    print(f"\nपरीक्षा: {auth_data['exam_name']}")
    if auth_data.get("instructions"):
        print(f"सूचना: {auth_data['instructions']}")

    # ── 2. Fetch questions ────────────────────────────────────────────────────
    hw.display("प्रश्न डाउनलोड करत आहे...", "")

    r = requests.get(_api(f"/device/exams/{exam_code}/questions"), timeout=30)
    r.raise_for_status()
    questions = r.json()["questions"]

    # Download question audio files
    audio_dir = f"./exam_audio/{exam_code}"
    os.makedirs(audio_dir, exist_ok=True)

    for q in questions:
        if q.get("audio_url"):
            dest = os.path.join(audio_dir, f"q{q['q_num']}.wav")
            q["_local_audio"] = dest if _download_audio(q["audio_url"], dest) else None
        else:
            q["_local_audio"] = None

    # ── 3. Answer each question ───────────────────────────────────────────────
    answer_files: dict[int, str] = {}  # q_num → local WAV path
    answers_dir = f"./exam_answers/{exam_code}_{student_id}"
    os.makedirs(answers_dir, exist_ok=True)

    for q in questions:
        q_num = q["q_num"]
        q_type = q["type"]
        q_text = q.get("question_text_mr") or f"प्रश्न {q_num}"
        audio_path = q.get("_local_audio")

        while True:  # loop for retry/repeat
            hw.display(f"प्रश्न {q_num}/{len(questions)}", q_type)
            print(f"\n──────────────────────────────")
            print(f"प्रश्न {q_num}: {q_text}")

            if q_type == "mcq" and q.get("options"):
                for i, opt in enumerate(q["options"], 1):
                    print(f"  {i}. {opt}")

            # Play audio and wait
            if audio_path:
                hw.display(f"प्रश्न {q_num}", "ऐकत आहे...")
                hw.play_audio(audio_path)

            hw.display("R=रेकॉर्ड  P=पुन्हा", "N=पुढे (उत्तर न देता)")
            btn = hw.wait_for_button()

            if btn == BTN_REPEAT:
                continue  # replay question

            if btn == BTN_NEXT:
                hw.display(f"प्रश्न {q_num}", "वगळले")
                break  # skip without answer

            if btn == BTN_RECORD:
                # Record answer
                hw.display("रेकॉर्डिंग...", "T=थांबवा")
                hw.start_recording()

                while True:
                    btn2 = hw.wait_for_button()
                    if btn2 in (BTN_RETRY, BTN_NEXT, BTN_RECORD):
                        break

                saved_path = hw.stop_recording()

                # Name: {exam_code}_{student_id}_q{n}.wav
                final_path = os.path.join(
                    answers_dir, f"{exam_code}_{student_id}_q{q_num}.wav"
                )
                os.replace(saved_path, final_path)
                hw.display(f"प्रश्न {q_num}", "उत्तर जतन झाले")
                print(f"[OK] Answer saved: {final_path}")

                if btn2 == BTN_RETRY:
                    # Delete and re-record
                    os.remove(final_path)
                    hw.display(f"प्रश्न {q_num}", "पुन्हा रेकॉर्ड करा")
                    continue

                answer_files[q_num] = final_path
                break

    # ── 4. Summary and submission ─────────────────────────────────────────────
    answered = len(answer_files)
    total = len(questions)
    hw.display(f"उत्तरे: {answered}/{total}", "R=सबमिट करा")
    print(f"\nउत्तरे दिली: {answered}/{total}")
    print("सबमिट करण्यासाठी R दाबा. N दाबून बाहेर पडा.")

    btn = hw.wait_for_button()
    if btn != BTN_RECORD:
        hw.display("सबमिट रद्द", "फाइल्स जतन आहेत")
        print("[INFO] Submission cancelled. Answer files kept locally.")
        return

    # ── 5. Upload ─────────────────────────────────────────────────────────────
    hw.display("अपलोड करत आहे...", "")
    print("[INFO] Uploading answers...")

    for attempt in range(3):
        try:
            files = []
            for q_num, path in answer_files.items():
                files.append(
                    ("files", (os.path.basename(path), open(path, "rb"), "audio/wav"))
                )

            r = requests.post(
                _api(f"/device/submit/{exam_id}/{student_id}"),
                files=files,
                timeout=120,
            )
            # Close file handles
            for _, f_tuple in files:
                f_tuple[1].close()

            r.raise_for_status()
            data = r.json()
            print(f"[OK] Submitted {data.get('answers_queued', 0)} answers.")
            hw.display("सबमिट यशस्वी!", "धन्यवाद")

            # Delete local copies only after confirmed receipt
            for path in answer_files.values():
                try:
                    os.remove(path)
                except OSError:
                    pass
            break

        except Exception as e:
            print(f"[WARN] Upload attempt {attempt + 1} failed: {e}")
            if attempt == 2:
                hw.display("अपलोड अयशस्वी", "फाइल्स जतन आहेत")
                print("[ERROR] Upload failed. Answer files kept for retry.")
            else:
                import time as _time
                _time.sleep(5)


if __name__ == "__main__":
    run_exam()
