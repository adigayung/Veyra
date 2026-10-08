@echo off
set "VEYRA_KEYSTORE=J:\Veyra\.tmp_explorer\keystore.bin"
set "VEYRA_DEFAULT_PATH=J:\Veyra\.tmp_explorer\L0\L1\L2\L3\L4\L5"
cd /d J:\Veyra
J:\Veyra\venv\Scripts\python.exe J:\Veyra\run.py > J:\Veyra\.tmp_explorer\server.log 2>&1
