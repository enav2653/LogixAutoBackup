# logix_watcher_lint.py - With improved recovery for persistent RSLinx/FT Linx comm loss
import asyncio
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from logix_designer_sdk import LogixProject, OperationMode

# ====================== USER CONFIGURATION ======================
PROJECT_DIR = r"C:\Users\User\PLC_ProgramBackups\C0TR2\PRESS"  # Updated to match your log
FILE_STARTS_WITH = "TMMI_C0TR2_PRESS"
MONITOR_TAG_NAME = "ControllerAuditValue"
STABILITY_SECONDS = 1800
POLL_INTERVAL = 2.0
EXTERNAL_PROGRAM = [
    r"python",
    r"C:\Users\User\BackupAutomation\QueueAutoUpload.py",
    r"--save-dir",
    r"C:\Users\User\PLC_ProgramBackups\C0TR2\PRESS",
    r"TR2_Gateway_IS\10.207.134.208\Backplane\2\A\192.168.1.100\Backplane\0"  # Adjust if needed
]
SCRIPT_DIR = r"C:\Users\User\BackupAutomation"
# =================================================================

def is_connection_or_license_error(e: Exception) -> bool:
    """Detect connection/licensing/comms errors, including your specific one."""
    msg = str(e).lower()
    return any(keyword in msg for keyword in [
        "connection", "communications", "comms", "lost connection",
        "license", "licensing", "activation", "checkout failed",
        "timeout", "failed to connect", "unable to establish",
        "rslinx", "linx", "cannotsenddata", "cannot communicate with linx"
    ])

async def find_latest_acd(directory: str, starts_with: str | None = None) -> Path | None:
    dir_path = Path(directory)
    if not dir_path.exists() or not dir_path.is_dir():
        print(f"[ERROR] Directory not found: {directory}")
        return None
    acd_files = list(dir_path.glob("*.ACD"))
    if starts_with:
        acd_files = [f for f in acd_files if f.name.startswith(starts_with)]
    if not acd_files:
        print(f"[INFO] No .ACD files found" + (f" starting with '{starts_with}'" if starts_with else ""))
        return None
    latest = max(acd_files, key=lambda p: p.stat().st_mtime)
    print(f"[INFO] Latest project: {latest.name} (modified {datetime.fromtimestamp(latest.stat().st_mtime)})")
    return latest

async def get_offline_tag_value(project: LogixProject, tag_name: str) -> Any | None:
    xpath = f"Controller/Tags/Tag[@Name='{tag_name}']"
    try:
        value = await project.get_tag_value_lint(xpath, OperationMode.OFFLINE)
        return value
    except Exception as e:
        print(f"[OFFLINE CHECK] Failed to read tag offline: {e}")
        return None

async def fully_reset_project(current_path: str) -> LogixProject | None:
    """Close and re-open the project to fully reset comms state."""
    print("[RECOVER] Performing FULL project reset (close + re-open) due to persistent comm error...")
    temp_project = None
    try:
        temp_project = await LogixProject.open_logix_project(current_path, None)
        print("[RECOVER] Project re-opened successfully.")
        
        # Attempt to go online with retries
        for attempt in range(5):
            try:
                await temp_project.go_online()
                print("[RECOVER] Back online after full reset.")
                return temp_project
            except Exception as online_e:
                if is_connection_or_license_error(online_e):
                    print(f"[RECOVER] Online attempt {attempt+1}/5 failed: {online_e}")
                    await asyncio.sleep(10 * (attempt + 1))  # Exponential backoff
                else:
                    raise
        print("[RECOVER] Failed to go online after full reset.")
        await temp_project.close()
        return None
    except Exception as e:
        print(f"[RECOVER] Failed during full project reset: {e}")
        if temp_project:
            try:
                await temp_project.close()
            except:
                pass
        return None

async def monitor_and_trigger_lint(
    project: LogixProject,
    tag_name: str,
    stability_sec: float,
    poll_sec: float,
    last_known_value: Any | None,
    wait_for_change: bool,
    current_path: str  # Pass current ACD path for reset
):
    xpath = f"Controller/Tags/Tag[@Name='{tag_name}']"
    current_last_value = last_known_value
    current_waiting = wait_for_change
    stable_start = None
    consecutive_errors = 0

    status_msg = f"Monitoring '{tag_name}' for changes..." if current_waiting else f"Monitoring {tag_name} for stability..."
    print(f"[MONITOR] {status_msg}")

    while True:
        try:
            current_value = await project.get_tag_value_lint(xpath, mode=OperationMode.ONLINE)

            consecutive_errors = 0  # Reset error count on success

            if current_last_value is None:
                print(f"[MONITOR] Connected. Current value: {current_value}")
                current_last_value = current_value
                continue

            if current_value != current_last_value:
                print(f"[CHANGE DETECTED] Tag '{tag_name}': {current_last_value} → {current_value}")
                if current_waiting:
                    print("[READY] Change detected after previous cycle. Starting stability countdown...")
                    current_waiting = False
                stable_start = time.time()
                current_last_value = current_value
            else:
                if current_waiting:
                    pass
                elif stable_start is None:
                    print(f"[STABLE START] Tag stable at {current_value}. Starting {stability_sec}s countdown...")
                    stable_start = time.time()
                elif time.time() - stable_start >= stability_sec:
                    print(f"[TRIGGER] Tag stable for {stability_sec}s at value {current_value}. Launching external program...")
                    return True, current_value, True

            await asyncio.sleep(poll_sec)

        except Exception as e:
            consecutive_errors += 1
            if is_connection_or_license_error(e):
                print(f"[COMM ERROR #{consecutive_errors}] {e}")
                if consecutive_errors >= 5:  # After ~10 seconds of failures, do full reset
                    new_project = await fully_reset_project(current_path)
                    if new_project:
                        # Return control to main_loop with the new project instance
                        raise RuntimeError("FULL_RESET_REQUIRED") from e
                    else:
                        print("[RECOVER] Full reset failed. Waiting 60s before retry...")
                        await asyncio.sleep(60)
                        consecutive_errors = 0  # Reset to try simple reads again
                else:
                    # For first few errors, just sleep and retry
                    await asyncio.sleep(poll_sec)
            else:
                print(f"[ERROR] Failed to read tag '{tag_name}': {e}")
                await asyncio.sleep(poll_sec)

