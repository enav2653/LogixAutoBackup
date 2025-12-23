# logix_watcher_lint.py - With offline pre-check on new project (fixed & improved)
import asyncio
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Tuple, Union
from logix_designer_sdk import LogixProject, OperationMode

# ====================== USER CONFIGURATION ======================
# Directory containing the downloaded .ACD backup files
PROJECT_DIR = r"C:\PLC_Backups\MyProject"  

# Optional: only consider files that start with this prefix (set to None to match any .ACD file)
FILE_STARTS_WITH = "MyProject"  # Example: "TMMI_LINE1" or None

# Name of the LINT tag in the controller that changes whenever the program is edited/saved
MONITOR_TAG_NAME = "ControllerAuditValue"

# How long the tag must remain unchanged before triggering a backup/upload (seconds)
STABILITY_SECONDS = 1800  # 30 minutes

# How often to poll the tag while online (seconds)
POLL_INTERVAL = 2.0

# External program/script to run when stability condition is met
# Example shown: a Python script that handles uploading or processing the backup
EXTERNAL_PROGRAM = [
    r"python",
    r"C:\Automation\BackupUploader.py",   # Your backup/upload script
    r"--save-dir",
    r"C:\PLC_Backups\MyProject",          # Where backups are stored
    r"ControllerPath\In\Your\PLC"         # Full controller path as used in Studio 5000 (e.g., Ethernet\192.168.1.100)
]

# Working directory when launching the external program (usually the folder containing the script)
SCRIPT_DIR = r"C:\Automation"
# =================================================================

def is_connection_or_license_error(e: Exception) -> bool:
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
    print("[RECOVER] Performing FULL project reset due to persistent comm error...")
    temp_project = None
    try:
        temp_project = await LogixProject.open_logix_project(current_path, None)
        print("[RECOVER] Project re-opened successfully.")
        for attempt in range(5):
            try:
                await temp_project.go_online()
                print("[RECOVER] Back online after full reset.")
                return temp_project
            except Exception as online_e:
                if is_connection_or_license_error(online_e):
                    print(f"[RECOVER] Online attempt {attempt+1}/5 failed: {online_e}")
                    await asyncio.sleep(10 * (attempt + 1))
                else:
                    raise
        print("[RECOVER] Failed to go online after reset.")
        await temp_project.close()
        return None
    except Exception as e:
        print(f"[RECOVER] Failed during full reset: {e}")
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
    current_path: str
) -> Tuple[Union[bool, str], Any]:
    """
    Always monitors for stability.
    Returns:
        (True, new_value) on trigger
        ("RESET_SUCCESS", new_project) on successful reset
    """
    xpath = f"Controller/Tags/Tag[@Name='{tag_name}']"
    last_value = None
    stable_start = None
    consecutive_errors = 0

    print(f"[MONITOR] Monitoring '{tag_name}' for stability ({stability_sec}s after any change)...")

    while True:
        try:
            current_value = await project.get_tag_value_lint(xpath, mode=OperationMode.ONLINE)
            consecutive_errors = 0

            if last_value is None:
                print(f"[MONITOR] Connected. Current value: {current_value}")
                last_value = current_value
                stable_start = time.time()  # Start countdown immediately on first read
                continue

            if current_value != last_value:
                print(f"[CHANGE DETECTED] Tag '{tag_name}': {last_value} → {current_value}")
                stable_start = time.time()
                last_value = current_value
            else:
                if stable_start is None:
                    stable_start = time.time()
                elif time.time() - stable_start >= stability_sec:
                    print(f"[TRIGGER] Tag stable for {stability_sec}s at value {current_value}. Launching backup...")
                    return True, current_value

            await asyncio.sleep(poll_sec)

        except Exception as e:
            consecutive_errors += 1
            if is_connection_or_license_error(e):
                print(f"[COMM ERROR #{consecutive_errors}] {e}")
                if consecutive_errors >= 5:
                    new_project = await fully_reset_project(current_path)
                    if new_project:
                        return "RESET_SUCCESS", new_project
                    else:
                        print("[RECOVER] Reset failed. Waiting 60s...")
                        await asyncio.sleep(60)
                        consecutive_errors = 0
                else:
                    await asyncio.sleep(poll_sec)
            else:
                print(f"[ERROR] Failed to read tag: {e}")
                await asyncio.sleep(poll_sec)

