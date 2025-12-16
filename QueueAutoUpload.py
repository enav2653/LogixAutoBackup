import asyncio
import sys
import os
import datetime
import argparse
import msvcrt  # Windows-specific, safe to import at top on Windows
from logix_designer_sdk import LogixProject, StdOutEventLogger
from Functions import (
    create_temp_acd_file,
    upload_to_new_acd,
    get_controller_name_from_acd,
    cleanup_temp_file
)

# Lock file in user's home directory (safe and accessible)
LOCK_FILE = os.path.join(os.path.expanduser("~"), ".logix_backup_upload.lock")
MAX_WAIT_TIME = 3600  # Max wait time in seconds (1 hour)
POLL_INTERVAL = 10    # How often to check lock

async def acquire_lock():
    lock_fd = None
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_BINARY)

        start_time = datetime.datetime.now()
        print("Waiting for access to controller upload (will queue if another backup is running)...")

        while True:
            try:
                os.lseek(lock_fd, 0, os.SEEK_SET)
                msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)  # Non-blocking lock attempt
                print("Lock acquired. Proceeding with upload...")
                return lock_fd
            except (BlockingIOError, OSError, IOError):
                elapsed = (datetime.datetime.now() - start_time).total_seconds()
                if elapsed > MAX_WAIT_TIME:
                    print(f"Timeout after waiting {MAX_WAIT_TIME} seconds for lock. Exiting.")
                    sys.exit(1)
                print(f"Another backup in progress. Waiting... ({int(elapsed)}s elapsed)")
                await asyncio.sleep(POLL_INTERVAL)

    except Exception as e:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except:
                pass
        print(f"Unexpected error acquiring lock: {e}", file=sys.stderr)
        sys.exit(1)

def release_lock(lock_fd):
    if lock_fd is None:
        return
    try:
        os.lseek(lock_fd, 0, os.SEEK_SET)
        msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
        os.close(lock_fd)
    except:
        pass
    finally:
        try:
            os.remove(LOCK_FILE)
        except:
            pass

async def main():
    parser = argparse.ArgumentParser(
        description="Upload project from controller and save it locally with timestamp. Only one upload at a time."
    )
    parser.add_argument("comm_path", help="Communication path to the controller")
    parser.add_argument(
        "--save-dir",
        default=".",
        help="Directory to save the final .ACD file (default: current directory)",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Optional prefix for the saved filename (e.g., 'Backup_')",
    )
    args = parser.parse_args()

    comm_path = args.comm_path
    save_dir = args.save_dir.rstrip(os.path.sep)
    prefix = args.prefix

    os.makedirs(save_dir, exist_ok=True)

    lock_fd = None
    new_project_path = None
    project = None

    try:
        lock_fd = await acquire_lock()

        # Create temp ACD and immediately register it for cleanup
        new_project_path = create_temp_acd_file()
        cleanup_temp_file(new_project_path)  # <-- Critical: ensures cleanup even on early exit
        print(f"Temporary project created: {new_project_path}")

        print(f"\nUploading from controller ({comm_path})...")
        await upload_to_new_acd(new_project_path, comm_path)
        print("Uploaded successfully!")

        project, controller_name = await get_controller_name_from_acd(new_project_path)

        now = datetime.datetime.now()
        date_time_str = now.strftime("%Y%m%d_%H%M")
        final_filename = f"{prefix}{controller_name}_{date_time_str}.ACD"
        final_path = os.path.join(save_dir, final_filename)

        print(f"\nSaving project as: {final_path}")
        await project.save_as(final_path, False, False)
        print("Project saved successfully!")

    except Exception as e:
        print(f"Error during backup: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Cleanup in reverse order
        if project:
            print("\nClosing project...")
            try:
                project.close()
            except:
                pass
            print("Project closed.")

        if new_project_path:
            cleanup_temp_file(new_project_path)  # Safe to call again (idempotent if already cleaned)
            print("Temporary files cleaned up.")

        if lock_fd is not None:
            print("Releasing lock for next queued backup...")
            release_lock(lock_fd)
            print("Lock released.")

if __name__ == "__main__":
    asyncio.run(main())