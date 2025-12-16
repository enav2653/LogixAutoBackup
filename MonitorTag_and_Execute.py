# logix_watcher_lint.py - With offline pre-check on new project (fixed & improved)
import asyncio
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from logix_designer_sdk import LogixProject, OperationMode

# ====================== USER CONFIGURATION ======================
PROJECT_DIR = r"C:\Users\User\PLC_ProgramBackups\C0TR2\DSF" #Directory where to monitor
FILE_STARTS_WITH = "TMMI_C0TR2_DSF" #What the files start with to find latest backup
MONITOR_TAG_NAME = "ControllerAuditValue" #Tag name to monitor in the PLC, needs to be type LINT
STABILITY_SECONDS = 1800 #Time from last detected change to backup being queued
POLL_INTERVAL = 2.0 #How often tag defined in "MONITOR_TAG_NAME" is checked

EXTERNAL_PROGRAM = [
    r"python",
    r"C:\Users\User\BackupAutomation\QueueAutoUpload.py", #Path to backup automation program
    r"--save-dir", #Specifier to save in a user defined directory
    r"C:\Users\User\PLC_ProgramBackups\C0TR2\DSF", #Path to save the backups in
    r"TR2_Gateway_IS\10.207.134.208\Backplane\2\A\192.168.1.100\Backplane\0" #Path to PLC, copy from program, requires FT Linx
	]
SCRIPT_DIR = r"C:\Users\User\BackupAutomation" #Directory to run the backup program from, usually the directory it is in.
# =================================================================

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
        current_last_value = value
        last_known_value = value
        last_triggered_value = value
        return value
    except Exception as e:
        print(f"[OFFLINE CHECK] Failed to read tag offline: {e}")
        return None

async def monitor_and_trigger_lint(
    project: LogixProject,
    tag_name: str,
    stability_sec: float,
    poll_sec: float,
    last_known_value: Any | None,
    wait_for_change: bool
):
    xpath = f"Controller/Tags/Tag[@Name='{tag_name}']"
    current_last_value = last_known_value
    current_waiting = wait_for_change
    stable_start = None

    status_msg = f"Monitoring '{tag_name}' for changes. . ." if current_waiting else f"Monitoring {tag_name} for stability..."
    print(f"[MONITOR] {status_msg}")

    while True:
        try:
            current_value = await project.get_tag_value_lint(xpath, mode=OperationMode.ONLINE)

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
                    pass  # Still waiting for first change after trigger
                elif stable_start is None:
                    print(f"[STABLE START] Tag stable at {current_value}. Starting {stability_sec}s countdown...")
                    stable_start = time.time()
                elif time.time() - stable_start >= stability_sec:
                    print(f"[TRIGGER] Tag stable for {stability_sec}s at value {current_value}. Launching external program...")
                    return True, current_value, True

            await asyncio.sleep(poll_sec)
        except Exception as e:
            print(f"[ERROR] Failed to read tag '{tag_name}': {e}")
            await asyncio.sleep(poll_sec)

def run_external_program(cmd):
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
    last_triggered_value: Any | None = None   # Value that successfully triggered external program
    waiting_for_change = False               # After success, wait for a new different value
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

            # ---------- New backup file detected ----------
            if new_path_str != current_project_path:
                print(f"[NEW BACKUP DETECTED] Loading: {latest_path.name}")

                # Clean up previous project completely
                if project is not None:
                    try:
                        if is_online:
                            await project.go_offline()
                        else:
                            pass
                        
                        if project is not None:
                            await project.close()
                        else:
                            pass
                            
                    except Exception as e:
                        print(f"[CLEANUP] Error during close: {e}")
                    project = None
                    is_online = False

                # Open new project offline first
                project = await LogixProject.open_logix_project(new_path_str, None)
                current_project_path = new_path_str

                offline_value = await get_offline_tag_value(project, MONITOR_TAG_NAME)
                
                if FirstRun:
                    last_triggered_value = offline_value
                    FirstRun = False
                else:
                    pass
                    
                #print(f"offline_value = {offline_value}\n")
                #print(f"last_triggered_value = {last_triggered_value}\n")
                if offline_value is not None:
                    print(f"[OFFLINE] Tag '{MONITOR_TAG_NAME}' value: {offline_value}")

                    if offline_value == last_triggered_value and last_triggered_value is not None:
                        #print("[INFO] Going online")
                        waiting_for_change = True  # Crucial: keep waiting
                    else:
                        print("[INFO] Tag value has changed since last run — will monitor for stability immediately.")
                        waiting_for_change = False
                        last_triggered_value = None  # Optional: treat as completely fresh
                else:
                    print("[WARN] Could not read tag offline — assuming change, will monitor for stability.")
                    waiting_for_change = False

                # ALWAYS go online for a new backup file
                print("[ONLINE] Going online to monitor...")
                await project.go_online()
                is_online = True

            # ---------- Same file, still waiting after previous success ----------
            elif waiting_for_change:
                print("[WAIT] Waiting for new tag change in current project. Checking again in 30s...")
                await asyncio.sleep(30)
                continue

            # ---------- Same file, but should be monitoring (safety net) ----------
            else:
                if not is_online:
                    print("[RECOVER] Project not online — going online again.")
                    await project.go_online()
                    is_online = True

            # ---------- Monitor loop ----------
            triggered, new_trigger_value, waiting_for_change = await monitor_and_trigger_lint(
                project,
                MONITOR_TAG_NAME,
                STABILITY_SECONDS,
                POLL_INTERVAL,
                last_triggered_value,
                waiting_for_change
            )

            if triggered:
                success = run_external_program(EXTERNAL_PROGRAM)
                if success:
                    print("[CYCLE] Backup/upload completed successfully. Going offline. Waiting for next change...\n")
                    last_triggered_value = new_trigger_value   # Remember the value that worked
                    waiting_for_change = True
                    await project.go_offline()
                    is_online = False
                    await asyncio.sleep(10)
                else:
                    print("[CYCLE] Backup/upload failed. Staying online, waiting for next stable change.\n")
                    waiting_for_change = True

        except Exception as e:
            print(f"[FATAL ERROR] Unexpected error: {e}")
            if project is not None:
                try:
                    if is_online:
                        await project.go_offline()
                    await project.close()
                except:
                    pass
                project = None
                is_online = False
            last_triggered_value = None
            waiting_for_change = False
            await asyncio.sleep(30)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n[STOP] Watcher stopped by user.")