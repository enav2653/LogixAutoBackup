import asyncio
import sys
import os
import datetime
import argparse

from logix_designer_sdk import LogixProject, StdOutEventLogger

from Functions import (
    open_project,
    create_temp_l5x_file,
    save_as_l5x,
    parse_controller_name,
    cleanup_temp_file,
    close_project,
    create_temp_acd_file,
    upload_to_new_acd,
    get_controller_name_from_acd
)

async def main():
    # Argument parsing
    parser = argparse.ArgumentParser(
        description="Upload project from controller and save it locally with timestamp."
    )
    parser.add_argument("comm_path", help="Communication path to the controller (e.g., IP address or path)")
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
    save_dir = args.save_dir.rstrip(os.path.sep)  # Normalize path
    prefix = args.prefix

    # Ensure save directory exists
    os.makedirs(save_dir, exist_ok=True)

    # Create temporary ACD file
    new_project_path = create_temp_acd_file()
    cleanup_temp_file(new_project_path)

    try:
        print(f"\nUploading from controller ({comm_path})...")
        await upload_to_new_acd(new_project_path, comm_path)
        print("Uploaded successfully!")

        # Get controller name from the uploaded project
        project, controller_name = await get_controller_name_from_acd(new_project_path)

        # Generate timestamp
        now = datetime.datetime.now()
        date_time_str = now.strftime("%Y%m%d_%H%M")

        # Build final filename and path
        final_filename = f"{prefix}{controller_name}_{date_time_str}.ACD"
        final_path = os.path.join(save_dir, final_filename)

        print(f"\nSaving project as: {final_path}")
        await project.save_as(final_path, False, False)
        print("Project saved successfully!")

    finally:
        # Always clean up temp file and close project
        if 'project' in locals() and project:
            print("\nClosing project...")
            project.close()
            print("Project closed.")

        cleanup_temp_file(new_project_path)
        print("Temporary files cleaned up.")


if __name__ == "__main__":
    asyncio.run(main())