@echo off
set PROFILE_OPENGL=1
echo PROFILE_OPENGL=%PROFILE_OPENGL%
python -X utf8 tests_gui/test_network_direct.py --bitrate 2500000 --max-fps 30 --codec h265
