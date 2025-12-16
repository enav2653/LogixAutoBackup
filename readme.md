This program automatically uploads from Logix 5000 PLC's.
To set up, copy 'MonitorTag_and_Execute.py, and rename.  
Open with a text editor, and put in the parameters at the top of the program and save

====================== USER CONFIGURATION ======================

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

=================================================================

This program requires:
	Python 3.12,
	Logix Designer SDK from Rockwell,
	FT Linx,
	Factory Talk Activation Manager
