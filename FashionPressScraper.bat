@echo off
setlocal EnableExtensions DisableDelayedExpansion

pushd "%~dp0" || goto folder_error
if not exist "fashion_press_collections_scraper.py" goto missing_script

py -3 -c "import sys, tkinter; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto run_with_py

python -c "import sys, tkinter; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto run_with_python

echo Python 3.10 or later with Tcl/Tk is required.
echo https://www.python.org/downloads/windows/
goto launch_error

:run_with_py
py -3 "fashion_press_collections_scraper.py"
goto check_result

:run_with_python
python "fashion_press_collections_scraper.py"

:check_result
if errorlevel 1 goto launch_error
popd
exit /b 0

:missing_script
echo The scraper script is missing. Extract the entire ZIP and run this file from that folder.
goto launch_error

:folder_error
echo Could not open the scraper folder.
pause
exit /b 1

:launch_error
echo The scraper could not start. See the message above.
popd
pause
exit /b 1