def run_external_program(cmd):
    # (unchanged from previous version)
    print(f"[EXEC] Running external program: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=SCRIPT_DIR,
            check=True,
            capture_output=True,
            text=True
        )
        print("[EXEC] External program finished successfully.")
        if result.stdout.strip():
            print("Output:\n" + result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[EXEC ERROR] Program failed (return code {e.returncode})")
        if e.stdout:
            print("STDOUT:", e.stdout.strip())
        if e.stderr:
            print("STDERR:", e.stderr.strip())
        return False
    except Exception as e:
        print(f"[EXEC ERROR] Failed to launch: {e}")
        return False

async def main_loop():
    current_project_path: str | None = None
    project: LogixProject | None = None
    is_online = False
    last_triggered_value: Any | None = None
    waiting_for_change = False
    FirstRun = True

    print("[START] Logix Watcher started.\n")

    while True:
        try:
            latest_path = await find_latest_acd(PROJECT_DIR, FILE_STARTS_WITH if FILE_STARTS_WITH else None)
            if latest_path is None:
                print("[WAIT] No matching project found. Sleeping 60s...")
                await asyncio.sleep(60)
                continue

            new_path_str = str(latest_path)

            if new_path_str != current_project_path:
                # (new file handling - mostly unchanged, but ensures clean close)
                print(f"[NEW BACKUP DETECTED] Loading: {latest_path.name}")
                if project is not None:
                    try:
                        if is_online:
                            await project.go_offline()
                        await project.close()
                    except Exception as e:
                        print(f"[CLEANUP] Error during close: {e}")
                    project = None
                    is_online = False

                project = await LogixProject.open_logix_project(new_path_str, None)
                current_project_path = new_path_str

                offline_value = await get_offline_tag_value(project, MONITOR_TAG_NAME)

                if FirstRun:
                    last_triggered_value = offline_value
                    FirstRun = False

                if offline_value is not None:
                    print(f"[OFFLINE] Tag '{MONITOR_TAG_NAME}' value: {offline_value}")
                    if offline_value == last_triggered_value and last_triggered_value is not None:
                        waiting_for_change = True
                    else:
                        print("[INFO] Tag value changed since last run — monitoring stability now.")
                        waiting_for_change = False
                        last_triggered_value = None
                else:
                    print("[WARN] Offline tag read failed — assuming change.")
                    waiting_for_change = False

                # Go online with retries
                for attempt in range(10):
                    try:
                        await project.go_online()
                        is_online = True
                        break
                    except Exception as e:
                        if is_connection_or_license_error(e):
                            print(f"[ONLINE RETRY {attempt+1}/10] {e}")
                            await asyncio.sleep(10)
                        else:
                            raise
                else:
                    print("[FATAL] Could not go online after retries. Waiting 60s...")
                    await asyncio.sleep(60)
                    continue

            elif waiting_for_change:
                print("[WAIT] Waiting for new tag change. Checking in 30s...")
                await asyncio.sleep(30)
                continue
            else:
                if not is_online:
                    print("[RECOVER] Not online — re-trying go_online...")
                    try:
                        await project.go_online()
                        is_online = True
                    except Exception as e:
                        if is_connection_or_license_error(e):
                            await asyncio.sleep(10)
                        else:
                            raise

            # Monitor loop - pass current_path for potential reset
            try:
                triggered, new_trigger_value, waiting_for_change = await monitor_and_trigger_lint(
                    project,
                    MONITOR_TAG_NAME,
                    STABILITY_SECONDS,
                    POLL_INTERVAL,
                    last_triggered_value,
                    waiting_for_change,
                    current_project_path
                )
            except RuntimeError as reset_e:
                if str(reset_e) == "FULL_RESET_REQUIRED":
                    # Project was reset inside monitor function - get new instance from global? Wait, better: continue loop to re-detect same file
                    print("[RECOVER] Full reset completed inside monitor. Restarting monitor loop...")
                    continue  # Will re-enter monitor with new project

            if triggered:
                success = run_external_program(EXTERNAL_PROGRAM)
                if success:
                    print("[CYCLE] Backup/upload completed. Going offline and waiting for next change...\n")
                    last_triggered_value = new_trigger_value
                    waiting_for_change = True
                    await project.go_offline()
                    is_online = False
                    await asyncio.sleep(10)
                else:
                    print("[CYCLE] Backup/upload failed. Staying online for next stable period.\n")
                    waiting_for_change = True

        except Exception as e:
            print(f"[FATAL ERROR] Unexpected: {e}")
            if project is not None:
                try:
                    if is_online:
                        await project.go_offline()
                    await project.close()
                except:
                    pass
                project = None
                is_online = False
            await asyncio.sleep(30)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n[STOP] Watcher stopped by user.")