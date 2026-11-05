#!/usr/bin/env python3
"""
Test suite for standalone filter_recovery.py hook.
Validates the invariant:
Only triggers when it reads the refusal message at the LAST LINE of the LAST MESSAGE,
avoiding false-positive full-message triggers on normal text discussing safety filters.
"""

import sys
import json
import subprocess
import tempfile
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
SCRIPT_PATH = PLUGIN_DIR / "filter_recovery.py"


def run_hook(payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Hook failed with code {proc.returncode}: {proc.stderr}")
    return json.loads(proc.stdout)


def test_suite():
    print("Running filter_recovery tests...\n")

    # Case 1: Empty payload -> allow
    res = run_hook({})
    assert res.get("decision") == "allow", f"Empty failed: {res}"
    print("  [PASS] Case 1: Empty payload returns allow")

    # Case 2: Normal task completion -> allow
    res_normal = run_hook({"fullyIdle": True, "message": "Task complete."})
    assert res_normal.get("decision") == "allow"
    print("  [PASS] Case 2: Normal task completion returns allow")

    # Case 3: Payload error has exact filter block -> continue
    res_err = run_hook({
        "fullyIdle": True,
        "error": "This request was blocked by Gemini's filters. They can occasionally trigger by mistake on safe coding, security, or biology-related queries."
    })
    assert res_err.get("decision") == "continue"
    assert "[EXECUTION RECOVERY: FALSE TRIGGER INTERCEPTED]" in res_err.get("reason", "")
    print("  [PASS] Case 3: Exact filter block string in payload error triggers continue with recovery directive")

    # Case 4: Multiline payload error with filter block on the LAST line -> continue
    res_err_last_line = run_hook({
        "fullyIdle": True,
        "error": "API request failed with status 400.\nThis request was blocked by Gemini's filters."
    })
    assert res_err_last_line.get("decision") == "continue"
    assert "[EXECUTION RECOVERY: FALSE TRIGGER INTERCEPTED]" in res_err_last_line.get("reason", "")
    print("  [PASS] Case 4: Multiline payload error with filter block on last line triggers continue")

    # Case 5: Multiline payload error with filter block mid-text followed by other lines -> allow (no full message trigger)
    res_err_mid = run_hook({
        "fullyIdle": True,
        "error": "This request was blocked by Gemini's filters.\nTraceback (most recent call last):\n  File 'test.py', line 1"
    })
    assert res_err_mid.get("decision") == "allow", f"Expected allow but got: {res_err_mid}"
    print("  [PASS] Case 5: Multiline payload error with filter block mid-text returns allow (no full message trigger)")

    # Case 6: Transcript single line filter block -> continue
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as tf:
        tf.write(json.dumps({
            "type": "MODEL_RESPONSE",
            "content": "This request was blocked by Gemini's filters."
        }) + "\n")
        transcript_path_1 = tf.name

    try:
        res_trans_1 = run_hook({"transcriptPath": transcript_path_1})
        assert res_trans_1.get("decision") == "continue"
        assert "[EXECUTION RECOVERY: FALSE TRIGGER INTERCEPTED]" in res_trans_1.get("reason", "")
        print("  [PASS] Case 6: Transcript step with single line filter block triggers continue")
    finally:
        Path(transcript_path_1).unlink(missing_ok=True)

    # Case 7: Transcript multiline with refusal on the LAST non-empty line -> continue
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as tf:
        tf.write(json.dumps({
            "type": "PLANNER_RESPONSE",
            "content": "Reviewing security policies for the smart contract...\nI cannot fulfill this request."
        }) + "\n")
        transcript_path_2 = tf.name

    try:
        res_trans_2 = run_hook({"transcriptPath": transcript_path_2})
        assert res_trans_2.get("decision") == "continue"
        assert "[EXECUTION RECOVERY: FALSE TRIGGER INTERCEPTED]" in res_trans_2.get("reason", "")
        print("  [PASS] Case 7: Transcript multiline with refusal on last line triggers continue")
    finally:
        Path(transcript_path_2).unlink(missing_ok=True)

    # Case 8: Transcript multiline with refusal mid-message followed by normal lines -> allow (no full message trigger)
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as tf:
        tf.write(json.dumps({
            "type": "PLANNER_RESPONSE",
            "content": "In previous runs, this request was blocked by Gemini's filters.\nHowever, after reframing the prompt, the audit completed successfully with 0 findings."
        }) + "\n")
        transcript_path_3 = tf.name

    try:
        res_trans_3 = run_hook({"transcriptPath": transcript_path_3})
        assert res_trans_3.get("decision") == "allow", f"Expected allow but got: {res_trans_3}"
        print("  [PASS] Case 8: Transcript multiline with refusal mid-message returns allow (not a full message trigger)")
    finally:
        Path(transcript_path_3).unlink(missing_ok=True)

    # Case 9: Multi-turn transcript where older step was blocked, but latest step completed normally -> allow
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as tf:
        tf.write(json.dumps({
            "type": "MODEL_RESPONSE",
            "content": "This request was blocked by Gemini's filters."
        }) + "\n")
        tf.write(json.dumps({
            "type": "USER_INPUT",
            "content": "Please continue with reframed terminology."
        }) + "\n")
        tf.write(json.dumps({
            "type": "PLANNER_RESPONSE",
            "content": "Reframed analysis complete. All checks passed."
        }) + "\n")
        transcript_path_4 = tf.name

    try:
        res_trans_4 = run_hook({"transcriptPath": transcript_path_4})
        assert res_trans_4.get("decision") == "allow", f"Expected allow but got: {res_trans_4}"
        print("  [PASS] Case 9: Multi-turn transcript with older blocked step and normal latest step returns allow")
    finally:
        Path(transcript_path_4).unlink(missing_ok=True)

    # Case 10: Tool response output containing refusal on last line -> allow (not a model refusal)
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as tf:
        tf.write(json.dumps({
            "type": "PLANNER_RESPONSE",
            "content": "Checking grep output",
            "tool_calls": [{"name": "run_command"}]
        }) + "\n")
        tf.write(json.dumps({
            "type": "TOOL_RESPONSE",
            "content": "grep: matching line\nThis request was blocked by Gemini's filters."
        }) + "\n")
        transcript_path_5 = tf.name

    try:
        res_trans_5 = run_hook({"transcriptPath": transcript_path_5})
        assert res_trans_5.get("decision") == "allow", f"Expected allow but got: {res_trans_5}"
        print("  [PASS] Case 10: Tool response containing refusal on last line returns allow")
    finally:
        Path(transcript_path_5).unlink(missing_ok=True)

    print("\nALL 10 FILTER RECOVERY TESTS PASSED SUCCESSFULLY.")


if __name__ == "__main__":
    test_suite()
