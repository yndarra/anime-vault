' anime-vault: window (anime_vault\gui) - download new pins, distribute them into dataN, settings.
' The .bat launchers in the collection (download.bat, distribute.bat) keep working as before.
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = root
' pythonw: no console - command output is shown inside the window.
shell.Run """" & root & "\venv\Scripts\pythonw.exe"" -m anime_vault.gui", 1, False
