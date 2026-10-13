import os
import sys

if sys.platform == "win32":
    _base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    os.environ["PYTHONNET_RUNTIME"] = "netfx"
    _dll_name = f"python{sys.version_info[0]}{sys.version_info[1]}.dll"
    for root, dirs, files in os.walk(_base):
        if _dll_name in files:
            os.environ["PYTHONNET_PYDLL"] = os.path.join(root, _dll_name)
            break