def run_external_program(cmd):
    print(f"[EXEC] Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=SCRIPT_DIR, check=True, capture_output=True, text=True)
        print("[EXEC] Success.")
        if result.stdout.strip():
            print("Output:\n" + result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[EXEC ERROR] Failed (code {e.returncode})")
        if e.stdout: print("STDOUT:", e.stdout.strip())
        if e.stderr: print("STDERR:", e.stderr.strip())
        return False
    except Exception as e:
        print(f"[EXEC ERROR] Launch failed: {e}")
        return False

async def main_loop():
    current_project_path: str | None = None
    project: LogixProject | None = None
    is_online = False

    print("[START] Logix Watcher started.\n")

    while True:
        try:
            latest_path = await find_latest_acd(PROJECT_DIR, FILE_STARTS_WITH if FILE_STARTS_WITH else None)
            if latest_path is None:
                print("[WAIT] No project found. Sleeping 60s...")
                await asyncio.sleep(60)
                continue

            new_path_str = str(latest_path)

            # New or different backup file detected
            if new_path_str != current_project_path:
                print(f"[NEW BACKUP DETECTED] Loading: {latest_path.name}")

                if project is not None:
                    try:
                        if is_online:
                            await project.go_offline()
                        await project.close()
                    except Exception as e:
                        print(f"[CLEANUP] Error: {e}")
                    project = None
                    is_online = False

                project = await LogixProject.open_logix_project(new_path_str, None)
                current_project_path = new_path_str

                offline_value = await get_offline_tag_value(project, MONITOR_TAG_NAME)
                if offline_value is not None:
                    print(f"[OFFLINE] Tag value: {offline_value}")
                else:
                    print("[WARN] Could not read offline tag value.")

                print("[ONLINE] Going online...")
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
                    print("[FATAL] Cannot go online. Waiting 60s...")
                    await asyncio.sleep(60)
                    continue

            # If not online (rare recovery case), try to fix
            if not is_online:
                print("[RECOVER] Attempting to go online...")
                try:
                    await project.go_online()
                    is_online = True
                except Exception as e:
                    if is_connection_or_license_error(e):
                        await asyncio.sleep(10)
                    else:
                        raise

            # Always monitor for stability when online
            result = await monitor_and_trigger_lint(
                project,
                MONITOR_TAG_NAME,
                STABILITY_SECONDS,
                POLL_INTERVAL,
                current_project_path
            )

            if result[0] == "RESET_SUCCESS":
                new_project = result[1]
                print("[RECOVER] Applying new project after reset.")
                try:
                    if is_online:
                        await project.go_offline()
                    await project.close()
                except:
                    pass
                project = new_project
                is_online = True
                continue  # Resume monitoring immediately

            triggered, trigger_value = result
            if triggered:
                success = run_external_program(EXTERNAL_PROGRAM)
                if success:
                    print("[CYCLE] Backup/upload completed. Going offline until next change...\n")
                    await project.go_offline()
                    is_online = False
                    # Wait for a change before restarting full monitoring
                    last_seen = trigger_value
                    print("[WAIT] Waiting for tag value to change before next cycle...")
                    while True:
                        try:
                            await project.go_online()
                            is_online = True
                            current = await project.get_tag_value_lint(
                                f"Controller/Tags/Tag[@Name='{MONITOR_TAG_NAME}']",
                                mode=OperationMode.ONLINE
                            )
                            if current != last_seen:
                                print(f"[CHANGE AFTER BACKUP] New value {current} ≠ {last_seen}. Starting new cycle...")
                                break
                            await project.go_offline()
                            is_online = False
                        except:
                            pass
                        await asyncio.sleep(30)
                else:
                    print("[CYCLE] Backup failed. Continuing to monitor current stability period...")

        except Exception as e:
            print(f"[FATAL ERROR] {e}")
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
        print("\n[STOP] Stopped by user.")