import pythonnet
import clr
import webview
import psutil
import subprocess
import ctypes
import wmi
import platform
import webbrowser
import json
import os
import re
import sys
import threading
import time
import pystray
from PIL import Image
from datetime import datetime
from ctypes import wintypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
        ("dmOrientation", ctypes.c_short), ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short), ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short), ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short), ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)
    ]


def detect_power_plans():
    """
    Windows'taki tüm güç planlarını otomatik bulur.
    Her marka için farklı GUID'ler otomatik algılanır.
    """
    try:
        r = subprocess.run(
            ["powercfg", "/list"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="ignore"
        )
        output = r.stdout
    except Exception:
        return {}

    plans = {}
    pattern = r"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\s+\(([^)]+)\)"

    for match in re.finditer(pattern, output):
        guid = match.group(1).lower()
        name = match.group(2).strip().lower()

        if ("dengeli" in name or "balanced" in name) and "windows" not in plans:
            plans["windows"] = guid
        elif any(k in name for k in
                 ["tasarruf", "saver", "sessiz", "quiet", "silent", "battery"]) and "silent" not in plans:
            plans["silent"] = guid
        elif any(k in name for k in ["turbo", "ultimate", "maximum", "nihai"]) and "turbo" not in plans:
            plans["turbo"] = guid
        elif any(k in name for k in ["performans", "performance"]) and "performance" not in plans:
            plans["performance"] = guid

    return plans


class Api:
    def __init__(self):
        self._lock = threading.Lock()
        self._wmi = None
        self._wmi_bright = None
        self._sysinfo = None
        self._last_net = None
        self._running = True
        self._low_batt = False
        self.config_file = "config.json"
        self.config = self._load_config()
        self._net_baslangic = self.config.get("last_net_total", 0)
        # Güç planlarını otomatik bul
        self.POWER_GUIDS = detect_power_plans()
        self._stats = {
            "cpu": 0, "ram": 0, "disk": 0,
            "net": "0 B", "net_pct": 0,
            "uptime": "...", "battery": 0, "battery_status": "Yok",
            "battery_plugged": False, "resolution": "0x0", "hz": "0"
        }
        threading.Thread(target=self._stats_loop, daemon=True).start()

    @staticmethod
    def _fmt(b):
        if b < 1024:      return f"{b:.0f} B"
        if b < 1024 ** 2:   return f"{b / 1024:.1f} KB"
        if b < 1024 ** 3:   return f"{b / 1024 ** 2:.1f} MB"
        if b < 1024 ** 4:   return f"{b / 1024 ** 3:.1f} GB"
        if b < 1024 ** 5:   return f"{b / 1024 ** 4:.1f} TB"
        return f"{b / 1024 ** 5:.1f} PB"

    def _get_wmi(self):
        if self._wmi is None and wmi is not None:
            try:
                self._wmi = wmi.WMI()
            except Exception:
                self._wmi = False
        return self._wmi or None

    @staticmethod
    def _run(cmd):
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
            r = subprocess.run(
                cmd,
                shell=False,
                capture_output=True,
                timeout=10,
                creationflags=creationflags
            )
            return r.returncode == 0
        except Exception:
            return False

    def _load_config(self):
        default = {
            "theme": "dark", "language": "TR", "power_mode": "windows",
            "refresh_rate": "144", "brightness": 80, "volume": 50,
            "charge_limit": 60, "power_save": False, "custom_themes": {},
            "last_net_total": 0, "startup": False
        }
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r", encoding="utf-8") as f:
                    default.update(json.load(f))
        except Exception:
            pass
        return default

    def _write_config(self):
        tmp = self.config_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=4)
        os.replace(tmp, self.config_file)

    def _save_setting(self, k, v):
        with self._lock:
            self.config[k] = v
            try:
                self._write_config(); return True
            except Exception:
                return False

    def _stats_loop(self):
        psutil.cpu_percent(interval=None)
        time.sleep(0.3)
        while self._running:
            try:
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory().percent
                try:
                    drive = os.environ.get("SystemDrive", "C:") + "\\"
                    disk = psutil.disk_usage(drive).percent
                except Exception:
                    disk = 0
                net = psutil.net_io_counters()
                simdiki = net.bytes_recv + net.bytes_sent
                total = self._net_baslangic + simdiki
                self._last_net = simdiki
                if not hasattr(self, "_net_save_counter"):
                    self._net_save_counter = 0
                self._net_save_counter += 1
                if self._net_save_counter >= 30:
                    self._save_setting("last_net_total", total)
                    self._net_save_counter = 0
                net_str = self._fmt(total)
                net_pct = 0
                if total > 0:
                    if total < 1024:
                        net_pct = max(1.0, (total / 1024.0) * 3.0)
                    elif total < 1024 ** 2:
                        net_pct = 3.0 + (total / 1024.0 / 1024.0) * 7.0
                    elif total < 1024 ** 3:
                        net_pct = 10.0 + (total / 1024.0 / 1024.0 / 1024.0) * 5.0
                    elif total < 1024 ** 4:
                        net_pct = 15.0 + (total / 1024.0 ** 3 - 1.0) / 999.0 * 75.0
                    else:
                        net_pct = 90.0 + min(9.0, (total / 1024.0 ** 4 - 1.0) * 0.1)
                try:
                    d = datetime.now() - datetime.fromtimestamp(psutil.boot_time())
                    up = f"{d.days}g {d.seconds // 3600}s {(d.seconds // 60) % 60}dk" if d.days > 0 else f"{d.seconds // 3600}s {(d.seconds // 60) % 60}dk"
                except Exception:
                    up = "..."
                pct, st, plug = 0, "Yok", False
                try:
                    b = psutil.sensors_battery()
                    if b:
                        pct = int(b.percent)
                        plug = bool(b.power_plugged)
                        st = "Şarjda" if plug else "Pilde"
                except Exception:
                    pass
                low = 0 < pct < 20 and not plug
                if low and not self._low_batt:
                    silent_guid = self.POWER_GUIDS.get("silent")
                    if silent_guid:
                        self._run(["powercfg", "/setactive", silent_guid])
                    self._low_batt = True
                elif not low:
                    self._low_batt = False
                res, hz = "0x0", "0"
                try:
                    u = ctypes.windll.user32
                    w_, h_ = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
                    res = f"{w_}x{h_}"
                    dm = DEVMODE();
                    dm.dmSize = ctypes.sizeof(DEVMODE)
                    if u.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
                        hz = str(dm.dmDisplayFrequency)
                except Exception:
                    pass
                self._stats.update({
                    "cpu": round(cpu, 1), "ram": round(ram, 1), "disk": round(disk, 1),
                    "net": net_str, "net_pct": round(net_pct, 1),
                    "uptime": up, "battery": pct,
                    "battery_status": st, "battery_plugged": plug,
                    "resolution": res, "hz": hz
                })
            except Exception:
                pass
            time.sleep(1.0)

    def get_stats(self):
        return dict(self._stats)

    def get_config(self):
        return dict(self.config)

    def save_config(self, theme, language):
        with self._lock:
            self.config["theme"] = theme
            self.config["language"] = language
            try:
                self._write_config(); return True
            except Exception:
                return False

    def change_power_mode(self, mode):
        g = self.POWER_GUIDS.get(mode)
        if not g:
            self.POWER_GUIDS = detect_power_plans()
            g = self.POWER_GUIDS.get(mode)
        if not g:
            return False
        ok = self._run(["powercfg", "/setactive", g])
        if ok:
            self._save_setting("power_mode", mode)
        return ok

    def toggle_power_save(self, enable):
        key = "silent" if enable else "windows"
        g = self.POWER_GUIDS.get(key)
        if not g:
            self.POWER_GUIDS = detect_power_plans()
            g = self.POWER_GUIDS.get(key)
        if not g:
            return False
        ok = self._run(["powercfg", "/setactive", g])
        if ok:
            self._save_setting("power_save", bool(enable))
        return ok

    def change_refresh_rate(self, hz):
        try:
            dm = DEVMODE();
            dm.dmSize = ctypes.sizeof(DEVMODE)
            if not ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
                return False
            dm.dmDisplayFrequency = 60 if str(hz) == "60" else 144
            dm.dmFields = 0x400000
            r = ctypes.windll.user32.ChangeDisplaySettingsW(ctypes.byref(dm), 0)
            if r == 0: self._save_setting("refresh_rate", hz); return True
            return False
        except Exception:
            return False

    def change_brightness(self, value):
        try:
            if self._wmi_bright is None:
                self._wmi_bright = wmi.WMI(namespace="wmi")
            m = self._wmi_bright.WmiMonitorBrightnessMethods()[0]
            m.WmiSetBrightness(int(value), 0)
            self._save_setting("brightness", int(value))
            return True
        except Exception:
            return False

    def change_volume(self, value):
        self._save_setting("volume", int(value));
        return True

    def change_charge_limit(self, value):
        self._save_setting("charge_limit", int(value));
        return True

    def open_night_mode(self):
        try:
            os.startfile("ms-settings:easeofaccess-colorfilter"); return True
        except Exception:
            return False

    def toggle_dnd(self):
        try:
            os.startfile("ms-settings:quiethours"); return True
        except Exception:
            return False

    def open_github(self):
        webbrowser.open("https://github.com/YardimSever/DarkHeida/issues/new");
        return True

    def get_system_info(self):
        if self._sysinfo: return self._sysinfo
        info = {"cpu": "?", "ram": "?", "gpu": "?", "os": "?"}
        try:
            info["os"] = f"{platform.system()} {platform.release()}"
            info["ram"] = f"{round(psutil.virtual_memory().total / (1024 ** 3), 1)} GB"
            w = self._get_wmi()
            if w:
                try:
                    info["cpu"] = w.Win32_Processor()[0].Name.strip()
                except Exception:
                    info["cpu"] = platform.processor() or "?"
                try:
                    g = [x.Name for x in w.Win32_VideoController() if x.Name]
                    info["gpu"] = " / ".join(g) if g else "?"
                except Exception:
                    pass
        except Exception:
            pass
        self._sysinfo = info
        return info

    def save_new_theme(self, name, data):
        with self._lock:
            self.config.setdefault("custom_themes", {})[name] = data
            try:
                self._write_config(); return True
            except Exception:
                return False

    def delete_custom_theme(self, name):
        with self._lock:
            if name in self.config.get("custom_themes", {}):
                del self.config["custom_themes"][name]
                try:
                    self._write_config(); return True
                except Exception:
                    return False
        return False

    def get_custom_themes(self):
        return self.config.get("custom_themes", {})

    def get_battery_warning(self):
        return 0 < self._stats.get("battery", 0) < 20 and not self._stats.get("battery_plugged", False)

    def get_startup_status(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, "DarkHeida")
                winreg.CloseKey(key)
                return True
            except FileNotFoundError:
                winreg.CloseKey(key)
                return False
        except Exception:
            return False

    def toggle_startup(self, enable):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            if enable:
                script_path = os.path.abspath(__file__)
                pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                if not os.path.exists(pythonw):
                    pythonw = sys.executable
                cmd = f'"{pythonw}" "{script_path}"'
                winreg.SetValueEx(key, "DarkHeida", 0, winreg.REG_SZ, cmd)
                self._save_setting("startup", True)
            else:
                try:
                    winreg.DeleteValue(key, "DarkHeida")
                except FileNotFoundError:
                    pass
                self._save_setting("startup", False)
            winreg.CloseKey(key)
            return True
        except Exception as e:
            print("[startup]", e)
            return False

    def stop(self):
        self._running = False


HTML = r"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<title>DarkHeida</title>
<style>
:root{--bg:#050505;--sidebar:#0A0A0A;--card:#111111;--text:#FFFFFF;--muted:#888888;--accent:#FF0033;--border:#222222;}
*{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Arial,sans-serif;}
body{background:var(--bg);color:var(--text);display:flex;height:100vh;overflow:hidden;transition:background .3s;}

/* KARTLAR */
.card{position:relative;overflow:hidden;transition:all .3s;background:linear-gradient(135deg,var(--card),var(--border));border-radius:12px;padding:18px;border:1px solid var(--border);}
.card::before{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,var(--accent) 0%,transparent 70%);opacity:.05;pointer-events:none;}
.card:hover{transform:translateY(-3px);box-shadow:0 8px 30px rgba(0,0,0,.8);}
.bar-fill{position:relative;overflow:hidden;}
.bar-fill::after{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.6),transparent);animation:shimmer 2s infinite;}
@keyframes shimmer{0%{left:-100%}100%{left:100%}}

/* SIDEBAR */
.menu-btn{position:relative;overflow:hidden;background:linear-gradient(135deg,var(--card),var(--border));color:var(--text);border:1px solid var(--border);text-align:left;padding:12px 15px;border-radius:10px;cursor:pointer;font-size:13px;margin-bottom:8px;transition:all .2s;}
.menu-btn::before{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.1),transparent);transition:left .5s;}
.menu-btn:hover::before{left:100%;}
.menu-btn:hover{transform:translateX(3px);box-shadow:0 4px 12px rgba(0,0,0,.5);}
.menu-btn.active{background:linear-gradient(135deg,var(--accent),#CC002A);color:#fff;border-color:var(--accent);box-shadow:0 4px 15px rgba(255,0,51,.4);}

/* BUTONLAR */
.mode-btn{position:relative;overflow:hidden;box-shadow:0 6px 20px rgba(0,0,0,.5);padding:22px;border:none;border-radius:14px;font-size:15px;font-weight:bold;color:#fff;cursor:pointer;transition:all .3s;}
.mode-btn::before{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.3),transparent);transition:left .6s;}
.mode-btn:hover::before{left:100%;}
.mode-btn:hover{transform:translateY(-5px);box-shadow:0 15px 35px rgba(0,0,0,.9);}
.mode-btn:active{transform:scale(.95);}
.mode-btn.selected{outline:3px solid #fff;}
.quick-btn:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.5);background:var(--border);}
.quick-btn:active{transform:scale(.97);}
.hz-btn{transition:all .2s;box-shadow:0 4px 15px rgba(0,0,0,.4);}
.hz-btn:hover{transform:translateY(-2px);box-shadow:0 8px 20px rgba(0,0,0,.6);}
.hz-btn:active{transform:scale(.97);}
#github-btn{transition:all .2s;box-shadow:0 6px 20px rgba(255,0,51,.4);}
#github-btn:hover{transform:translateY(-3px);box-shadow:0 12px 30px rgba(255,0,51,.6);}
.theme-creator-btn,.delete-theme-btn{transition:all .2s;}
.theme-creator-btn:hover{transform:translateY(-2px);box-shadow:0 8px 25px rgba(156,39,176,.6);}
.delete-theme-btn:hover{transform:translateY(-2px);box-shadow:0 8px 25px rgba(244,67,54,.6);}
.modal-box{animation:zoomIn .3s ease;}
@keyframes zoomIn{from{transform:scale(.85);opacity:0}to{transform:scale(1);opacity:1}}
.notification{animation:slideIn .3s ease;}
@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}
.support-box:hover{transform:translateY(-3px);box-shadow:0 8px 30px rgba(0,0,0,.8);}

/* SLIDERLAR */
.slider-bg{overflow:hidden;position:absolute;top:0;left:0;height:100%;border-radius:5px;z-index:1;}
.slider-bg::after{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.6),transparent);animation:shimmer 2s infinite;pointer-events:none;}
.slider-wrapper input[type=range]::-webkit-slider-thumb{box-shadow:0 0 15px white;transition:transform .15s;-webkit-appearance:none;width:20px;height:20px;border-radius:50%;background:#fff;border:2px solid var(--accent);}
.slider-wrapper input[type=range]::-webkit-slider-thumb:hover{transform:scale(1.2);}

/* İMZA */
.signature{display:flex;flex-direction:column;align-items:center;gap:2px;cursor:pointer;padding:10px;border-radius:8px;transition:all .3s;border:1px solid transparent;margin-top:auto;position:relative;overflow:hidden;}
.signature::before{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,0,51,.5),transparent);animation:signatureShine 2s linear infinite;z-index:0;pointer-events:none;}
.signature:hover::before{animation:signatureShine 0.4s linear infinite;}
@keyframes signatureShine{0%{left:-100%}100%{left:100%}}
.signature-text,.signature-hint{position:relative;z-index:1;}
.signature-text{font-size:12px;font-family:Consolas,monospace;color:var(--muted);transition:all .3s;}
.signature-hint{display:flex;align-items:center;gap:4px;font-size:9px;color:var(--accent);animation:hintPulse 1.5s ease-in-out infinite;}
.hint-text{font-family:'Segoe UI',sans-serif;letter-spacing:.5px;font-weight:bold;}
.hint-arrow{font-size:8px;animation:hintBounce 1s ease-in-out infinite;}
@keyframes hintPulse{0%,100%{opacity:.4}50%{opacity:1}}
@keyframes hintBounce{0%,100%{transform:translateY(0)}50%{transform:translateY(2px)}}
.signature:hover{background:var(--card);border-color:var(--accent);}
.signature:hover .signature-text{color:var(--accent);}

/* LAYOUT */
.sidebar{width:220px;background:var(--sidebar);border-right:1px solid var(--border);padding:20px 15px;display:flex;flex-direction:column;}
.logo{color:var(--accent);font-size:18px;font-weight:bold;margin-bottom:30px;}
.content{flex:1;padding:30px;overflow-y:auto;}
.page{display:none;}
.page.active{display:block;}
h1{color:var(--accent);font-size:22px;margin-bottom:20px;}
.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin-bottom:20px;}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;position:relative;z-index:1;}
.card-title{font-size:13px;font-weight:bold;}
.card-value{font-size:16px;font-weight:bold;}
.bar-bg{background:var(--border);height:10px;border-radius:5px;overflow:hidden;position:relative;z-index:1;}
.bar-fill{height:100%;border-radius:5px;transition:width .4s ease;}
.cpu-color{color:#FF0033;} .cpu-bar{background:linear-gradient(90deg,#FF0033,#FF9800);}
.ram-color{color:#0088FF;} .ram-bar{background:linear-gradient(90deg,#0088FF,#00E5FF);}
.disk-color{color:#9C27B0;} .disk-bar{background:linear-gradient(90deg,#9C27B0,#E91E63);}
.net-color{color:#00E5FF;} .net-bar{background:linear-gradient(90deg,#00E5FF,#00FF88);}
.uptime-text{color:var(--muted);font-size:13px;margin-bottom:20px;}
.btn-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin-bottom:30px;}
.btn-windows{background:linear-gradient(135deg,#9E9E9E,#424242);}
.btn-silent{background:linear-gradient(135deg,#4CAF50,#1B5E20);}
.btn-performance{background:linear-gradient(135deg,#FF9800,#BF360C);}
.btn-turbo{background:linear-gradient(135deg,#F44336,#7F0000);}
.fan-section,.smart-mode-section,.support-box{background:linear-gradient(135deg,var(--card),var(--border));border-radius:12px;padding:20px;border:1px solid var(--border);margin-bottom:20px;}
.fan-title,.smart-mode-title{color:var(--text);font-size:14px;font-weight:bold;margin-bottom:10px;}
.fan-placeholder,.smart-mode-desc{color:var(--muted);font-size:12px;font-style:italic;}
.smart-mode-section{display:flex;justify-content:space-between;align-items:center;}
.smart-mode-info{flex:1;}
.toggle-switch{position:relative;width:50px;height:26px;background:var(--border);border-radius:13px;cursor:pointer;transition:background .3s;flex-shrink:0;}
.toggle-switch.active{background:var(--accent);}
.toggle-switch::after{content:'';position:absolute;top:3px;left:3px;width:20px;height:20px;background:#fff;border-radius:50%;transition:transform .3s;}
.toggle-switch.active::after{transform:translateX(24px);}
.quick-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin-bottom:30px;}
.quick-btn{padding:15px;border:1px solid var(--border);border-radius:10px;background:var(--card);color:var(--text);font-size:13px;cursor:pointer;transition:all .2s;}
.quick-btn.active{background:linear-gradient(135deg,var(--accent),#CC002A);color:#fff;border-color:var(--accent);}
.slider-container{margin-bottom:25px;}
.slider-label{color:var(--text);font-size:14px;font-weight:bold;margin-bottom:10px;display:flex;justify-content:space-between;}
.slider-value{color:var(--accent);font-weight:bold;}
.slider-wrapper{position:relative;width:100%;height:10px;border-radius:5px;background:var(--border);}
.slider-wrapper input[type=range]{width:100%;height:10px;outline:none;-webkit-appearance:none;position:absolute;top:0;left:0;background:transparent;z-index:3;cursor:pointer;}
#bright-bg{background:linear-gradient(90deg,#FF9800,#FFEB3B);}
#volume-bg{background:linear-gradient(90deg,#0088FF,#00E5FF);}
#charge-bg{background:linear-gradient(90deg,#4CAF50,#00FF88);}
#kb-bright-bg{background:linear-gradient(90deg,#9C27B0,#E91E63);}
.info-cards{display:grid;grid-template-columns:repeat(2,1fr);gap:15px;margin-bottom:20px;}
.info-card{background:linear-gradient(135deg,var(--card),var(--border));border-radius:12px;padding:18px;border:1px solid var(--border);}
.info-card-title{color:var(--muted);font-size:12px;margin-bottom:8px;}
.info-card-value{color:var(--accent);font-size:18px;font-weight:bold;}
.hz-btn-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:20px;}
.hz-btn{padding:12px;border:none;border-radius:10px;font-size:13px;font-weight:bold;color:#fff;cursor:pointer;}
.hz-btn-60{background:linear-gradient(135deg,#0088FF,#0055AA);}
.hz-btn-144{background:linear-gradient(135deg,#FF0033,#AA0022);}
select{background:var(--card);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:10px;font-size:13px;margin-bottom:20px;width:100%;}
.info-text{color:var(--muted);font-size:12px;line-height:1.6;}
.warning{color:#FF9800;font-size:12px;margin-bottom:15px;}
#github-btn{background:linear-gradient(135deg,var(--accent),#CC002A);color:#fff;font-weight:bold;border:none;padding:18px;font-size:14px;border-radius:12px;cursor:pointer;width:100%;margin-bottom:20px;}
.support-box{cursor:pointer;transition:all .3s;}
.support-box h2{color:var(--accent);font-size:16px;margin-bottom:15px;}
.support-box p{color:var(--text);font-size:13px;line-height:1.8;}
.support-box .highlight{color:var(--accent);font-weight:bold;}
.theme-creator-btn,.delete-theme-btn{color:#fff;border:none;padding:12px;border-radius:10px;font-size:13px;font-weight:bold;cursor:pointer;width:100%;margin-bottom:20px;}
.theme-creator-btn{background:linear-gradient(135deg,#9C27B0,#E91E63);}
.delete-theme-btn{background:linear-gradient(135deg,#F44336,#7F0000);}
.respect-warning{color:var(--muted);font-size:11px;font-style:italic;text-align:center;padding:15px;margin-top:20px;border-top:1px solid var(--border);opacity:.7;}
.notification{position:fixed;top:20px;right:20px;background:linear-gradient(135deg,#FF9800,#F57C00);color:#fff;padding:15px 25px;border-radius:10px;z-index:2000;font-size:13px;font-weight:bold;display:none;}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:3000;justify-content:center;align-items:center;padding:20px;}
.modal-overlay.show{display:flex;}
.modal-box{background:linear-gradient(135deg,var(--card),var(--border));border:2px solid var(--accent);border-radius:20px;padding:40px;max-width:700px;width:100%;max-height:80vh;overflow-y:auto;position:relative;}
.modal-close{position:absolute;top:15px;right:20px;background:transparent;border:none;color:var(--muted);font-size:24px;cursor:pointer;}
.modal-close:hover{color:var(--accent);}
.modal-box h2{color:var(--accent);font-size:24px;margin-bottom:25px;text-align:center;}
.modal-box p{color:var(--text);font-size:15px;line-height:2;margin-bottom:20px;text-align:justify;}
.modal-box .signature-line{text-align:center;margin-top:30px;padding-top:20px;border-top:1px solid var(--border);color:var(--muted);font-style:italic;}
#theme-creator .modal-box{max-width:500px;padding:30px;}
#theme-creator label{color:var(--text);font-size:12px;display:block;margin-bottom:5px;}
#theme-creator input[type=text]{width:100%;padding:10px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);}
#theme-creator input[type=color]{width:100%;height:40px;border:none;border-radius:8px;cursor:pointer;}
.color-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:20px 0;}
.btn-row{display:flex;gap:10px;}
.btn-row button{flex:1;padding:12px;color:#fff;border:none;border-radius:10px;font-weight:bold;cursor:pointer;}
.btn-save{background:linear-gradient(135deg,#4CAF50,#1B5E20);}
.btn-cancel{background:linear-gradient(135deg,#9E9E9E,#424242);}

/* GELİŞMİŞ KARTLAR */
.adv-card{background:linear-gradient(135deg,var(--card),var(--border));border:1px solid var(--border);border-radius:14px;margin-bottom:20px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.5);transition:all .3s;}
.adv-card:hover{transform:translateY(-3px);box-shadow:0 8px 30px rgba(0,0,0,.8);border-color:var(--accent);}
.adv-header{display:flex;align-items:center;gap:12px;padding:16px 20px;background:linear-gradient(90deg,rgba(255,0,51,.12),transparent);border-bottom:1px solid var(--border);}
.adv-icon{font-size:22px;filter:drop-shadow(0 0 8px var(--accent));}
.adv-title{color:var(--accent);font-size:14px;font-weight:900;letter-spacing:2px;flex:1;text-shadow:0 0 15px rgba(255,0,51,.5);}
.adv-badge{padding:4px 10px;border-radius:20px;background:rgba(255,152,0,.15);color:#FF9800;font-size:10px;font-weight:bold;letter-spacing:1px;border:1px solid rgba(255,152,0,.3);}
.adv-content{padding:18px 20px;}
.adv-row{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.04);}
.adv-row:last-child{border-bottom:none;}
.adv-label{color:var(--text);font-size:12px;font-weight:600;min-width:90px;letter-spacing:.5px;}
.adv-select{flex:1;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:8px 12px;font-size:12px;opacity:.55;cursor:not-allowed;}
.adv-slider-wrap{flex:1;position:relative;height:8px;background:var(--border);border-radius:4px;opacity:.55;}
.adv-slider-wrap input[type=range]{position:absolute;top:0;left:0;width:100%;height:8px;background:transparent;-webkit-appearance:none;appearance:none;cursor:not-allowed;margin:0;outline:none;}
.adv-slider-wrap input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:16px;height:16px;border-radius:50%;background:#fff;border:2px solid var(--accent);cursor:not-allowed;box-shadow:0 0 8px rgba(255,0,51,.5);}
.adv-btn-group{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:18px 20px !important;}
.adv-btn{padding:16px 12px;border:none;border-radius:12px;font-size:13px;font-weight:bold;color:#fff;cursor:pointer;transition:all .3s;box-shadow:0 4px 15px rgba(0,0,0,.4);}
.adv-btn:not(:disabled):hover{transform:translateY(-3px);box-shadow:0 8px 25px rgba(0,0,0,.7);}
.adv-btn:not(:disabled):active{transform:scale(.96);}
.adv-btn:disabled{opacity:.5;cursor:not-allowed;}
.adv-btn-red{background:linear-gradient(135deg,#FF0033,#7F0000);}
.adv-btn-green{background:linear-gradient(135deg,#4CAF50,#1B5E20);}
.adv-btn-orange{background:linear-gradient(135deg,#FF9800,#BF360C);}
.adv-btn-blue{background:linear-gradient(135deg,#0088FF,#0055AA);}
.adv-check-row{justify-content:flex-start;}
.adv-check-label{display:flex;align-items:center;gap:10px;cursor:not-allowed;opacity:.6;color:var(--text);font-size:13px;}
.adv-check{width:18px;height:18px;accent-color:var(--accent);cursor:not-allowed;}
.adv-footer{padding:12px 20px;background:rgba(255,152,0,.06);border-top:1px solid rgba(255,152,0,.15);color:#FF9800;font-size:11px;font-style:italic;text-align:center;letter-spacing:.5px;}

/* GELİŞTİRİCİ ALANI */
.dev-info-card{display:flex;gap:20px;align-items:flex-start;background:linear-gradient(135deg,rgba(255,0,51,.08),rgba(255,0,51,.02));border:1px solid rgba(255,0,51,.2);border-radius:14px;padding:25px;margin-bottom:20px;}
.dev-info-icon{font-size:48px;flex-shrink:0;filter:drop-shadow(0 0 15px var(--accent));}
.dev-info-content{flex:1;}
.dev-info-content h3{color:var(--accent);font-size:16px;font-weight:bold;margin-bottom:10px;letter-spacing:1px;}
.dev-info-content p{color:var(--text);font-size:13px;line-height:1.8;opacity:.85;}
.dev-info-content b{color:var(--accent);}
.dev-empty-card{background:linear-gradient(135deg,var(--card),var(--border));border:1px dashed var(--border);border-radius:14px;margin-bottom:15px;overflow:hidden;transition:all .3s;opacity:.7;}
.dev-empty-card:hover{opacity:1;border-color:var(--accent);border-style:solid;}
.dev-empty-header{display:flex;align-items:center;gap:12px;padding:14px 20px;background:linear-gradient(90deg,rgba(255,255,255,.03),transparent);border-bottom:1px dashed var(--border);}
.dev-empty-icon{font-size:20px;opacity:.5;}
.dev-empty-title{color:var(--muted);font-size:13px;font-weight:700;letter-spacing:1.5px;flex:1;}
.dev-empty-content{padding:18px 20px;}
.dev-empty-content p{color:var(--muted);font-size:12px;font-style:italic;line-height:1.6;margin:0;}
/* ===== İMZA KORUMA UYARISI ===== */
.sig-warning-overlay{
    position:fixed;top:0;left:0;width:100%;height:100%;
    background:rgba(0,0,0,0.95);z-index:99999;
    display:flex;justify-content:center;align-items:center;
    animation:sigFadeIn .3s ease;
}
@keyframes sigFadeIn{from{opacity:0}to{opacity:1}}
.sig-warning-box{
    max-width:520px;padding:50px;text-align:center;
    background:linear-gradient(135deg,#0A0000,#1A0000);
    border:2px solid #FF0033;border-radius:20px;
    box-shadow:0 20px 60px rgba(255,0,51,.6);
    animation:sigPulse 2s infinite;
}
@keyframes sigPulse{
    0%,100%{box-shadow:0 20px 60px rgba(255,0,51,.3)}
    50%{box-shadow:0 20px 60px rgba(255,0,51,.8)}
}
.sig-warning-icon{
    font-size:80px;line-height:1;margin-bottom:10px;
    animation:sigShake 1s infinite;
}
@keyframes sigShake{
    0%,100%{transform:rotate(0)}
    25%{transform:rotate(-10deg)}
    75%{transform:rotate(10deg)}
}
.sig-warning-title{
    color:#FF0033;font-size:32px;font-weight:900;
    margin:20px 0;letter-spacing:2px;
    text-shadow:0 0 30px #FF0033;
}
.sig-warning-text{
    color:#fff;font-size:15px;line-height:1.9;
    margin:20px 0;
}
.sig-warning-text strong{color:#FF0033;}
.sig-warning-quote{
    color:#888;font-size:13px;font-style:italic;
    margin:25px 0;padding:15px;
    border-top:1px solid #333;border-bottom:1px solid #333;
}
.sig-warning-sig{
    color:#FF0033;font-size:14px;font-weight:bold;
    margin-top:20px;letter-spacing:1px;
}
.sig-warning-btn{
    margin-top:30px;padding:15px 45px;
    background:linear-gradient(135deg,#FF0033,#CC002A);
    color:white;border:none;border-radius:10px;
    font-size:16px;font-weight:bold;cursor:pointer;
    transition:all .3s;box-shadow:0 6px 20px rgba(255,0,51,.5);
}
.sig-warning-btn:hover{
    transform:translateY(-3px) scale(1.05);
    box-shadow:0 12px 30px rgba(255,0,51,.8);
}
.sig-warning-btn:active{transform:scale(.95);}
</style>
</head>
<body>

<div class="notification" id="notification"></div>

<div class="sidebar">
    <div class="logo" id="app-title">DarkHeida</div>
    <button class="menu-btn active" onclick="showPage('perf',this)" id="menu-perf">⚡ Performans</button>
    <button class="menu-btn" onclick="showPage('screen',this)" id="menu-screen">🖥️ Ekran</button>
    <button class="menu-btn" onclick="showPage('battery',this)" id="menu-battery">🔋 Pil</button>
    <button class="menu-btn" onclick="showPage('advanced',this)" id="menu-advanced">🛠️ Gelişmiş</button>
    <button class="menu-btn" onclick="showPage('keyboard',this)" id="menu-keyboard">⌨️ Klavye</button>
    <button class="menu-btn" onclick="showPage('settings',this)" id="menu-settings">⚙️ Ayarlar</button>
    <button class="menu-btn" onclick="showPage('support',this)" id="menu-support">🆘 Destek</button>
    <div class="signature" onclick="openManifesto()" id="signature">
        <div class="signature-text" id="signature-text">⚡ YardımSever & DH</div>
        <div class="signature-hint">
            <span class="hint-text" id="sig-hint">tıkla</span>
            <span class="hint-arrow">▼</span>
        </div>
    </div>
</div>

<div class="content">
    <div class="page active" id="page-perf">
        <h1 id="perf-title">⚡ PERFORMANS MODU</h1>
        <div class="stats-grid">
            <div class="card"><div class="card-header"><span class="card-title" id="cpu-label">🧠 CPU</span><span class="card-value cpu-color" id="cpu-value">%0</span></div><div class="bar-bg"><div class="bar-fill cpu-bar" id="cpu-bar" style="width:0"></div></div></div>
            <div class="card"><div class="card-header"><span class="card-title" id="ram-label">💾 RAM</span><span class="card-value ram-color" id="ram-value">%0</span></div><div class="bar-bg"><div class="bar-fill ram-bar" id="ram-bar" style="width:0"></div></div></div>
            <div class="card"><div class="card-header"><span class="card-title" id="disk-label">💿 Disk</span><span class="card-value disk-color" id="disk-value">%0</span></div><div class="bar-bg"><div class="bar-fill disk-bar" id="disk-bar" style="width:0"></div></div></div>
            <div class="card"><div class="card-header"><span class="card-title" id="net-label">🌐 Ağ</span><span class="card-value net-color" id="net-value">0 B</span></div><div class="bar-bg"><div class="bar-fill net-bar" id="net-bar" style="width:0"></div></div></div>
        </div>
        <div class="uptime-text"><span id="uptime-label">Çalışma Süresi</span>: <span id="uptime">...</span></div>
        <div class="btn-grid">
            <button class="mode-btn btn-windows" id="mode-windows" onclick="setPowerMode('windows')">Windows</button>
            <button class="mode-btn btn-silent" id="mode-silent" onclick="setPowerMode('silent')">Sessiz</button>
            <button class="mode-btn btn-performance" id="mode-performance" onclick="setPowerMode('performance')">Performans</button>
            <button class="mode-btn btn-turbo" id="mode-turbo" onclick="setPowerMode('turbo')">Turbo</button>
        </div>
        <div class="smart-mode-section">
            <div class="smart-mode-info">
                <div class="smart-mode-title" id="smart-mode-title">🤖 AKILLI MOD</div> 
                <div class="smart-mode-desc" id="smart-mode-desc">Bilgisayarınızı otomatik yönetir.</div>
            </div>
            <div class="toggle-switch" id="smart-toggle" onclick="toggleSmartMode()"></div>
        </div>
        <div class="fan-section">
            <div class="fan-title" id="fan-title">🌡️ FAN HIZI VE SICAKLIK</div>
            <div class="fan-placeholder" id="fan-placeholder">Bu özellik geliştiriciler tarafından eklenecektir.</div>
        </div>
        <h1 style="font-size:16px;" id="quick-title">⚡ HIZLI ERİŞİM</h1>
        <div class="quick-grid">
            <button class="quick-btn" id="btn-night" onclick="openNightMode()">🌙 Gece Modu</button>
            <button class="quick-btn" id="btn-dnd" onclick="toggleDND()">🔕 Rahatsız Etme</button>
            <button class="quick-btn" id="btn-save" onclick="togglePowerSave()">🔋 Pil Tasarrufu</button>
        </div>
        <div class="slider-container">
            <div class="slider-label"><span id="volume-label">🔊 Ses Seviyesi</span><span class="slider-value" id="volume-value">%50</span></div>
            <div class="slider-wrapper">
                <div class="slider-bg" id="volume-bg" style="width:50%"></div>
                <input type="range" min="0" max="100" value="50" id="volume-slider" oninput="updateSlider(this)">
            </div>
        </div>
        <div class="respect-warning" id="respect-warning">⚠️ Saygı Duy, Saygı Gör!<br>Emeğe saygısızlık programın çalışmasını durduru.</div>
    </div>

    <div class="page" id="page-screen">
        <h1 id="screen-title">🖥️ EKRAN AYARLARI</h1>
        <div class="info-cards">
            <div class="info-card"><div class="info-card-title" id="res-label">📐 Çözünürlük</div><div class="info-card-value" id="resolution-value">-- x --</div></div>
            <div class="info-card"><div class="info-card-title" id="hz-title">⚡ Mevcut Hz</div><div class="info-card-value" id="hz-value">-- Hz</div></div>
        </div>
        <div class="slider-label" id="hz-label">Yenileme Hızı</div>
        <div class="hz-btn-grid">
            <button class="hz-btn hz-btn-60" onclick="setRefreshRate('60')">60Hz</button>
            <button class="hz-btn hz-btn-144" onclick="setRefreshRate('144')">144Hz</button>
        </div>
        <div class="slider-container">
            <div class="slider-label"><span id="bright-label">☀️ Parlaklık</span><span class="slider-value" id="bright-value">%80</span></div>
            <div class="slider-wrapper">
                <div class="slider-bg" id="bright-bg" style="width:80%"></div>
                <input type="range" min="0" max="100" value="80" id="bright-slider" oninput="updateSlider(this)">
            </div>
        </div>

        <div class="adv-card">
            <div class="adv-header">
                <span class="adv-icon">🖥️</span>
                <span class="adv-title" id="adv-gpu-title">GPU MODU</span>
                <span class="adv-badge">⏳ YAKINDA</span>
            </div>
            <div class="adv-content adv-btn-group">
                <button class="adv-btn adv-btn-green" disabled>🔋 iGPU</button>
                <button class="adv-btn adv-btn-orange" disabled>⚖️ Dengeli</button>
                <button class="adv-btn adv-btn-red" disabled>⚡ dGPU</button>
            </div>
            <div class="adv-footer">⏳ Geliştiriciler tarafından eklenecektir</div>
        </div>

        <div class="adv-card">
            <div class="adv-header">
                <span class="adv-icon">🔋</span>
                <span class="adv-title" id="adv-power-title">GÜÇ YÖNETİMİ</span>
                <span class="adv-badge">⏳ YAKINDA</span>
            </div>
            <div class="adv-content">
                <div class="adv-row"><span class="adv-label">CPU Limiti</span><div class="adv-slider-wrap"><input type="range" min="5" max="100" value="100" disabled></div></div>
                <div class="adv-row"><span class="adv-label">GPU Limiti</span><div class="adv-slider-wrap"><input type="range" min="5" max="100" value="100" disabled></div></div>
                <div class="adv-row"><span class="adv-label">Thermal</span><select class="adv-select" disabled><option>Otomatik</option><option>Agresif</option><option>Sessiz</option></select></div>
                <div class="adv-row"><span class="adv-label">Fan Eğrisi</span><select class="adv-select" disabled><option>Varsayılan</option><option>Performans</option><option>Sessiz</option><option>Turbo</option></select></div>
            </div>
            <div class="adv-footer">⏳ Geliştiriciler tarafından eklenecektir</div>
        </div>
    </div>

    <div class="page" id="page-battery">
        <h1 id="battery-title">🔋 PİL SAĞLIĞI</h1>
        <div class="info-cards">
            <div class="info-card"><div class="info-card-title" id="batt-pct-label">🔋 Pil Yüzdesi</div><div class="info-card-value" id="battery-percent">-- %</div></div>
            <div class="info-card"><div class="info-card-title" id="batt-status-label">⚡ Durum</div><div class="info-card-value" id="battery-status">--</div></div>
        </div>
        <div class="slider-container">
            <div class="slider-label"><span id="charge-label">🔌 Şarj Limiti</span><span class="slider-value" id="charge-value">%60</span></div>
            <div class="slider-wrapper">
                <div class="slider-bg" id="charge-bg" style="width:20%"></div>
                <input type="range" min="50" max="100" value="60" id="charge-slider" oninput="updateSlider(this)">
            </div>
        </div>
        <div class="info-text" id="battery-health">Pil Sağlığı: ⏳ Geliştiriciler ekleyecek</div>
    </div>

    <div class="page" id="page-keyboard">
        <h1 id="keyboard-title">⌨️ KLAVYE</h1>
        <div class="warning" id="kb-warning">⚠️ Bu özellik henüz geliştirme aşamasındadır.</div>
        <div class="slider-label" id="effect-label">✨ Efekt</div>
        <select id="effect-select">
            <option>Gökkuşağı</option><option>Nefes</option><option>Statik</option><option>Kapat</option>
        </select>
        <div class="slider-container">
            <div class="slider-label"><span id="kb-bright-label">💡 Klavye Parlaklığı</span><span class="slider-value" id="kb-bright-value">%80</span></div>
            <div class="slider-wrapper">
                <div class="slider-bg" id="kb-bright-bg" style="width:80%"></div>
                <input type="range" min="0" max="100" value="80" id="kb-bright-slider" oninput="updateSlider(this)">
            </div>
        </div>

        <div class="adv-card">
            <div class="adv-header">
                <span class="adv-icon">🌙</span>
                <span class="adv-title" id="adv-standby-title">MODERN STANDBY</span>
                <span class="adv-badge">⏳ YAKINDA</span>
            </div>
            <div class="adv-content">
                <div class="adv-row adv-check-row"><label class="adv-check-label"><input type="checkbox" class="adv-check" disabled><span class="adv-check-text" id="standby-1">Modern Standby aktif</span></label></div>
                <div class="adv-row adv-check-row"><label class="adv-check-label"><input type="checkbox" class="adv-check" disabled><span class="adv-check-text" id="standby-2">Kapak kapatınca uyku</span></label></div>
                <div class="adv-row adv-check-row"><label class="adv-check-label"><input type="checkbox" class="adv-check" disabled><span class="adv-check-text" id="standby-3">Şarjda PCIe kontrolü</span></label></div>
                <div class="adv-row adv-check-row"><label class="adv-check-label"><input type="checkbox" class="adv-check" disabled><span class="adv-check-text" id="standby-4">Modern Standby sırasında ağı kapat</span></label></div>
            </div>
            <div class="adv-footer">⏳ Geliştiriciler tarafından eklenecektir</div>
        </div>

        <div class="adv-card">
            <div class="adv-header">
                <span class="adv-icon">🎮</span>
                <span class="adv-title" id="adv-keys-title">TUŞ ATAMALARI</span>
                <span class="adv-badge">⏳ YAKINDA</span>
            </div>
            <div class="adv-content">
                <div class="adv-row"><span class="adv-label">M1</span><select class="adv-select" disabled><option>Sesi Azalt</option><option>Sesi Artır</option><option>Mikrofonu Sustur</option><option>Performans Modu</option><option>Görsel Mod</option><option>Özel</option></select></div>
                <div class="adv-row"><span class="adv-label">M2</span><select class="adv-select" disabled><option>Sesi Artır</option></select></div>
                <div class="adv-row"><span class="adv-label">M3</span><select class="adv-select" disabled><option>Mikrofonu Sustur</option></select></div>
                <div class="adv-row"><span class="adv-label">M4</span><select class="adv-select" disabled><option>Performans Modu</option></select></div>
                <div class="adv-row"><span class="adv-label">M5/ROG</span><select class="adv-select" disabled><option>Görsel Mod</option></select></div>
                <div class="adv-row"><span class="adv-label">FN+F4</span><select class="adv-select" disabled><option>Özel</option></select></div>
                <div class="adv-row"><span class="adv-label">FN+C</span><select class="adv-select" disabled><option>Özel</option></select></div>
                <div class="adv-row"><span class="adv-label">FN+V</span><select class="adv-select" disabled><option>Özel</option></select></div>
            </div>
            <div class="adv-footer">⏳ Geliştiriciler tarafından eklenecektir</div>
        </div>
    </div>

    <div class="page" id="page-advanced">
        <h1 id="advanced-title">🛠️ GELİŞMİŞ AYARLAR</h1>
        <div class="warning" id="adv-warning">💡 Bu alan geliştiriciler içindir. Kendi özelliklerinizi buraya ekleyebilirsiniz.</div>

        <div class="dev-info-card">
            <div class="dev-info-icon">📖</div>
            <div class="dev-info-content">
                <h3 id="dev-info-title">Geliştiriciler İçin Not</h3>
                <p id="dev-info-text">
                    Bu alan, <b>DarkHeida</b>'yı geliştiren kişilerin kendi özelliklerini ekleyebilmesi için tasarlanmıştır.
                    Boş kartları doldurun, yeni özellikler ekleyin, deneyin.
                </p>
            </div>
        </div>

        <div class="dev-empty-card">
            <div class="dev-empty-header">
                <span class="dev-empty-icon">🎯</span>
                <span class="dev-empty-title" id="dev-empty-title-1">BOŞ ALAN - 1</span>
                <span class="adv-badge">⏳ YAKINDA</span>
            </div>
            <div class="dev-empty-content">
                <p id="dev-empty-desc-1">Bu kart boş bırakılmıştır. Geliştiriciler tarafından doldurulacaktır.</p>
            </div>
        </div>

        <div class="dev-empty-card">
            <div class="dev-empty-header">
                <span class="dev-empty-icon">🎯</span>
                <span class="dev-empty-title" id="dev-empty-title-2">BOŞ ALAN - 2</span>
                <span class="adv-badge">⏳ YAKINDA</span>
            </div>
            <div class="dev-empty-content">
                <p id="dev-empty-desc-2">Bu kart boş bırakılmıştır. Geliştiriciler tarafından doldurulacaktır.</p>
            </div>
        </div>

        <div class="dev-empty-card">
            <div class="dev-empty-header">
                <span class="dev-empty-icon">🎯</span>
                <span class="dev-empty-title" id="dev-empty-title-3">BOŞ ALAN - 3</span>
                <span class="adv-badge">⏳ YAKINDA</span>
            </div>
            <div class="dev-empty-content">
                <p id="dev-empty-desc-3">Bu kart boş bırakılmıştır. Geliştiriciler tarafından doldurulacaktır.</p>
            </div>
        </div>
    </div>

    <div class="page" id="page-settings">
        <h1 id="settings-title">⚙️ AYARLAR</h1>
        <div class="slider-label" id="theme-label">🎨 Tema</div>
        <select id="theme-select" onchange="saveTheme(this.value)"></select>
        <button class="theme-creator-btn" onclick="openThemeCreator()" id="btn-create-theme">🎨 YENİ TEMA OLUŞTUR</button>
        <button class="delete-theme-btn" onclick="deleteSelectedTheme()" id="btn-del-theme">🗑️ SEÇİLİ TEMAYI SİL</button>
        <div class="smart-mode-section">
            <div class="smart-mode-info">
                <div class="smart-mode-title" id="startup-title">🔄 Başlangıçta Çalıştır</div>
                <div class="smart-mode-desc" id="startup-desc">Windows açıldığında DarkHeida otomatik başlasın</div>
            </div>
            <div class="toggle-switch" id="startup-toggle" onclick="toggleStartup()"></div>
        </div>
        <div class="slider-label" id="lang-label">🌐 Dil</div>
        <select id="lang-select" onchange="saveLanguage(this.value)"></select>
        <div class="slider-label" id="sys-label">💻 Sistem Bilgisi</div>
        <div class="info-text" id="sys-info">Yükleniyor...</div>
    </div>

    <div class="page" id="page-support">
        <h1 id="support-title">🆘 DESTEK</h1>
        <div class="support-box" onclick="openGithub()">
            <h2 id="support-box-title">🚀 GELİŞTİRİCİ DESTEĞİ</h2>
            <p id="support-box-text"></p>
        </div>
        <button id="github-btn" onclick="openGithub()">🐛 Hata Bildir (GitHub)</button>
        <div class="slider-label" id="dev-notes-label">📝 Geliştirici Notları</div>
        <div class="info-text" id="dev-notes-text"></div>
    </div>
</div>

<div class="modal-overlay" id="theme-creator">
    <div class="modal-box">
        <button class="modal-close" onclick="closeThemeCreator()">✕</button>
        <h2 id="tc-title">🎨 TEMA OLUŞTUR</h2>
        <label id="tc-name-lbl">Tema Adı:</label>
        <input type="text" id="new-theme-name" placeholder="Benim Temam">
        <div class="color-grid">
            <div><label id="tc-bg-lbl">Arka Plan:</label><input type="color" id="new-theme-bg" value="#050505"></div>
            <div><label id="tc-card-lbl">Kart:</label><input type="color" id="new-theme-card" value="#111111"></div>
            <div><label id="tc-text-lbl">Metin:</label><input type="color" id="new-theme-text" value="#FFFFFF"></div>
            <div><label id="tc-accent-lbl">Vurgu:</label><input type="color" id="new-theme-accent" value="#FF0033"></div>
            <div><label id="tc-border-lbl">Kenarlık:</label><input type="color" id="new-theme-border" value="#222222"></div>
        </div>
        <div class="btn-row">
            <button class="btn-save" onclick="saveNewTheme()" id="tc-save">💾 Kaydet</button>
            <button class="btn-cancel" onclick="closeThemeCreator()" id="tc-close">❌ Kapat</button>
        </div>
    </div>
</div>

<div class="modal-overlay" id="manifesto">
    <div class="modal-box">
        <button class="modal-close" onclick="closeManifesto()">✕</button>
        <h2 id="manifesto-title">📜 MANİFESTO</h2>
        <p id="manifesto-text"></p>
        <div class="signature-line" id="manifesto-sig">⚡ YardımSever &amp; DH</div>
    </div>
</div>

<script>
const DEFAULT_THEMES={dark:{bg:"#050505",sidebar:"#0A0A0A",card:"#111111",text:"#FFFFFF",muted:"#888888",accent:"#FF0033",border:"#222222"},rog:{bg:"#000000",sidebar:"#0A0000",card:"#1A0000",text:"#FFFFFF",muted:"#CC8888",accent:"#FF0033",border:"#330000"},cyber:{bg:"#050A1A",sidebar:"#050A1A",card:"#0A1428",text:"#FFFFFF",muted:"#8899CC",accent:"#0088FF",border:"#1A2F50"},matrix:{bg:"#000000",sidebar:"#000000",card:"#001A00",text:"#FFFFFF",muted:"#88CC88",accent:"#00FF41",border:"#004A00"},light:{bg:"#F0F0F0",sidebar:"#E0E0E0",card:"#FFFFFF",text:"#000000",muted:"#555555",accent:"#2196F3",border:"#D0D0D0"},neon_purple:{bg:"#0A0014",sidebar:"#0A0014",card:"#1A0033",text:"#FFFFFF",muted:"#CC88FF",accent:"#9C27B0",border:"#330066"},golden:{bg:"#1A1200",sidebar:"#1A1200",card:"#2A1F00",text:"#FFFFFF",muted:"#CCAA66",accent:"#FFD700",border:"#4A3500"},ice_blue:{bg:"#001A2A",sidebar:"#001A2A",card:"#002A40",text:"#FFFFFF",muted:"#88CCFF",accent:"#00E5FF",border:"#004A66"},red_devil:{bg:"#1A0000",sidebar:"#1A0000",card:"#330000",text:"#FFFFFF",muted:"#CC6666",accent:"#FF0000",border:"#660000"},forest:{bg:"#001A00",sidebar:"#001A00",card:"#003300",text:"#FFFFFF",muted:"#66CC66",accent:"#00FF00",border:"#006600"}};

const THEME_LABELS={TR:{dark:"Tam Siyah",rog:"ROG Kırmızı",cyber:"Siber Mavi",matrix:"Matrix Yeşili",light:"Açık",neon_purple:"Neon Mor",golden:"Altın Sarısı",ice_blue:"Buz Mavisi",red_devil:"Kırmızı Şeytan",forest:"Orman Yeşili"},EN:{dark:"Pure Black",rog:"ROG Red",cyber:"Cyber Blue",matrix:"Matrix Green",light:"Light",neon_purple:"Neon Purple",golden:"Golden",ice_blue:"Ice Blue",red_devil:"Red Devil",forest:"Forest Green"},DE:{dark:"Reines Schwarz",rog:"ROG Rot",cyber:"Cyber Blau",matrix:"Matrix Grün",light:"Hell",neon_purple:"Neon Lila",golden:"Golden",ice_blue:"Eisblau",red_devil:"Roter Teufel",forest:"Waldgrün"},RU:{dark:"Чистый черный",rog:"ROG Красный",cyber:"Кибер Синий",matrix:"Матрица Зеленый",light:"Светлая",neon_purple:"Неоново-фиолетовый",golden:"Золотой",ice_blue:"Ледяно-синий",red_devil:"Красный дьявол",forest:"Лесной зеленый"},ZH:{dark:"纯黑",rog:"ROG 红",cyber:"赛博蓝",matrix:"矩阵绿",light:"浅色",neon_purple:"霓虹紫",golden:"金色",ice_blue:"冰蓝",red_devil:"红魔",forest:"森林绿"},JA:{dark:"純黒",rog:"ROG 赤",cyber:"サイバーブルー",matrix:"マトリックスグリーン",light:"ライト",neon_purple:"ネオンパープル",golden:"ゴールデン",ice_blue:"アイスブルー",red_devil:"レッドデビル",forest:"フォレストグリーン"},KO:{dark:"순수 검정",rog:"ROG 빨강",cyber:"사이버 블루",matrix:"매트릭스 그린",light:"밝은",neon_purple:"네온 퍼플",golden:"골든",ice_blue:"아이스 블루",red_devil:"레드 데빌",forest:"포레스트 그린"},IT:{dark:"Nero puro",rog:"Rosso ROG",cyber:"Blu cyber",matrix:"Verde Matrix",light:"Chiaro",neon_purple:"Viola neon",golden:"Dorato",ice_blue:"Blu ghiaccio",red_devil:"Diavolo rosso",forest:"Verde foresta"},PT:{dark:"Preto puro",rog:"Vermelho ROG",cyber:"Azul cyber",matrix:"Verde Matrix",light:"Claro",neon_purple:"Roxo neon",golden:"Dourado",ice_blue:"Azul gelo",red_devil:"Diabo vermelho",forest:"Verde floresta"},AR:{dark:"أسود نقي",rog:"أحمر ROG",cyber:"أزرق سيبراني",matrix:"أخضر ماتريكس",light:"فاتح",neon_purple:"أرجواني نيون",golden:"ذهبي",ice_blue:"أزرق ثلجي",red_devil:"الشيطان الأحمر",forest:"أخضر غابة"},HI:{dark:"शुद्ध काला",rog:"ROG लाल",cyber:"साइबर नीला",matrix:"मैट्रिक्स हरा",light:"हल्का",neon_purple:"नियॉन बैंगनी",golden:"सुनहरा",ice_blue:"बर्फीला नीला",red_devil:"लाल शैतान",forest:"वन हरा"},ID:{dark:"Hitam Murni",rog:"Merah ROG",cyber:"Biru Cyber",matrix:"Hijau Matrix",light:"Terang",neon_purple:"Ungu Neon",golden:"Emas",ice_blue:"Biru Es",red_devil:"Iblis Merah",forest:"Hijau Hutan"},NL:{dark:"Puur zwart",rog:"ROG rood",cyber:"Cyber blauw",matrix:"Matrix groen",light:"Licht",neon_purple:"Neon paars",golden:"Gouden",ice_blue:"Ijsblauw",red_devil:"Rode duivel",forest:"Bosgroen"},PL:{dark:"Czysta czerń",rog:"ROG czerwony",cyber:"Cyber niebieski",matrix:"Matrix zielony",light:"Jasny",neon_purple:"Neonowy fiolet",golden:"Złoty",ice_blue:"Lodowy niebieski",red_devil:"Czerwony diabeł",forest:"Leśna zieleń"},SV:{dark:"Ren svart",rog:"ROG röd",cyber:"Cyber blå",matrix:"Matrix grön",light:"Ljus",neon_purple:"Neon lila",golden:"Gyllene",ice_blue:"Isblå",red_devil:"Röd djävul",forest:"Skogsgrön"},NO:{dark:"Ren svart",rog:"ROG rød",cyber:"Cyber blå",matrix:"Matrix grønn",light:"Lys",neon_purple:"Neon lilla",golden:"Gylden",ice_blue:"Isblå",red_devil:"Rød djevel",forest:"Skoggrønn"},EL:{dark:"Καθαρό μαύρο",rog:"ROG κόκκινο",cyber:"Cyber μπλε",matrix:"Matrix πράσινο",light:"Φωτεινό",neon_purple:"Νέον μωβ",golden:"Χρυσό",ice_blue:"Παγωμένο μπλε",red_devil:"Κόκκινος διάβολος",forest:"Δάσος πράσινο"},HE:{dark:"שחור טהור",rog:"אדום ROG",cyber:"כחול סייבר",matrix:"ירוק מטריקס",light:"בהיר",neon_purple:"סגול ניאון",golden:"זהב",ice_blue:"כחול קרח",red_devil:"שטן אדום",forest:"ירוק יער"},TH:{dark:"ดำสนิท",rog:"ROG แดง",cyber:"ไซเบอร์บลู",matrix:"เมทริกซ์กรีน",light:"สว่าง",neon_purple:"นีออนม่วง",golden:"ทอง",ice_blue:"น้ำแข็งบลู",red_devil:"ปีศาจแดง",forest:"ป่าดำ"},VI:{dark:"Đen tuyền",rog:"Đỏ ROG",cyber:"Xanh cyber",matrix:"Xanh Matrix",light:"Sáng",neon_purple:"Tím neon",golden:"Vàng",ice_blue:"Xanh băng",red_devil:"Ác quỷ đỏ",forest:"Xanh rừng"}};

const DIL = {
TR:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Disk",net:"🌐 Ağ",app_title:"DarkHeida",menu_perf:"⚡ Performans",menu_screen:"🖥️ Ekran",menu_battery:"🔋 Pil",menu_keyboard:"⌨️ Klavye",menu_settings:"⚙️ Ayarlar",menu_support:"🆘 Destek",perf_mode:"⚡ PERFORMANS MODU",quick_access:"⚡ HIZLI ERİŞİM",btn_windows:"Windows",btn_silent:"Sessiz",btn_performance:"Performans",btn_turbo:"Turbo",night_mode:"🌙 Gece Modu",dnd:"🔕 Rahatsız Etme",power_save:"🔋 Pil Tasarrufu",screen_title:"🖥️ EKRAN AYARLARI",resolution:"📐 Çözünürlük",current_hz:"⚡ Mevcut Hz",refresh_rate:"Yenileme Hızı",brightness:"☀️ Parlaklık",battery_title:"🔋 PİL SAĞLIĞI",battery_percent:"🔋 Pil Yüzdesi",battery_status:"⚡ Durum",charge_limit:"🔌 Şarj Limiti",battery_health:"Pil Sağlığı: ⏳ Geliştiriciler ekleyecek",keyboard_title:"⌨️ KLAVYE",effect:"✨ Efekt",kb_warning:"⚠️ Bu özellik henüz geliştirme aşamasındadır.",kb_bright:"💡 Klavye Parlaklığı",settings_title:"⚙️ AYARLAR",startup_title:"🔄 Başlangıçta Çalıştır",startup_desc:"Windows açıldığında DarkHeida otomatik başlasın",theme:"🎨 Tema",create_theme:"🎨 YENİ TEMA OLUŞTUR",delete_theme:"🗑️ SEÇİLİ TEMAYI SİL",language:"🌐 Dil",sys_info:"💻 Sistem Bilgisi",support_title:"🆘 DESTEK",report_issue:"🐛 Hata Bildir (GitHub)",dev_notes:"📝 Geliştirici Notları",dev_notes_text:"Bu proje açık kaynaklıdır.<br><br>• Pil Sağlığı<br>• Fan Hızı ve Sıcaklık<br>• Klavye Işığı<br>• GPU Durdurma<br>• Ses Kontrolü (pycaw)<br>• Akıllı Mod",uptime:"Çalışma Süresi",volume:"🔊 Ses Seviyesi",fan_title:"🌡️ FAN HIZI VE SICAKLIK",fan_placeholder:"Bu özellik geliştiriciler tarafından eklenecektir.",support_box_title:"🚀 GELİŞTİRİCİ DESTEĞİ",support_box_text:"<span class='highlight'>DarkHeida</span> şu anda <span class='highlight'>geliştirme aşamasındadır.</span><br><br>Çalışmayan butonlar <span class='highlight'>yakında</span> kullanılabilir olacaktır.<br><br>🙏",smart_title:"🤖 AKILLI MOD",smart_desc:"Bilgisayarınızı otomatik yönetir.",respect:"⚠️ Saygı Duy, Saygı Gör!<br>Emeğe saygısızlık programın çalışmasını durdurur.",tc_title:"🎨 TEMA OLUŞTUR",tc_name:"Tema Adı:",tc_bg:"Arka Plan:",tc_card:"Kart:",tc_text:"Metin:",tc_accent:"Vurgu:",tc_border:"Kenarlık:",tc_save:"💾 Kaydet",tc_close:"❌ Kapat",manifesto_title:"📜 MANİFESTO",manifesto_text:"Bu programı yaratmamın sebebi piyasada bulunan programlara rakip olması için değil, daha modern kullanıcı dostu özelliklerle ve geliştirmeye açık bir hale getirmektir. Yani rekabet değil, daha modern erişilebilirlik sağlamak. Lütfen emeğe saygı gösterin. Program açık kaynak kodludur, dilediğiniz gibi geliştirebilirsiniz. Ama rica ediyorum imzamızı kaldırmayın lütfen :)). Piyasada bulunan birçok programın ağır çalışmasından bıktık. Sürekli güncelleme, yükleyici yorucu olduğu için ben de böyle bir proje düşündüm. Daha modern ve daha çok özelliklere sahip bir program olması için yardımınıza ihtiyacımız var. Şimdiden teşekkür ediyorum, umarım beğenirsiniz. Kullanımınızı kolaylaştıracak özellikler olursa Destek bölümünden geliştiricilere programın ismini etiketleyerek istediğiniz özellik için istekte bulunabilirsiniz :)",notif_saved:"✅ Tema kaydedildi",notif_deleted:"🗑️ Tema silindi",notif_mode:"⚡ Mod",notif_smart_on:"🤖 Akıllı Mod AÇIK",notif_smart_off:"🤖 Akıllı Mod KAPALI",notif_psave_on:"🔋 Pil Tasarrufu AÇIK",notif_psave_off:"⚡ Normal Mod",notif_dnd_on:"🔕 Rahatsız Etme AÇIK",notif_dnd_off:"🔔 Rahatsız Etme KAPALI",notif_hz:"⚡ Hz ayarlandı",notif_night:"🌙 Gece Modu",warn_theme_name:"Tema adı gerekli!",warn_default:"Varsayılan tema silinemez!",sig_hint:"tıkla",adv_menu:"🛠️ Gelişmiş",adv_title:"🛠️ GELİŞMİŞ AYARLAR",adv_warning:"💡 Bu alan geliştiriciler içindir.",dev_info_title:"Geliştiriciler İçin Not",dev_info_text:"Bu alan, <b>DarkHeida</b>'yı geliştiren kişilerin kendi özelliklerini ekleyebilmesi için tasarlanmıştır.",dev_empty_1:"BOŞ ALAN - 1",dev_empty_2:"BOŞ ALAN - 2",dev_empty_3:"BOŞ ALAN - 3",dev_empty_desc:"Bu kart boş bırakılmıştır.",adv_gpu:"GPU MODU",adv_power:"GÜÇ YÖNETİMİ",adv_standby:"MODERN STANDBY",adv_keys:"TUŞ ATAMALARI",adv_soon:"⏳ YAKINDA",adv_footer:"⏳ Geliştiriciler tarafından eklenecektir"},

EN:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Disk",net:"🌐 Network",app_title:"DarkHeida",menu_perf:"⚡ Performance",menu_screen:"🖥️ Display",menu_battery:"🔋 Battery",menu_keyboard:"⌨️ Keyboard",menu_settings:"⚙️ Settings",menu_support:"🆘 Support",perf_mode:"⚡ PERFORMANCE MODE",quick_access:"⚡ QUICK ACCESS",btn_windows:"Windows",btn_silent:"Silent",btn_performance:"Performance",btn_turbo:"Turbo",night_mode:"🌙 Night Mode",dnd:"🔕 Do Not Disturb",power_save:"🔋 Power Save",screen_title:"🖥️ DISPLAY SETTINGS",resolution:"📐 Resolution",current_hz:"⚡ Current Hz",refresh_rate:"Refresh Rate",brightness:"☀️ Brightness",battery_title:"🔋 BATTERY HEALTH",battery_percent:"🔋 Battery %",battery_status:"⚡ Status",charge_limit:"🔌 Charge Limit",battery_health:"Battery Health: ⏳ Coming soon",keyboard_title:"⌨️ KEYBOARD",effect:"✨ Effect",kb_warning:"⚠️ This feature is under development.",kb_bright:"💡 Keyboard Brightness",settings_title:"⚙️ SETTINGS",startup_title:"🔄 Run at Startup",startup_desc:"DarkHeida starts automatically when Windows boots",theme:"🎨 Theme",create_theme:"🎨 CREATE NEW THEME",delete_theme:"🗑️ DELETE SELECTED THEME",language:"🌐 Language",sys_info:"💻 System Info",support_title:"🆘 SUPPORT",report_issue:"🐛 Report Bug (GitHub)",dev_notes:"📝 Developer Notes",dev_notes_text:"This project is open source.<br><br>• Battery Health<br>• Fan Speed & Temperature<br>• Keyboard Light<br>• GPU Disable<br>• Volume Control (pycaw)<br>• Smart Mode",uptime:"Uptime",volume:"🔊 Volume",fan_title:"🌡️ FAN SPEED & TEMPERATURE",fan_placeholder:"This feature will be added by developers.",support_box_title:"🚀 DEVELOPER SUPPORT",support_box_text:"<span class='highlight'>DarkHeida</span> is currently <span class='highlight'>under development.</span><br><br>Non-working buttons will be available <span class='highlight'>soon.</span><br><br>🙏",smart_title:"🤖 SMART MODE",smart_desc:"Manages your PC automatically.",respect:"⚠️ Respect and Be Respected!<br>Disrespect stops the program.",tc_title:"🎨 CREATE THEME",tc_name:"Theme Name:",tc_bg:"Background:",tc_card:"Card:",tc_text:"Text:",tc_accent:"Accent:",tc_border:"Border:",tc_save:"💾 Save",tc_close:"❌ Close",manifesto_title:"📜 MANIFESTO",manifesto_text:"The reason I created this program is not to compete with the programs on the market, but to make it a whole project that is open to development with more modern, user-friendly features. In other words, not competition, but providing more modern accessibility. Please respect the effort. The program is open source, you can develop it as you wish. But I kindly ask you not to remove our signature :)). We got tired of the heavy operation of many programs on the market. Constant updates and installers were exhausting, so I thought of such a project. We need your help to make a more modern program with more features. I thank you in advance, I hope you like it. If there are features that will make your use easier, you can request the feature you want from the Support section by tagging the developers with the program name :))",notif_saved:"✅ Theme saved",notif_deleted:"🗑️ Theme deleted",notif_mode:"⚡ Mode",notif_smart_on:"🤖 Smart Mode ON",notif_smart_off:"🤖 Smart Mode OFF",notif_psave_on:"🔋 Power Save ON",notif_psave_off:"⚡ Normal Mode",notif_dnd_on:"🔕 DND ON",notif_dnd_off:"🔔 DND OFF",notif_hz:"⚡ Hz applied",notif_night:"🌙 Night Mode",warn_theme_name:"Theme name required!",warn_default:"Default themes cannot be deleted!",sig_hint:"click",adv_menu:"🛠️ Advanced",adv_title:"🛠️ ADVANCED SETTINGS",adv_warning:"💡 This area is for developers.",dev_info_title:"Note for Developers",dev_info_text:"This area is designed for <b>DarkHeida</b> developers.",dev_empty_1:"EMPTY AREA - 1",dev_empty_2:"EMPTY AREA - 2",dev_empty_3:"EMPTY AREA - 3",dev_empty_desc:"This card is left empty.",adv_gpu:"GPU MODE",adv_power:"POWER MANAGEMENT",adv_standby:"MODERN STANDBY",adv_keys:"KEY BINDINGS",adv_soon:"⏳ SOON",adv_footer:"⏳ Will be added by developers"},

DE:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Datenträger",net:"🌐 Netzwerk",app_title:"DarkHeida",menu_perf:"⚡ Leistung",menu_screen:"🖥️ Anzeige",menu_battery:"🔋 Akku",menu_keyboard:"⌨️ Tastatur",menu_settings:"⚙️ Einstellungen",menu_support:"🆘 Hilfe",perf_mode:"⚡ LEISTUNGSMODUS",quick_access:"⚡ SCHNELLZUGRIFF",btn_windows:"Windows",btn_silent:"Leise",btn_performance:"Leistung",btn_turbo:"Turbo",night_mode:"🌙 Nachtmodus",dnd:"🔕 Nicht stören",power_save:"🔋 Energiesparen",screen_title:"🖥️ ANZEIGE",resolution:"📐 Auflösung",current_hz:"⚡ Aktuelle Hz",refresh_rate:"Bildwiederholrate",brightness:"☀️ Helligkeit",battery_title:"🔋 AKKU-ZUSTAND",battery_percent:"🔋 Akku %",battery_status:"⚡ Status",charge_limit:"🔌 Ladegrenze",battery_health:"Akkuzustand: ⏳ Bald",keyboard_title:"⌨️ TASTATUR",effect:"✨ Effekt",kb_warning:"⚠️ Diese Funktion befindet sich in Entwicklung.",kb_bright:"💡 Tastaturhelligkeit",settings_title:"⚙️ EINSTELLUNGEN",startup_title:"🔄 Beim Start ausführen",startup_desc:"DarkHeida startet automatisch beim Windows-Start",theme:"🎨 Thema",create_theme:"🎨 NEUES THEMA",delete_theme:"🗑️ THEMA LÖSCHEN",language:"🌐 Sprache",sys_info:"💻 Systeminfo",support_title:"🆘 HILFE",report_issue:"🐛 Fehler melden (GitHub)",dev_notes:"📝 Entwicklernotizen",dev_notes_text:"Open Source.<br><br>• Akkuzustand<br>• Lüftergeschwindigkeit<br>• Tastaturbeleuchtung<br>• GPU deaktivieren<br>• Lautstärke<br>• Intelligenter Modus",uptime:"Laufzeit",volume:"🔊 Lautstärke",fan_title:"🌡️ LÜFTERDREHZAHL UND TEMPERATUR",fan_placeholder:"Diese Funktion wird von Entwicklern hinzugefügt.",support_box_title:"🚀 ENTWICKLER-UNTERSTÜTZUNG",support_box_text:"<span class='highlight'>DarkHeida</span> befindet sich derzeit <span class='highlight'>in Entwicklung.</span><br><br>Nicht funktionierende Schaltflächen werden <span class='highlight'>bald</span> verfügbar sein.<br><br>🙏",smart_title:"🤖 INTELLIGENTER MODUS",smart_desc:"Verwaltet Ihren Computer automatisch.",respect:"⚠️ Respektiere und werde respektiert!<br>Missachtung der Arbeit stoppt das Programm.",tc_title:"🎨 THEMA ERSTELLEN",tc_name:"Themenname:",tc_bg:"Hintergrund:",tc_card:"Karte:",tc_text:"Text:",tc_accent:"Akzent:",tc_border:"Rand:",tc_save:"💾 Speichern",tc_close:"❌ Schließen",manifesto_title:"📜 MANIFEST",manifesto_text:"Der Grund, warum ich dieses Programm erstellt habe, ist nicht, mit bestehenden Programmen auf dem Markt zu konkurrieren, sondern es zu einem modernen, benutzerfreundlichen und entwicklungsoffenen Projekt zu machen. Bitte respektieren Sie die Arbeit. Das Programm ist Open Source. Aber bitte entfernen Sie nicht unsere Signatur :))",notif_saved:"✅ Thema gespeichert",notif_deleted:"🗑️ Thema gelöscht",notif_mode:"⚡ Modus",notif_smart_on:"🤖 Intelligenter Modus AN",notif_smart_off:"🤖 Intelligenter Modus AUS",notif_psave_on:"🔋 Energiesparen AN",notif_psave_off:"⚡ Normal",notif_dnd_on:"🔕 Nicht stören AN",notif_dnd_off:"🔔 Nicht stören AUS",notif_hz:"⚡ Hz angewendet",notif_night:"🌙 Nachtmodus",warn_theme_name:"Themename erforderlich!",warn_default:"Standardthemen können nicht gelöscht werden!",sig_hint:"klicken",adv_menu:"🛠️ Erweitert",adv_title:"🛠️ ERWEITERTE EINSTELLUNGEN",adv_warning:"💡 Dieser Bereich ist für Entwickler.",dev_info_title:"Hinweis für Entwickler",dev_info_text:"Dieser Bereich ist für <b>DarkHeida</b>-Entwickler gedacht.",dev_empty_1:"LEERER BEREICH - 1",dev_empty_2:"LEERER BEREICH - 2",dev_empty_3:"LEERER BEREICH - 3",dev_empty_desc:"Diese Karte bleibt leer.",adv_gpu:"GPU-MODUS",adv_power:"ENERGIEVERWALTUNG",adv_standby:"MODERNER STANDBY",adv_keys:"TASTENBELEGUNGEN",adv_soon:"⏳ BALD",adv_footer:"⏳ Wird von Entwicklern hinzugefügt"},

RU:{cpu:"🧠 ЦП",ram:"💾 ОЗУ",disk:"💿 Диск",net:"🌐 Сеть",app_title:"DarkHeida",menu_perf:"⚡ Производительность",menu_screen:"🖥️ Экран",menu_battery:"🔋 Батарея",menu_keyboard:"⌨️ Клавиатура",menu_settings:"⚙️ Настройки",menu_support:"🆘 Поддержка",perf_mode:"⚡ РЕЖИМ ПРОИЗВОДИТЕЛЬНОСТИ",quick_access:"⚡ БЫСТРЫЙ ДОСТУП",btn_windows:"Windows",btn_silent:"Тихий",btn_performance:"Производительность",btn_turbo:"Турбо",night_mode:"🌙 Ночной режим",dnd:"🔕 Не беспокоить",power_save:"🔋 Энергосбережение",screen_title:"🖥️ ЭКРАН",resolution:"📐 Разрешение",current_hz:"⚡ Текущая Hz",refresh_rate:"Частота обновления",brightness:"☀️ Яркость",battery_title:"🔋 СОСТОЯНИЕ БАТАРЕИ",battery_percent:"🔋 Батарея %",battery_status:"⚡ Статус",charge_limit:"🔌 Предел заряда",battery_health:"Состояние: ⏳ Скоро",keyboard_title:"⌨️ КЛАВИАТУРА",effect:"✨ Эффект",kb_warning:"⚠️ Эта функция находится в разработке.",kb_bright:"💡 Яркость клавиатуры",settings_title:"⚙️ НАСТРОЙКИ",startup_title:"🔄 Запуск при старте",startup_desc:"DarkHeida запускается автоматически при загрузке Windows",theme:"🎨 Тема",create_theme:"🎨 СОЗДАТЬ ТЕМУ",delete_theme:"🗑️ УДАЛИТЬ ТЕМУ",language:"🌐 Язык",sys_info:"💻 Система",support_title:"🆘 ПОДДЕРЖКА",report_issue:"🐛 Сообщить об ошибке (GitHub)",dev_notes:"📝 Заметки разработчика",dev_notes_text:"Открытый код.<br><br>• Состояние батареи<br>• Скорость вентилятора<br>• Подсветка клавиатуры<br>• Отключение GPU<br>• Громкость<br>• Умный режим",uptime:"Время работы",volume:"🔊 Громкость",fan_title:"🌡️ СКОРОСТЬ ВЕНТИЛЯТОРА",fan_placeholder:"Будет добавлено разработчиками.",support_box_title:"🚀 ПОДДЕРЖКА РАЗРАБОТЧИКОВ",support_box_text:"<span class='highlight'>DarkHeida</span> сейчас <span class='highlight'>в разработке.</span><br><br>Неработающие кнопки <span class='highlight'>скоро</span> станут доступны.<br><br>🙏",smart_title:"🤖 УМНЫЙ РЕЖИМ",smart_desc:"Управляет ПК автоматически.",respect:"⚠️ Уважай и будь уважаем!<br>Неуважение останавливает программу.",tc_title:"🎨 СОЗДАТЬ ТЕМУ",tc_name:"Название:",tc_bg:"Фон:",tc_card:"Карта:",tc_text:"Текст:",tc_accent:"Акцент:",tc_border:"Граница:",tc_save:"💾 Сохранить",tc_close:"❌ Закрыть",manifesto_title:"📜 МАНИФЕСТ",manifesto_text:"Эта программа создана, чтобы объединять, а не конкурировать. Уважайте труд. Открытый код. Не удаляйте подпись :))",notif_saved:"✅ Сохранено",notif_deleted:"🗑️ Удалено",notif_mode:"⚡ Режим",notif_smart_on:"🤖 Умный режим ВКЛ",notif_smart_off:"🤖 Умный режим ВЫКЛ",notif_psave_on:"🔋 Энергосбережение ВКЛ",notif_psave_off:"⚡ Обычный режим",notif_dnd_on:"🔕 Не беспокоить ВКЛ",notif_dnd_off:"🔔 Не беспокоить ВЫКЛ",notif_hz:"⚡ Hz применено",notif_night:"🌙 Ночной режим",warn_theme_name:"Введите название!",warn_default:"Стандартные темы нельзя удалить!",sig_hint:"нажми",adv_menu:"🛠️ Расширенные",adv_title:"🛠️ РАСШИРЕННЫЕ НАСТРОЙКИ",adv_warning:"💡 Этот раздел для разработчиков.",dev_info_title:"Заметка разработчикам",dev_info_text:"Этот раздел для разработчиков <b>DarkHeida</b>.",dev_empty_1:"ПУСТАЯ ОБЛАСТЬ - 1",dev_empty_2:"ПУСТАЯ ОБЛАСТЬ - 2",dev_empty_3:"ПУСТАЯ ОБЛАСТЬ - 3",dev_empty_desc:"Эта карта оставлена пустой.",adv_gpu:"РЕЖИМ GPU",adv_power:"УПРАВЛЕНИЕ ПИТАНИЕМ",adv_standby:"СОВРЕМЕННЫЙ STANDBY",adv_keys:"НАЗНАЧЕНИЯ КЛАВИШ",adv_soon:"⏳ СКОРО",adv_footer:"⏳ Будет добавлено разработчиками"},

ZH:{cpu:"🧠 处理器",ram:"💾 内存",disk:"💿 磁盘",net:"🌐 网络",app_title:"DarkHeida",menu_perf:"⚡ 性能",menu_screen:"🖥️ 显示",menu_battery:"🔋 电池",menu_keyboard:"⌨️ 键盘",menu_settings:"⚙️ 设置",menu_support:"🆘 支持",perf_mode:"⚡ 性能模式",quick_access:"⚡ 快速访问",btn_windows:"Windows",btn_silent:"静音",btn_performance:"性能",btn_turbo:"涡轮",night_mode:"🌙 夜间模式",dnd:"🔕 请勿打扰",power_save:"🔋 省电",screen_title:"🖥️ 显示设置",resolution:"📐 分辨率",current_hz:"⚡ 当前 Hz",refresh_rate:"刷新率",brightness:"☀️ 亮度",battery_title:"🔋 电池健康",battery_percent:"🔋 电池 %",battery_status:"⚡ 状态",charge_limit:"🔌 充电限制",battery_health:"电池健康: ⏳ 即将推出",keyboard_title:"⌨️ 键盘",effect:"✨ 效果",kb_warning:"⚠️ 此功能正在开发中。",kb_bright:"💡 键盘亮度",settings_title:"⚙️ 设置",startup_title:"🔄 开机启动",startup_desc:"Windows 启动时 DarkHeida 自动运行",theme:"🎨 主题",create_theme:"🎨 创建新主题",delete_theme:"🗑️ 删除选定主题",language:"🌐 语言",sys_info:"💻 系统信息",support_title:"🆘 支持",report_issue:"🐛 报告问题 (GitHub)",dev_notes:"📝 开发者笔记",dev_notes_text:"开源项目。<br><br>• 电池健康<br>• 风扇温度<br>• 键盘灯<br>• 禁用 GPU<br>• 音量控制<br>• 智能模式",uptime:"运行时间",volume:"🔊 音量",fan_title:"🌡️ 风扇速度",fan_placeholder:"将由开发者添加。",support_box_title:"🚀 开发者支持",support_box_text:"<span class='highlight'>DarkHeida</span> 目前 <span class='highlight'>正在开发中。</span><br><br>不工作的按钮将 <span class='highlight'>很快</span> 可用。<br><br>🙏",smart_title:"🤖 智能模式",smart_desc:"自动管理您的电脑。",respect:"⚠️ 尊重他人，赢得尊重！<br>不尊重劳动会停止程序。",tc_title:"🎨 创建主题",tc_name:"主题名称：",tc_bg:"背景：",tc_card:"卡片：",tc_text:"文本：",tc_accent:"强调：",tc_border:"边框：",tc_save:"💾 保存",tc_close:"❌ 关闭",manifesto_title:"📜 宣言",manifesto_text:"这个程序是为了统一而不是竞争。请尊重劳动。开源。请不要删除我们的签名 :))",notif_saved:"✅ 已保存",notif_deleted:"🗑️ 已删除",notif_mode:"⚡ 模式",notif_smart_on:"🤖 智能 开",notif_smart_off:"🤖 智能 关",notif_psave_on:"🔋 省电 开",notif_psave_off:"⚡ 正常",notif_dnd_on:"🔕 免打扰 开",notif_dnd_off:"🔔 免打扰 关",notif_hz:"⚡ Hz 已应用",notif_night:"🌙 夜间模式",warn_theme_name:"需要主题名称！",warn_default:"默认主题无法删除！",sig_hint:"点击",adv_menu:"🛠️ 高级",adv_title:"🛠️ 高级设置",adv_warning:"💡 此区域供开发者使用。",dev_info_title:"开发者须知",dev_info_text:"此区域为 <b>DarkHeida</b> 开发者设计。",dev_empty_1:"空白区域 - 1",dev_empty_2:"空白区域 - 2",dev_empty_3:"空白区域 - 3",dev_empty_desc:"此卡片留空。",adv_gpu:"GPU 模式",adv_power:"电源管理",adv_standby:"现代待机",adv_keys:"按键绑定",adv_soon:"⏳ 即将推出",adv_footer:"⏳ 将由开发者添加"},

JA:{cpu:"🧠 CPU",ram:"💾 メモリ",disk:"💿 ディスク",net:"🌐 ネットワーク",app_title:"DarkHeida",menu_perf:"⚡ パフォーマンス",menu_screen:"🖥️ ディスプレイ",menu_battery:"🔋 バッテリー",menu_keyboard:"⌨️ キーボード",menu_settings:"⚙️ 設定",menu_support:"🆘 サポート",perf_mode:"⚡ パフォーマンスモード",quick_access:"⚡ クイックアクセス",btn_windows:"Windows",btn_silent:"サイレント",btn_performance:"パフォーマンス",btn_turbo:"ターボ",night_mode:"🌙 ナイトモード",dnd:"🔕 おやすみ",power_save:"🔋 省電力",screen_title:"🖥️ ディスプレイ設定",resolution:"📐 解像度",current_hz:"⚡ 現在 Hz",refresh_rate:"リフレッシュレート",brightness:"☀️ 明るさ",battery_title:"🔋 バッテリー",battery_percent:"🔋 バッテリー %",battery_status:"⚡ 状態",charge_limit:"🔌 充電制限",battery_health:"バッテリー: ⏳ 近日公開",keyboard_title:"⌨️ キーボード",effect:"✨ エフェクト",kb_warning:"⚠️ この機能は開発中です。",kb_bright:"💡 明るさ",settings_title:"⚙️ 設定",startup_title:"🔄 起動時に実行",startup_desc:"Windows起動時に自動起動",theme:"🎨 テーマ",create_theme:"🎨 テーマ作成",delete_theme:"🗑️ テーマ削除",language:"🌐 言語",sys_info:"💻 システム情報",support_title:"🆘 サポート",report_issue:"🐛 バグ報告 (GitHub)",dev_notes:"📝 開発者ノート",dev_notes_text:"オープンソース。<br><br>• バッテリー<br>• ファン温度<br>• キーボードライト<br>• GPU無効化<br>• 音量<br>• スマートモード",uptime:"稼働時間",volume:"🔊 音量",fan_title:"🌡️ ファン速度",fan_placeholder:"開発者が追加予定。",support_box_title:"🚀 開発者サポート",support_box_text:"<span class='highlight'>DarkHeida</span>は現在<span class='highlight'>開発中です。</span><br><br>機能しないボタンは<span class='highlight'>まもなく</span>利用可能になります。🙏",smart_title:"🤖 スマートモード",smart_desc:"PCを自動管理します。",respect:"⚠️ 尊重し、尊重されよ！<br>労働への敬意を欠くと停止します。",tc_title:"🎨 テーマ作成",tc_name:"テーマ名：",tc_bg:"背景：",tc_card:"カード：",tc_text:"テキスト：",tc_accent:"アクセント：",tc_border:"ボーダー：",tc_save:"💾 保存",tc_close:"❌ 閉じる",manifesto_title:"📜 マニフェスト",manifesto_text:"このプログラムは競争ではなく統一のために作られました。労働を尊重してください。署名は削除しないでください :))",notif_saved:"✅ 保存",notif_deleted:"🗑️ 削除",notif_mode:"⚡ モード",notif_smart_on:"🤖 スマート ON",notif_smart_off:"🤖 スマート OFF",notif_psave_on:"🔋 省電力 ON",notif_psave_off:"⚡ 通常",notif_dnd_on:"🔕 おやすみ ON",notif_dnd_off:"🔔 おやすみ OFF",notif_hz:"⚡ Hz 適用",notif_night:"🌙 ナイトモード",warn_theme_name:"テーマ名必要！",warn_default:"デフォルトテーマは削除不可！",sig_hint:"クリック",adv_menu:"🛠️ 詳細",adv_title:"🛠️ 詳細設定",adv_warning:"💡 このエリアは開発者用です。",dev_info_title:"開発者への注意",dev_info_text:"このエリアは <b>DarkHeida</b> 開発者用です。",dev_empty_1:"空きエリア - 1",dev_empty_2:"空きエリア - 2",dev_empty_3:"空きエリア - 3",dev_empty_desc:"このカードは空です。",adv_gpu:"GPUモード",adv_power:"電源管理",adv_standby:"モダンスタンバイ",adv_keys:"キー割り当て",adv_soon:"⏳ 近日公開",adv_footer:"⏳ 開発者が追加"},

KO:{cpu:"🧠 CPU",ram:"💾 메모리",disk:"💿 디스크",net:"🌐 네트워크",app_title:"DarkHeida",menu_perf:"⚡ 성능",menu_screen:"🖥️ 디스플레이",menu_battery:"🔋 배터리",menu_keyboard:"⌨️ 키보드",menu_settings:"⚙️ 설정",menu_support:"🆘 지원",perf_mode:"⚡ 성능 모드",quick_access:"⚡ 빠른 액세스",btn_windows:"Windows",btn_silent:"조용히",btn_performance:"성능",btn_turbo:"터보",night_mode:"🌙 야간 모드",dnd:"🔕 방해 금지",power_save:"🔋 절전",screen_title:"🖥️ 디스플레이",resolution:"📐 해상도",current_hz:"⚡ 현재 Hz",refresh_rate:"주사율",brightness:"☀️ 밝기",battery_title:"🔋 배터리",battery_percent:"🔋 배터리 %",battery_status:"⚡ 상태",charge_limit:"🔌 충전 제한",battery_health:"배터리: ⏳ 곧",keyboard_title:"⌨️ 키보드",effect:"✨ 효과",kb_warning:"⚠️ 이 기능은 개발 중입니다.",kb_bright:"💡 키보드 밝기",settings_title:"⚙️ 설정",startup_title:"🔄 시작 시 실행",startup_desc:"Windows 시작 시 자동 실행",theme:"🎨 테마",create_theme:"🎨 테마 만들기",delete_theme:"🗑️ 테마 삭제",language:"🌐 언어",sys_info:"💻 시스템 정보",support_title:"🆘 지원",report_issue:"🐛 버그 신고 (GitHub)",dev_notes:"📝 개발자 노트",dev_notes_text:"오픈 소스.<br><br>• 배터리 상태<br>• 팬 온도<br>• 키보드 조명<br>• GPU 비활성화<br>• 볼륨<br>• 스마트 모드",uptime:"가동 시간",volume:"🔊 볼륨",fan_title:"🌡️ 팬 속도",fan_placeholder:"개발자가 추가 예정.",support_box_title:"🚀 개발자 지원",support_box_text:"<span class='highlight'>DarkHeida</span>는 현재 <span class='highlight'>개발 중입니다.</span><br><br>곧 사용 가능합니다. 🙏",smart_title:"🤖 스마트 모드",smart_desc:"PC를 자동 관리합니다.",respect:"⚠️ 존중하라, 존중받으라!",tc_title:"🎨 테마 만들기",tc_name:"테마 이름:",tc_bg:"배경:",tc_card:"카드:",tc_text:"텍스트:",tc_accent:"강조:",tc_border:"테두리:",tc_save:"💾 저장",tc_close:"❌ 닫기",manifesto_title:"📜 선언문",manifesto_text:"이 프로그램은 경쟁이 아닌 통합을 위해 만들어졌습니다. 서명을 제거하지 마세요 :))",notif_saved:"✅ 저장됨",notif_deleted:"🗑️ 삭제됨",notif_mode:"⚡ 모드",notif_smart_on:"🤖 스마트 ON",notif_smart_off:"🤖 스마트 OFF",notif_psave_on:"🔋 절전 ON",notif_psave_off:"⚡ 일반",notif_dnd_on:"🔕 방해금지 ON",notif_dnd_off:"🔔 방해금지 OFF",notif_hz:"⚡ Hz 적용",notif_night:"🌙 야간 모드",warn_theme_name:"테마 이름 필요!",warn_default:"기본 테마 삭제 불가!",sig_hint:"클릭",adv_menu:"🛠️ 고급",adv_title:"🛠️ 고급 설정",adv_warning:"💡 이 영역은 개발자용입니다.",dev_info_title:"개발자 노트",dev_info_text:"이 영역은 <b>DarkHeida</b> 개발자용입니다.",dev_empty_1:"빈 영역 - 1",dev_empty_2:"빈 영역 - 2",dev_empty_3:"빈 영역 - 3",dev_empty_desc:"이 카드는 비어 있습니다.",adv_gpu:"GPU 모드",adv_power:"전원 관리",adv_standby:"모던 스탠바이",adv_keys:"키 바인딩",adv_soon:"⏳ 곧",adv_footer:"⏳ 개발자가 추가"},

IT:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Disco",net:"🌐 Rete",app_title:"DarkHeida",menu_perf:"⚡ Prestazioni",menu_screen:"🖥️ Display",menu_battery:"🔋 Batteria",menu_keyboard:"⌨️ Tastiera",menu_settings:"⚙️ Impostazioni",menu_support:"🆘 Supporto",perf_mode:"⚡ MODALITÀ PRESTAZIONI",quick_access:"⚡ ACCESSO RAPIDO",btn_windows:"Windows",btn_silent:"Silenzioso",btn_performance:"Prestazioni",btn_turbo:"Turbo",night_mode:"🌙 Modalità notturna",dnd:"🔕 Non disturbare",power_save:"🔋 Risparmio",screen_title:"🖥️ SCHERMO",resolution:"📐 Risoluzione",current_hz:"⚡ Hz attuale",refresh_rate:"Frequenza",brightness:"☀️ Luminosità",battery_title:"🔋 BATTERIA",battery_percent:"🔋 Batteria %",battery_status:"⚡ Stato",charge_limit:"🔌 Limite di carica",battery_health:"Salute: ⏳ Prossimamente",keyboard_title:"⌨️ TASTIERA",effect:"✨ Effetto",kb_warning:"⚠️ In sviluppo.",kb_bright:"💡 Luminosità",settings_title:"⚙️ IMPOSTAZIONI",startup_title:"🔄 Avvia all'avvio",startup_desc:"Avvio automatico con Windows",theme:"🎨 Tema",create_theme:"🎨 CREA TEMA",delete_theme:"🗑️ ELIMINA TEMA",language:"🌐 Lingua",sys_info:"💻 Sistema",support_title:"🆘 SUPPORTO",report_issue:"🐛 Segnala bug (GitHub)",dev_notes:"📝 Note sviluppatore",dev_notes_text:"Open source.<br><br>• Salute batteria<br>• Velocità ventola<br>• Luce tastiera<br>• Disabilita GPU<br>• Volume<br>• Modalità intelligente",uptime:"Tempo attività",volume:"🔊 Volume",fan_title:"🌡️ VENTOLA E TEMPERATURA",fan_placeholder:"Sarà aggiunto dagli sviluppatori.",support_box_title:"🚀 SUPPORTO SVILUPPATORI",support_box_text:"<span class='highlight'>DarkHeida</span> è in <span class='highlight'>sviluppo.</span><br><br>Presto disponibile. 🙏",smart_title:"🤖 MODALITÀ INTELLIGENTE",smart_desc:"Gestisce il PC automaticamente.",respect:"⚠️ Rispetta e sarai rispettato!",tc_title:"🎨 CREA TEMA",tc_name:"Nome tema:",tc_bg:"Sfondo:",tc_card:"Card:",tc_text:"Testo:",tc_accent:"Accento:",tc_border:"Bordo:",tc_save:"💾 Salva",tc_close:"❌ Chiudi",manifesto_title:"📜 MANIFESTO",manifesto_text:"Questo programma è stato creato per unire, non per competere. Non rimuovere la firma :))",notif_saved:"✅ Salvato",notif_deleted:"🗑️ Eliminato",notif_mode:"⚡ Modalità",notif_smart_on:"🤖 Smart ON",notif_smart_off:"🤖 Smart OFF",notif_psave_on:"🔋 Risparmio ON",notif_psave_off:"⚡ Normale",notif_dnd_on:"🔕 Non disturbare ON",notif_dnd_off:"🔔 Non disturbare OFF",notif_hz:"⚡ Hz applicato",notif_night:"🌙 Modalità notturna",warn_theme_name:"Nome tema richiesto!",warn_default:"Temi predefiniti non eliminabili!",sig_hint:"clicca",adv_menu:"🛠️ Avanzate",adv_title:"🛠️ IMPOSTAZIONI AVANZATE",adv_warning:"💡 Questa area è per sviluppatori.",dev_info_title:"Nota per sviluppatori",dev_info_text:"Questa area è per sviluppatori di <b>DarkHeida</b>.",dev_empty_1:"AREA VUOTA - 1",dev_empty_2:"AREA VUOTA - 2",dev_empty_3:"AREA VUOTA - 3",dev_empty_desc:"Questa scheda è vuota.",adv_gpu:"MODALITÀ GPU",adv_power:"GESTIONE ENERGIA",adv_standby:"MODERN STANDBY",adv_keys:"ASSEGNAZIONE TASTI",adv_soon:"⏳ PRESTO",adv_footer:"⏳ Sarà aggiunto dagli sviluppatori"},

PT:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Disco",net:"🌐 Rede",app_title:"DarkHeida",menu_perf:"⚡ Desempenho",menu_screen:"🖥️ Tela",menu_battery:"🔋 Bateria",menu_keyboard:"⌨️ Teclado",menu_settings:"⚙️ Configurações",menu_support:"🆘 Suporte",perf_mode:"⚡ MODO DESEMPENHO",quick_access:"⚡ ACESSO RÁPIDO",btn_windows:"Windows",btn_silent:"Silencioso",btn_performance:"Desempenho",btn_turbo:"Turbo",night_mode:"🌙 Modo noturno",dnd:"🔕 Não perturbe",power_save:"🔋 Economia",screen_title:"🖥️ TELA",resolution:"📐 Resolução",current_hz:"⚡ Hz atual",refresh_rate:"Taxa atualização",brightness:"☀️ Brilho",battery_title:"🔋 BATERIA",battery_percent:"🔋 Bateria %",battery_status:"⚡ Status",charge_limit:"🔌 Limite carga",battery_health:"Saúde: ⏳ Em breve",keyboard_title:"⌨️ TECLADO",effect:"✨ Efeito",kb_warning:"⚠️ Em desenvolvimento.",kb_bright:"💡 Brilho",settings_title:"⚙️ CONFIGURAÇÕES",startup_title:"🔄 Executar na inicialização",startup_desc:"Início automático com Windows",theme:"🎨 Tema",create_theme:"🎨 CRIAR TEMA",delete_theme:"🗑️ EXCLUIR TEMA",language:"🌐 Idioma",sys_info:"💻 Sistema",support_title:"🆘 SUPORTE",report_issue:"🐛 Relatar bug (GitHub)",dev_notes:"📝 Notas desenvolvedor",dev_notes_text:"Código aberto.<br><br>• Saúde bateria<br>• Velocidade ventoinha<br>• Luz teclado<br>• Desabilitar GPU<br>• Volume<br>• Modo inteligente",uptime:"Tempo ativo",volume:"🔊 Volume",fan_title:"🌡️ VENTOINHA E TEMP",fan_placeholder:"Será adicionado por desenvolvedores.",support_box_title:"🚀 SUPORTE DESENVOLVEDOR",support_box_text:"<span class='highlight'>DarkHeida</span> em <span class='highlight'>desenvolvimento.</span><br><br>Em breve. 🙏",smart_title:"🤖 MODO INTELIGENTE",smart_desc:"Gerencia seu PC automaticamente.",respect:"⚠️ Respeite e seja respeitado!",tc_title:"🎨 CRIAR TEMA",tc_name:"Nome:",tc_bg:"Fundo:",tc_card:"Card:",tc_text:"Texto:",tc_accent:"Destaque:",tc_border:"Borda:",tc_save:"💾 Salvar",tc_close:"❌ Fechar",manifesto_title:"📜 MANIFESTO",manifesto_text:"Este programa foi criado para unir, não competir. Não remova a assinatura :))",notif_saved:"✅ Salvo",notif_deleted:"🗑️ Excluído",notif_mode:"⚡ Modo",notif_smart_on:"🤖 Smart ON",notif_smart_off:"🤖 Smart OFF",notif_psave_on:"🔋 Economia ON",notif_psave_off:"⚡ Normal",notif_dnd_on:"🔕 Não perturbe ON",notif_dnd_off:"🔔 Não perturbe OFF",notif_hz:"⚡ Hz aplicado",notif_night:"🌙 Modo noturno",warn_theme_name:"Nome obrigatório!",warn_default:"Temas padrão não excluíveis!",sig_hint:"clique",adv_menu:"🛠️ Avançado",adv_title:"🛠️ CONFIGURAÇÕES AVANÇADAS",adv_warning:"💡 Esta área é para desenvolvedores.",dev_info_title:"Nota para desenvolvedores",dev_info_text:"Esta área é para desenvolvedores do <b>DarkHeida</b>.",dev_empty_1:"ÁREA VAZIA - 1",dev_empty_2:"ÁREA VAZIA - 2",dev_empty_3:"ÁREA VAZIA - 3",dev_empty_desc:"Este card está vazio.",adv_gpu:"MODO GPU",adv_power:"GERENCIAMENTO ENERGIA",adv_standby:"MODERN STANDBY",adv_keys:"ATRIBUIÇÃO TECLAS",adv_soon:"⏳ EM BREVE",adv_footer:"⏳ Será adicionado por desenvolvedores"},

AR:{cpu:"🧠 المعالج",ram:"💾 الذاكرة",disk:"💿 القرص",net:"🌐 الشبكة",app_title:"DarkHeida",menu_perf:"⚡ الأداء",menu_screen:"🖥️ العرض",menu_battery:"🔋 البطارية",menu_keyboard:"⌨️ لوحة المفاتيح",menu_settings:"⚙️ الإعدادات",menu_support:"🆘 الدعم",perf_mode:"⚡ وضع الأداء",quick_access:"⚡ وصول سريع",btn_windows:"Windows",btn_silent:"صامت",btn_performance:"الأداء",btn_turbo:"تيربو",night_mode:"🌙 الوضع الليلي",dnd:"🔕 عدم الإزعاج",power_save:"🔋 توفير الطاقة",screen_title:"🖥️ العرض",resolution:"📐 الدقة",current_hz:"⚡ Hz الحالي",refresh_rate:"معدل التحديث",brightness:"☀️ السطوع",battery_title:"🔋 البطارية",battery_percent:"🔋 البطارية %",battery_status:"⚡ الحالة",charge_limit:"🔌 حد الشحن",battery_health:"الصحة: ⏳ قريباً",keyboard_title:"⌨️ لوحة المفاتيح",effect:"✨ تأثير",kb_warning:"⚠️ قيد التطوير.",kb_bright:"💡 السطوع",settings_title:"⚙️ الإعدادات",startup_title:"🔄 التشغيل عند البدء",startup_desc:"بدء تلقائي مع Windows",theme:"🎨 السمة",create_theme:"🎨 إنشاء سمة",delete_theme:"🗑️ حذف السمة",language:"🌐 اللغة",sys_info:"💻 النظام",support_title:"🆘 الدعم",report_issue:"🐛 الإبلاغ عن خطأ (GitHub)",dev_notes:"📝 ملاحظات المطور",dev_notes_text:"مفتوح المصدر.<br><br>• صحة البطارية<br>• سرعة المروحة<br>• إضاءة المفاتيح<br>• تعطيل GPU<br>• الصوت<br>• الوضع الذكي",uptime:"وقت التشغيل",volume:"🔊 الصوت",fan_title:"🌡️ المروحة والحرارة",fan_placeholder:"سيضيفه المطورون.",support_box_title:"🚀 دعم المطورين",support_box_text:"<span class='highlight'>DarkHeida</span> قيد <span class='highlight'>التطوير.</span><br><br>قريباً. 🙏",smart_title:"🤖 الوضع الذكي",smart_desc:"يدير جهازك تلقائياً.",respect:"⚠️ احترم واحترم!",tc_title:"🎨 إنشاء سمة",tc_name:"اسم السمة:",tc_bg:"الخلفية:",tc_card:"البطاقة:",tc_text:"النص:",tc_accent:"التمييز:",tc_border:"الحدود:",tc_save:"💾 حفظ",tc_close:"❌ إغلاق",manifesto_title:"📜 البيان",manifesto_text:"تم إنشاء هذا البرنامج للتوحيد وليس للمنافسة. لا تزل التوقيع :))",notif_saved:"✅ تم الحفظ",notif_deleted:"🗑️ تم الحذف",notif_mode:"⚡ الوضع",notif_smart_on:"🤖 ذكي مفعل",notif_smart_off:"🤖 ذكي معطل",notif_psave_on:"🔋 توفير مفعل",notif_psave_off:"⚡ عادي",notif_dnd_on:"🔕 عدم الإزعاج مفعل",notif_dnd_off:"🔔 عدم الإزعاج معطل",notif_hz:"⚡ تم تطبيق Hz",notif_night:"🌙 الوضع الليلي",warn_theme_name:"اسم السمة مطلوب!",warn_default:"لا يمكن حذف السمات الافتراضية!",sig_hint:"انقر",adv_menu:"🛠️ متقدم",adv_title:"🛠️ إعدادات متقدمة",adv_warning:"💡 هذه المنطقة للمطورين.",dev_info_title:"ملاحظة للمطورين",dev_info_text:"هذه المنطقة مخصصة لمطوري <b>DarkHeida</b>.",dev_empty_1:"منطقة فارغة - 1",dev_empty_2:"منطقة فارغة - 2",dev_empty_3:"منطقة فارغة - 3",dev_empty_desc:"هذه البطاقة فارغة.",adv_gpu:"وضع GPU",adv_power:"إدارة الطاقة",adv_standby:"الاستعداد الحديث",adv_keys:"تعيين المفاتيح",adv_soon:"⏳ قريباً",adv_footer:"⏳ سيتم إضافته من المطورين"},

HI:{cpu:"🧠 सीपीयू",ram:"💾 रैम",disk:"💿 डिस्क",net:"🌐 नेटवर्क",app_title:"DarkHeida",menu_perf:"⚡ प्रदर्शन",menu_screen:"🖥️ डिस्प्ले",menu_battery:"🔋 बैटरी",menu_keyboard:"⌨️ कीबोर्ड",menu_settings:"⚙️ सेटिंग्स",menu_support:"🆘 सहायता",perf_mode:"⚡ प्रदर्शन मोड",quick_access:"⚡ त्वरित पहुँच",btn_windows:"Windows",btn_silent:"साइलेंट",btn_performance:"प्रदर्शन",btn_turbo:"टर्बो",night_mode:"🌙 रात्रि मोड",dnd:"🔕 परेशान न करें",power_save:"🔋 पावर सेव",screen_title:"🖥️ डिस्प्ले",resolution:"📐 रिज़ॉल्यूशन",current_hz:"⚡ वर्तमान Hz",refresh_rate:"रिफ्रेश रेट",brightness:"☀️ चमक",battery_title:"🔋 बैटरी",battery_percent:"🔋 बैटरी %",battery_status:"⚡ स्थिति",charge_limit:"🔌 चार्ज सीमा",battery_health:"स्वास्थ्य: ⏳ जल्द",keyboard_title:"⌨️ कीबोर्ड",effect:"✨ प्रभाव",kb_warning:"⚠️ विकास में।",kb_bright:"💡 चमक",settings_title:"⚙️ सेटिंग्स",startup_title:"🔄 स्टार्टअप पर चलाएँ",startup_desc:"Windows बूट पर स्वतः शुरू",theme:"🎨 थीम",create_theme:"🎨 थीम बनाएँ",delete_theme:"🗑️ थीम हटाएँ",language:"🌐 भाषा",sys_info:"💻 सिस्टम",support_title:"🆘 सहायता",report_issue:"🐛 बग रिपोर्ट (GitHub)",dev_notes:"📝 डेवलपर नोट्स",dev_notes_text:"ओपन सोर्स।<br><br>• बैटरी स्वास्थ्य<br>• पंखा गति<br>• कीबोर्ड लाइट<br>• GPU अक्षम<br>• वॉल्यूम<br>• स्मार्ट मोड",uptime:"अपटाइम",volume:"🔊 वॉल्यूम",fan_title:"🌡️ पंखा और तापमान",fan_placeholder:"डेवलपर्स जोड़ेंगे।",support_box_title:"🚀 डेवलपर सहायता",support_box_text:"<span class='highlight'>DarkHeida</span> <span class='highlight'>विकास में है।</span><br><br>जल्द उपलब्ध। 🙏",smart_title:"🤖 स्मार्ट मोड",smart_desc:"PC स्वचालित प्रबंधन।",respect:"⚠️ सम्मान करें और पाएं!",tc_title:"🎨 थीम बनाएँ",tc_name:"थीम नाम:",tc_bg:"पृष्ठभूमि:",tc_card:"कार्ड:",tc_text:"टेक्स्ट:",tc_accent:"एक्सेंट:",tc_border:"बॉर्डर:",tc_save:"💾 सहेजें",tc_close:"❌ बंद",manifesto_title:"📜 घोषणापत्र",manifesto_text:"यह कार्यक्रम एकता के लिए बनाया गया है। हस्ताक्षर न हटाएँ :))",notif_saved:"✅ सहेजा",notif_deleted:"🗑️ हटाया",notif_mode:"⚡ मोड",notif_smart_on:"🤖 स्मार्ट ON",notif_smart_off:"🤖 स्मार्ट OFF",notif_psave_on:"🔋 पावर सेव ON",notif_psave_off:"⚡ सामान्य",notif_dnd_on:"🔕 DND ON",notif_dnd_off:"🔔 DND OFF",notif_hz:"⚡ Hz लागू",notif_night:"🌙 नाइट मोड",warn_theme_name:"थीम नाम आवश्यक!",warn_default:"डिफ़ॉल्ट थीम हटाई नहीं जा सकतीं!",sig_hint:"क्लिक",adv_menu:"🛠️ उन्नत",adv_title:"🛠️ उन्नत सेटिंग्स",adv_warning:"💡 यह क्षेत्र डेवलपर्स के लिए है।",dev_info_title:"डेवलपर्स के लिए नोट",dev_info_text:"यह क्षेत्र <b>DarkHeida</b> डेवलपर्स के लिए है।",dev_empty_1:"खाली क्षेत्र - 1",dev_empty_2:"खाली क्षेत्र - 2",dev_empty_3:"खाली क्षेत्र - 3",dev_empty_desc:"यह कार्ड खाली है।",adv_gpu:"GPU मोड",adv_power:"पावर प्रबंधन",adv_standby:"मॉडर्न स्टैंडबाय",adv_keys:"की बाइंडिंग",adv_soon:"⏳ जल्द",adv_footer:"⏳ डेवलपर्स जोड़ेंगे"},

ID:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Disk",net:"🌐 Jaringan",app_title:"DarkHeida",menu_perf:"⚡ Performa",menu_screen:"🖥️ Tampilan",menu_battery:"🔋 Baterai",menu_keyboard:"⌨️ Keyboard",menu_settings:"⚙️ Pengaturan",menu_support:"🆘 Dukungan",perf_mode:"⚡ MODE PERFORMA",quick_access:"⚡ AKSES CEPAT",btn_windows:"Windows",btn_silent:"Senyap",btn_performance:"Performa",btn_turbo:"Turbo",night_mode:"🌙 Mode Malam",dnd:"🔕 Jangan Ganggu",power_save:"🔋 Hemat Daya",screen_title:"🖥️ TAMPILAN",resolution:"📐 Resolusi",current_hz:"⚡ Hz Saat Ini",refresh_rate:"Tingkat Penyegaran",brightness:"☀️ Kecerahan",battery_title:"🔋 BATERAI",battery_percent:"🔋 Baterai %",battery_status:"⚡ Status",charge_limit:"🔌 Batas Isi",battery_health:"Kesehatan: ⏳ Segera",keyboard_title:"⌨️ KEYBOARD",effect:"✨ Efek",kb_warning:"⚠️ Dalam pengembangan.",kb_bright:"💡 Kecerahan",settings_title:"⚙️ PENGATURAN",startup_title:"🔄 Jalankan saat Startup",startup_desc:"Mulai otomatis dengan Windows",theme:"🎨 Tema",create_theme:"🎨 BUAT TEMA",delete_theme:"🗑️ HAPUS TEMA",language:"🌐 Bahasa",sys_info:"💻 Sistem",support_title:"🆘 DUKUNGAN",report_issue:"🐛 Laporkan Bug (GitHub)",dev_notes:"📝 Catatan Pengembang",dev_notes_text:"Open source.<br><br>• Kesehatan Baterai<br>• Kecepatan Kipas<br>• Lampu Keyboard<br>• Nonaktifkan GPU<br>• Volume<br>• Mode Cerdas",uptime:"Waktu Aktif",volume:"🔊 Volume",fan_title:"🌡️ KIPAS & SUHU",fan_placeholder:"Akan ditambahkan pengembang.",support_box_title:"🚀 DUKUNGAN PENGEMBANG",support_box_text:"<span class='highlight'>DarkHeida</span> dalam <span class='highlight'>pengembangan.</span><br><br>Segera. 🙏",smart_title:"🤖 MODE CERDAS",smart_desc:"Mengelola PC otomatis.",respect:"⚠️ Hormati dan Dihormati!",tc_title:"🎨 BUAT TEMA",tc_name:"Nama Tema:",tc_bg:"Latar:",tc_card:"Kartu:",tc_text:"Teks:",tc_accent:"Aksen:",tc_border:"Batas:",tc_save:"💾 Simpan",tc_close:"❌ Tutup",manifesto_title:"📜 MANIFESTO",manifesto_text:"Program ini dibuat untuk menyatukan, bukan bersaing. Jangan hapus tanda tangan :))",notif_saved:"✅ Disimpan",notif_deleted:"🗑️ Dihapus",notif_mode:"⚡ Mode",notif_smart_on:"🤖 Smart ON",notif_smart_off:"🤖 Smart OFF",notif_psave_on:"🔋 Hemat ON",notif_psave_off:"⚡ Normal",notif_dnd_on:"🔕 Jangan Ganggu ON",notif_dnd_off:"🔔 Jangan Ganggu OFF",notif_hz:"⚡ Hz diterapkan",notif_night:"🌙 Mode Malam",warn_theme_name:"Nama tema diperlukan!",warn_default:"Tema default tidak dapat dihapus!",sig_hint:"klik",adv_menu:"🛠️ Lanjutan",adv_title:"🛠️ PENGATURAN LANJUTAN",adv_warning:"💡 Area ini untuk pengembang.",dev_info_title:"Catatan Pengembang",dev_info_text:"Area ini untuk pengembang <b>DarkHeida</b>.",dev_empty_1:"AREA KOSONG - 1",dev_empty_2:"AREA KOSONG - 2",dev_empty_3:"AREA KOSONG - 3",dev_empty_desc:"Kartu ini kosong.",adv_gpu:"MODE GPU",adv_power:"MANAJEMEN DAYA",adv_standby:"MODERN STANDBY",adv_keys:"PENGIKATAN TOMBOL",adv_soon:"⏳ SEGERA",adv_footer:"⏳ Ditambahkan pengembang"},

NL:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Schijf",net:"🌐 Netwerk",app_title:"DarkHeida",menu_perf:"⚡ Prestaties",menu_screen:"🖥️ Weergave",menu_battery:"🔋 Batterij",menu_keyboard:"⌨️ Toetsenbord",menu_settings:"⚙️ Instellingen",menu_support:"🆘 Ondersteuning",perf_mode:"⚡ PRESTATIEMODUS",quick_access:"⚡ SNELLE TOEGANG",btn_windows:"Windows",btn_silent:"Stil",btn_performance:"Prestaties",btn_turbo:"Turbo",night_mode:"🌙 Nachtmodus",dnd:"🔕 Niet storen",power_save:"🔋 Energiebesparing",screen_title:"🖥️ WEERGAVE",resolution:"📐 Resolutie",current_hz:"⚡ Huidige Hz",refresh_rate:"Vernieuwing",brightness:"☀️ Helderheid",battery_title:"🔋 BATTERIJ",battery_percent:"🔋 Batterij %",battery_status:"⚡ Status",charge_limit:"🔌 Laadlimiet",battery_health:"Gezondheid: ⏳ Binnenkort",keyboard_title:"⌨️ TOETSENBORD",effect:"✨ Effect",kb_warning:"⚠️ In ontwikkeling.",kb_bright:"💡 Helderheid",settings_title:"⚙️ INSTELLINGEN",startup_title:"🔄 Uitvoeren bij opstarten",startup_desc:"Automatisch starten met Windows",theme:"🎨 Thema",create_theme:"🎨 NIEUW THEMA",delete_theme:"🗑️ THEMA VERWIJDEREN",language:"🌐 Taal",sys_info:"💻 Systeem",support_title:"🆘 ONDERSTEUNING",report_issue:"🐛 Bug melden (GitHub)",dev_notes:"📝 Ontwikkelaarsnotities",dev_notes_text:"Open source.<br><br>• Batterijgezondheid<br>• Fansnelheid<br>• Toetsenbordverlichting<br>• GPU uitschakelen<br>• Volume<br>• Slimme modus",uptime:"Uptime",volume:"🔊 Volume",fan_title:"🌡️ FAN & TEMP",fan_placeholder:"Wordt toegevoegd door ontwikkelaars.",support_box_title:"🚀 ONTWIKKELAARSONDERSTEUNING",support_box_text:"<span class='highlight'>DarkHeida</span> in <span class='highlight'>ontwikkeling.</span><br><br>Binnenkort. 🙏",smart_title:"🤖 SLIMME MODUS",smart_desc:"Beheert uw PC automatisch.",respect:"⚠️ Respecteer en word gerespecteerd!",tc_title:"🎨 THEMA MAKEN",tc_name:"Themanaam:",tc_bg:"Achtergrond:",tc_card:"Kaart:",tc_text:"Tekst:",tc_accent:"Accent:",tc_border:"Rand:",tc_save:"💾 Opslaan",tc_close:"❌ Sluiten",manifesto_title:"📜 MANIFEST",manifesto_text:"Dit programma is gemaakt om te verenigen, niet concurreren. Verwijder de handtekening niet :))",notif_saved:"✅ Opgeslagen",notif_deleted:"🗑️ Verwijderd",notif_mode:"⚡ Modus",notif_smart_on:"🤖 Slim ON",notif_smart_off:"🤖 Slim OFF",notif_psave_on:"🔋 Besparing ON",notif_psave_off:"⚡ Normaal",notif_dnd_on:"🔕 Niet storen ON",notif_dnd_off:"🔔 Niet storen OFF",notif_hz:"⚡ Hz toegepast",notif_night:"🌙 Nachtmodus",warn_theme_name:"Themanaam vereist!",warn_default:"Standaardthema's niet verwijderbaar!",sig_hint:"klik",adv_menu:"🛠️ Geavanceerd",adv_title:"🛠️ GEAVANCEERDE INSTELLINGEN",adv_warning:"💡 Dit gebied is voor ontwikkelaars.",dev_info_title:"Opmerking voor ontwikkelaars",dev_info_text:"Dit gebied is voor <b>DarkHeida</b>-ontwikkelaars.",dev_empty_1:"LEEG GEBIED - 1",dev_empty_2:"LEEG GEBIED - 2",dev_empty_3:"LEEG GEBIED - 3",dev_empty_desc:"Deze kaart is leeg.",adv_gpu:"GPU-MODUS",adv_power:"ENERGIEBEHEER",adv_standby:"MODERNE STANDBY",adv_keys:"TOETSEN TOEWIJZEN",adv_soon:"⏳ BINNENKORT",adv_footer:"⏳ Wordt toegevoegd door ontwikkelaars"},

PL:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Dysk",net:"🌐 Sieć",app_title:"DarkHeida",menu_perf:"⚡ Wydajność",menu_screen:"🖥️ Ekran",menu_battery:"🔋 Bateria",menu_keyboard:"⌨️ Klawiatura",menu_settings:"⚙️ Ustawienia",menu_support:"🆘 Wsparcie",perf_mode:"⚡ TRYB WYDAJNOŚCI",quick_access:"⚡ SZYBKI DOSTĘP",btn_windows:"Windows",btn_silent:"Cichy",btn_performance:"Wydajność",btn_turbo:"Turbo",night_mode:"🌙 Tryb nocny",dnd:"🔕 Nie przeszkadzać",power_save:"🔋 Oszczędzanie",screen_title:"🖥️ EKRAN",resolution:"📐 Rozdzielczość",current_hz:"⚡ Bieżące Hz",refresh_rate:"Częstotliwość",brightness:"☀️ Jasność",battery_title:"🔋 BATERIA",battery_percent:"🔋 Bateria %",battery_status:"⚡ Status",charge_limit:"🔌 Limit ładowania",battery_health:"Stan: ⏳ Wkrótce",keyboard_title:"⌨️ KLAWIATURA",effect:"✨ Efekt",kb_warning:"⚠️ W opracowaniu.",kb_bright:"💡 Jasność",settings_title:"⚙️ USTAWIENIA",startup_title:"🔄 Uruchom przy starcie",startup_desc:"Autostart z Windows",theme:"🎨 Motyw",create_theme:"🎨 UTWÓRZ MOTYW",delete_theme:"🗑️ USUŃ MOTYW",language:"🌐 Język",sys_info:"💻 System",support_title:"🆘 WSPARCIE",report_issue:"🐛 Zgłoś błąd (GitHub)",dev_notes:"📝 Notatki programisty",dev_notes_text:"Open source.<br><br>• Stan baterii<br>• Prędkość wentylatora<br>• Podświetlenie klawiatury<br>• Wyłącz GPU<br>• Głośność<br>• Tryb inteligentny",uptime:"Czas pracy",volume:"🔊 Głośność",fan_title:"🌡️ WENTYLATOR & TEMP",fan_placeholder:"Zostanie dodane przez programistów.",support_box_title:"🚀 WSPARCIE PROGRAMISTÓW",support_box_text:"<span class='highlight'>DarkHeida</span> w <span class='highlight'>fazie rozwoju.</span><br><br>Wkrótce. 🙏",smart_title:"🤖 TRYB INTELIGENTNY",smart_desc:"Automatycznie zarządza PC.",respect:"⚠️ Szanuj i bądź szanowany!",tc_title:"🎨 UTWÓRZ MOTYW",tc_name:"Nazwa motywu:",tc_bg:"Tło:",tc_card:"Karta:",tc_text:"Tekst:",tc_accent:"Akcent:",tc_border:"Obramowanie:",tc_save:"💾 Zapisz",tc_close:"❌ Zamknij",manifesto_title:"📜 MANIFEST",manifesto_text:"Ten program powstał, aby jednoczyć, a nie konkurować. Nie usuwaj podpisu :))",notif_saved:"✅ Zapisano",notif_deleted:"🗑️ Usunięto",notif_mode:"⚡ Tryb",notif_smart_on:"🤖 Inteligentny ON",notif_smart_off:"🤖 Inteligentny OFF",notif_psave_on:"🔋 Oszczędzanie ON",notif_psave_off:"⚡ Normalny",notif_dnd_on:"🔕 Nie przeszkadzać ON",notif_dnd_off:"🔔 Nie przeszkadzać OFF",notif_hz:"⚡ Hz zastosowano",notif_night:"🌙 Tryb nocny",warn_theme_name:"Nazwa motywu wymagana!",warn_default:"Domyślnych motywów nie można usunąć!",sig_hint:"kliknij",adv_menu:"🛠️ Zaawansowane",adv_title:"🛠️ USTAWIENIA ZAAWANSOWANE",adv_warning:"💡 Ten obszar jest dla programistów.",dev_info_title:"Notatka dla programistów",dev_info_text:"Ten obszar dla programistów <b>DarkHeida</b>.",dev_empty_1:"PUSTY OBSZAR - 1",dev_empty_2:"PUSTY OBSZAR - 2",dev_empty_3:"PUSTY OBSZAR - 3",dev_empty_desc:"Ta karta jest pusta.",adv_gpu:"TRYB GPU",adv_power:"ZARZĄDZANIE ENERGIĄ",adv_standby:"MODERN STANDBY",adv_keys:"PRZYPISANIE KLAWISZY",adv_soon:"⏳ WKRÓTCE",adv_footer:"⏳ Zostanie dodane przez programistów"},

SV:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Disk",net:"🌐 Nätverk",app_title:"DarkHeida",menu_perf:"⚡ Prestanda",menu_screen:"🖥️ Bildskärm",menu_battery:"🔋 Batteri",menu_keyboard:"⌨️ Tangentbord",menu_settings:"⚙️ Inställningar",menu_support:"🆘 Support",perf_mode:"⚡ PRESTANDALÄGE",quick_access:"⚡ SNABBÅTKOMST",btn_windows:"Windows",btn_silent:"Tyst",btn_performance:"Prestanda",btn_turbo:"Turbo",night_mode:"🌙 Nattläge",dnd:"🔕 Stör ej",power_save:"🔋 Energispar",screen_title:"🖥️ BILDSKÄRM",resolution:"📐 Upplösning",current_hz:"⚡ Aktuell Hz",refresh_rate:"Uppdateringsfrekvens",brightness:"☀️ Ljusstyrka",battery_title:"🔋 BATTERI",battery_percent:"🔋 Batteri %",battery_status:"⚡ Status",charge_limit:"🔌 Laddningsgräns",battery_health:"Hälsa: ⏳ Snart",keyboard_title:"⌨️ TANGENTBORD",effect:"✨ Effekt",kb_warning:"⚠️ Under utveckling.",kb_bright:"💡 Ljusstyrka",settings_title:"⚙️ INSTÄLLNINGAR",startup_title:"🔄 Kör vid start",startup_desc:"Startar automatiskt med Windows",theme:"🎨 Tema",create_theme:"🎨 SKAPA TEMA",delete_theme:"🗑️ TA BORT TEMA",language:"🌐 Språk",sys_info:"💻 System",support_title:"🆘 SUPPORT",report_issue:"🐛 Rapportera bugg (GitHub)",dev_notes:"📝 Utvecklarnoteringar",dev_notes_text:"Öppen källkod.<br><br>• Batterihälsa<br>• Fläkthastighet<br>• Tangentbordsbelysning<br>• Inaktivera GPU<br>• Volym<br>• Smart läge",uptime:"Drifttid",volume:"🔊 Volym",fan_title:"🌡️ FLÄKT & TEMP",fan_placeholder:"Läggs till av utvecklare.",support_box_title:"🚀 UTVECKLARSTÖD",support_box_text:"<span class='highlight'>DarkHeida</span> <span class='highlight'>under utveckling.</span><br><br>Snart. 🙏",smart_title:"🤖 SMART LÄGE",smart_desc:"Hanterar din PC automatiskt.",respect:"⚠️ Respektera och bli respekterad!",tc_title:"🎨 SKAPA TEMA",tc_name:"Temanamn:",tc_bg:"Bakgrund:",tc_card:"Kort:",tc_text:"Text:",tc_accent:"Accent:",tc_border:"Kant:",tc_save:"💾 Spara",tc_close:"❌ Stäng",manifesto_title:"📜 MANIFEST",manifesto_text:"Detta program skapades för att förena, inte för att tävla. Ta inte bort signaturen :))",notif_saved:"✅ Sparat",notif_deleted:"🗑️ Borttaget",notif_mode:"⚡ Läge",notif_smart_on:"🤖 Smart ON",notif_smart_off:"🤖 Smart OFF",notif_psave_on:"🔋 Energispar ON",notif_psave_off:"⚡ Normalt",notif_dnd_on:"🔕 Stör ej ON",notif_dnd_off:"🔔 Stör ej OFF",notif_hz:"⚡ Hz tillämpat",notif_night:"🌙 Nattläge",warn_theme_name:"Temanamn krävs!",warn_default:"Standardteman kan inte tas bort!",sig_hint:"klicka",adv_menu:"🛠️ Avancerat",adv_title:"🛠️ AVANCERADE INSTÄLLNINGAR",adv_warning:"💡 Detta område är för utvecklare.",dev_info_title:"Notering för utvecklare",dev_info_text:"Detta område är för <b>DarkHeida</b>-utvecklare.",dev_empty_1:"TOM OMRÅDE - 1",dev_empty_2:"TOM OMRÅDE - 2",dev_empty_3:"TOM OMRÅDE - 3",dev_empty_desc:"Detta kort lämnas tomt.",adv_gpu:"GPU-LÄGE",adv_power:"ENERGIHANTERING",adv_standby:"MODERN STANDBY",adv_keys:"TANGENTBORDSKOPPLINGAR",adv_soon:"⏳ SNART",adv_footer:"⏳ Läggs till av utvecklare"},

NO:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Disk",net:"🌐 Nettverk",app_title:"DarkHeida",menu_perf:"⚡ Ytelse",menu_screen:"🖥️ Skjerm",menu_battery:"🔋 Batteri",menu_keyboard:"⌨️ Tastatur",menu_settings:"⚙️ Innstillinger",menu_support:"🆘 Støtte",perf_mode:"⚡ YTELSESMODUS",quick_access:"⚡ HURTIGTILGANG",btn_windows:"Windows",btn_silent:"Stille",btn_performance:"Ytelse",btn_turbo:"Turbo",night_mode:"🌙 Nattmodus",dnd:"🔕 Ikke forstyrr",power_save:"🔋 Strømsparing",screen_title:"🖥️ SKJERM",resolution:"📐 Oppløsning",current_hz:"⚡ Gjeldende Hz",refresh_rate:"Oppdateringsfrekvens",brightness:"☀️ Lysstyrke",battery_title:"🔋 BATTERI",battery_percent:"🔋 Batteri %",battery_status:"⚡ Status",charge_limit:"🔌 Ladingsgrense",battery_health:"Helse: ⏳ Snart",keyboard_title:"⌨️ TASTATUR",effect:"✨ Effekt",kb_warning:"⚠️ Under utvikling.",kb_bright:"💡 Lysstyrke",settings_title:"⚙️ INNSTILLINGER",startup_title:"🔄 Kjør ved oppstart",startup_desc:"Starter automatisk med Windows",theme:"🎨 Tema",create_theme:"🎨 LAG TEMA",delete_theme:"🗑️ SLETT TEMA",language:"🌐 Språk",sys_info:"💻 System",support_title:"🆘 STØTTE",report_issue:"🐛 Rapporter feil (GitHub)",dev_notes:"📝 Utviklernotater",dev_notes_text:"Åpen kildekode.<br><br>• Batterihelse<br>• Viftehastighet<br>• Tastaturbelysning<br>• Deaktiver GPU<br>• Volum<br>• Smart modus",uptime:"Oppetid",volume:"🔊 Volum",fan_title:"🌡️ VIFTE & TEMP",fan_placeholder:"Legges til av utviklere.",support_box_title:"🚀 UTVIKLERSTØTTE",support_box_text:"<span class='highlight'>DarkHeida</span> <span class='highlight'>under utvikling.</span><br><br>Snart. 🙏",smart_title:"🤖 SMART MODUS",smart_desc:"Administrerer PC-en automatisk.",respect:"⚠️ Respekter og bli respektert!",tc_title:"🎨 LAG TEMA",tc_name:"Temanavn:",tc_bg:"Bakgrunn:",tc_card:"Kort:",tc_text:"Tekst:",tc_accent:"Aksent:",tc_border:"Kant:",tc_save:"💾 Lagre",tc_close:"❌ Lukk",manifesto_title:"📜 MANIFEST",manifesto_text:"Dette programmet ble laget for å forene, ikke konkurrere. Ikke fjern signaturen :))",notif_saved:"✅ Lagret",notif_deleted:"🗑️ Slettet",notif_mode:"⚡ Modus",notif_smart_on:"🤖 Smart ON",notif_smart_off:"🤖 Smart OFF",notif_psave_on:"🔋 Strømsparing ON",notif_psave_off:"⚡ Normal",notif_dnd_on:"🔕 Ikke forstyrr ON",notif_dnd_off:"🔔 Ikke forstyrr OFF",notif_hz:"⚡ Hz brukt",notif_night:"🌙 Nattmodus",warn_theme_name:"Temanavn kreves!",warn_default:"Standardtemaer kan ikke slettes!",sig_hint:"klikk",adv_menu:"🛠️ Avansert",adv_title:"🛠️ AVANSERTE INNSTILLINGER",adv_warning:"💡 Dette området er for utviklere.",dev_info_title:"Merknad for utviklere",dev_info_text:"Dette området for <b>DarkHeida</b>-utviklere.",dev_empty_1:"TOMT OMRÅDE - 1",dev_empty_2:"TOMT OMRÅDE - 2",dev_empty_3:"TOMT OMRÅDE - 3",dev_empty_desc:"Dette kortet er tomt.",adv_gpu:"GPU-MODUS",adv_power:"STRØMSTYRING",adv_standby:"MODERN STANDBY",adv_keys:"TASTATURBINDINGER",adv_soon:"⏳ SNART",adv_footer:"⏳ Legges til av utviklere"},

EL:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Δίσκος",net:"🌐 Δίκτυο",app_title:"DarkHeida",menu_perf:"⚡ Απόδοση",menu_screen:"🖥️ Οθόνη",menu_battery:"🔋 Μπαταρία",menu_keyboard:"⌨️ Πληκτρολόγιο",menu_settings:"⚙️ Ρυθμίσεις",menu_support:"🆘 Υποστήριξη",perf_mode:"⚡ ΛΕΙΤΟΥΡΓΙΑ ΑΠΟΔΟΣΗΣ",quick_access:"⚡ ΓΡΗΓΟΡΗ ΠΡΟΣΒΑΣΗ",btn_windows:"Windows",btn_silent:"Αθόρυβο",btn_performance:"Απόδοση",btn_turbo:"Turbo",night_mode:"🌙 Νυχτερινή λειτουργία",dnd:"🔕 Μην ενοχλείτε",power_save:"🔋 Εξοικονόμηση",screen_title:"🖥️ ΟΘΟΝΗ",resolution:"📐 Ανάλυση",current_hz:"⚡ Τρέχουσα Hz",refresh_rate:"Ρυθμός ανανέωσης",brightness:"☀️ Φωτεινότητα",battery_title:"🔋 ΜΠΑΤΑΡΙΑ",battery_percent:"🔋 Μπαταρία %",battery_status:"⚡ Κατάσταση",charge_limit:"🔌 Όριο φόρτισης",battery_health:"Υγεία: ⏳ Σύντομα",keyboard_title:"⌨️ ΠΛΗΚΤΡΟΛΟΓΙΟ",effect:"✨ Εφέ",kb_warning:"⚠️ Υπό ανάπτυξη.",kb_bright:"💡 Φωτεινότητα",settings_title:"⚙️ ΡΥΘΜΙΣΕΙΣ",startup_title:"🔄 Εκτέλεση κατά την εκκίνηση",startup_desc:"Αυτόματη εκκίνηση με Windows",theme:"🎨 Θέμα",create_theme:"🎨 ΝΕΟ ΘΕΜΑ",delete_theme:"🗑️ ΔΙΑΓΡΑΦΗ ΘΕΜΑΤΟΣ",language:"🌐 Γλώσσα",sys_info:"💻 Σύστημα",support_title:"🆘 ΥΠΟΣΤΗΡΙΞΗ",report_issue:"🐛 Αναφορά σφάλματος (GitHub)",dev_notes:"📝 Σημειώσεις προγραμματιστή",dev_notes_text:"Ανοιχτός κώδικας.<br><br>• Υγεία μπαταρίας<br>• Ταχύτητα ανεμιστήρα<br>• Φωτισμός πληκτρολογίου<br>• Απενεργοποίηση GPU<br>• Ένταση<br>• Έξυπνη λειτουργία",uptime:"Χρόνος λειτουργίας",volume:"🔊 Ένταση",fan_title:"🌡️ ΑΝΕΜΙΣΤΗΡΑΣ & ΘΕΡΜ",fan_placeholder:"Θα προστεθεί από προγραμματιστές.",support_box_title:"🚀 ΥΠΟΣΤΗΡΙΞΗ ΠΡΟΓΡΑΜΜΑΤΙΣΤΩΝ",support_box_text:"<span class='highlight'>DarkHeida</span> <span class='highlight'>υπό ανάπτυξη.</span><br><br>Σύντομα. 🙏",smart_title:"🤖 ΕΞΥΠΝΗ ΛΕΙΤΟΥΡΓΙΑ",smart_desc:"Διαχειρίζεται τον υπολογιστή αυτόματα.",respect:"⚠️ Σεβάσου και σεβάσου!",tc_title:"🎨 ΝΕΟ ΘΕΜΑ",tc_name:"Όνομα θέματος:",tc_bg:"Φόντο:",tc_card:"Κάρτα:",tc_text:"Κείμενο:",tc_accent:"Έμφαση:",tc_border:"Περίγραμμα:",tc_save:"💾 Αποθήκευση",tc_close:"❌ Κλείσιμο",manifesto_title:"📜 ΜΑΝΙΦΕΣΤΟ",manifesto_text:"Αυτό το πρόγραμμα δημιουργήθηκε για να ενώσει, όχι για να ανταγωνιστεί. Μην αφαιρέσετε την υπογραφή :))",notif_saved:"✅ Αποθηκεύτηκε",notif_deleted:"🗑️ Διαγράφηκε",notif_mode:"⚡ Λειτουργία",notif_smart_on:"🤖 Smart ON",notif_smart_off:"🤖 Smart OFF",notif_psave_on:"🔋 Εξοικονόμηση ON",notif_psave_off:"⚡ Κανονικό",notif_dnd_on:"🔕 Μην ενοχλείτε ON",notif_dnd_off:"🔔 Μην ενοχλείτε OFF",notif_hz:"⚡ Hz εφαρμόστηκε",notif_night:"🌙 Νυχτερινή λειτουργία",warn_theme_name:"Απαιτείται όνομα!",warn_default:"Τα προεπιλεγμένα θέματα δεν μπορούν να διαγραφούν!",sig_hint:"κλικ",adv_menu:"🛠️ Για προχωρημένους",adv_title:"🛠️ ΡΥΘΜΙΣΕΙΣ ΓΙΑ ΠΡΟΧΩΡΗΜΕΝΟΥΣ",adv_warning:"💡 Αυτή η περιοχή είναι για προγραμματιστές.",dev_info_title:"Σημείωση για προγραμματιστές",dev_info_text:"Αυτή η περιοχή για προγραμματιστές του <b>DarkHeida</b>.",dev_empty_1:"ΚΕΝΗ ΠΕΡΙΟΧΗ - 1",dev_empty_2:"ΚΕΝΗ ΠΕΡΙΟΧΗ - 2",dev_empty_3:"ΚΕΝΗ ΠΕΡΙΟΧΗ - 3",dev_empty_desc:"Αυτή η κάρτα είναι κενή.",adv_gpu:"ΛΕΙΤΟΥΡΓΙΑ GPU",adv_power:"ΔΙΑΧΕΙΡΙΣΗ ΕΝΕΡΓΕΙΑΣ",adv_standby:"MODERN STANDBY",adv_keys:"ΑΝΑΘΕΣΗ ΠΛΗΚΤΡΩΝ",adv_soon:"⏳ ΣΥΝΤΟΜΑ",adv_footer:"⏳ Θα προστεθεί από προγραμματιστές"},

HE:{cpu:"🧠 מעבד",ram:"💾 זיכרון",disk:"💿 דיסק",net:"🌐 רשת",app_title:"DarkHeida",menu_perf:"⚡ ביצועים",menu_screen:"🖥️ תצוגה",menu_battery:"🔋 סוללה",menu_keyboard:"⌨️ מקלדת",menu_settings:"⚙️ הגדרות",menu_support:"🆘 תמיכה",perf_mode:"⚡ מצב ביצועים",quick_access:"⚡ גישה מהירה",btn_windows:"Windows",btn_silent:"שקט",btn_performance:"ביצועים",btn_turbo:"טורבו",night_mode:"🌙 מצב לילה",dnd:"🔕 אל תפריע",power_save:"🔋 חיסכון",screen_title:"🖥️ תצוגה",resolution:"📐 רזולוציה",current_hz:"⚡ Hz נוכחי",refresh_rate:"קצב רענון",brightness:"☀️ בהירות",battery_title:"🔋 סוללה",battery_percent:"🔋 סוללה %",battery_status:"⚡ מצב",charge_limit:"🔌 מגבלת טעינה",battery_health:"בריאות: ⏳ בקרוב",keyboard_title:"⌨️ מקלדת",effect:"✨ אפקט",kb_warning:"⚠️ בפיתוח.",kb_bright:"💡 בהירות",settings_title:"⚙️ הגדרות",startup_title:"🔄 הפעלה בעת האתחול",startup_desc:"הפעלה אוטומטית עם Windows",theme:"🎨 ערכת נושא",create_theme:"🎨 צור ערכת נושא",delete_theme:"🗑️ מחק ערכת נושא",language:"🌐 שפה",sys_info:"💻 מערכת",support_title:"🆘 תמיכה",report_issue:"🐛 דווח על באג (GitHub)",dev_notes:"📝 הערות מפתח",dev_notes_text:"קוד פתוח.<br><br>• בריאות סוללה<br>• מהירות מאוורר<br>• תאורת מקלדת<br>• השבת GPU<br>• עוצמת קול<br>• מצב חכם",uptime:"זמן פעילות",volume:"🔊 עוצמת קול",fan_title:"🌡️ מאוורר וטמפרטורה",fan_placeholder:"יתווסף על ידי מפתחים.",support_box_title:"🚀 תמיכת מפתחים",support_box_text:"<span class='highlight'>DarkHeida</span> <span class='highlight'>בפיתוח.</span><br><br>בקרוב. 🙏",smart_title:"🤖 מצב חכם",smart_desc:"מנהל את המחשב אוטומטית.",respect:"⚠️ כבד והכובד!",tc_title:"🎨 צור ערכת נושא",tc_name:"שם ערכת הנושא:",tc_bg:"רקע:",tc_card:"כרטיס:",tc_text:"טקסט:",tc_accent:"הדגשה:",tc_border:"גבול:",tc_save:"💾 שמור",tc_close:"❌ סגור",manifesto_title:"📜 מניפסט",manifesto_text:"תוכנית זו נוצרה כדי לאחד, לא כדי להתחרות. אל תסיר את החתימה :))",notif_saved:"✅ נשמר",notif_deleted:"🗑️ נמחק",notif_mode:"⚡ מצב",notif_smart_on:"🤖 חכם ON",notif_smart_off:"🤖 חכם OFF",notif_psave_on:"🔋 חיסכון ON",notif_psave_off:"⚡ רגיל",notif_dnd_on:"🔕 אל תפריע ON",notif_dnd_off:"🔔 אל תפריע OFF",notif_hz:"⚡ Hz הוחל",notif_night:"🌙 מצב לילה",warn_theme_name:"נדרש שם!",warn_default:"לא ניתן למחוק ערכות ברירת מחדל!",sig_hint:"לחץ",adv_menu:"🛠️ מתקדם",adv_title:"🛠️ הגדרות מתקדמות",adv_warning:"💡 אזור זה מיועד למפתחים.",dev_info_title:"הערה למפתחים",dev_info_text:"אזור זה למפתחי <b>DarkHeida</b>.",dev_empty_1:"אזור ריק - 1",dev_empty_2:"אזור ריק - 2",dev_empty_3:"אזור ריק - 3",dev_empty_desc:"כרטיס זה ריק.",adv_gpu:"מצב GPU",adv_power:"ניהול חשמל",adv_standby:"MODERN STANDBY",adv_keys:"שיוך מקשים",adv_soon:"⏳ בקרוב",adv_footer:"⏳ יתווסף על ידי מפתחים"},

TH:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 ดิสก์",net:"🌐 เครือข่าย",app_title:"DarkHeida",menu_perf:"⚡ ประสิทธิภาพ",menu_screen:"🖥️ จอแสดงผล",menu_battery:"🔋 แบตเตอรี่",menu_keyboard:"⌨️ คีย์บอร์ด",menu_settings:"⚙️ การตั้งค่า",menu_support:"🆘 การสนับสนุน",perf_mode:"⚡ โหมดประสิทธิภาพ",quick_access:"⚡ การเข้าถึงด่วน",btn_windows:"Windows",btn_silent:"เงียบ",btn_performance:"ประสิทธิภาพ",btn_turbo:"เทอร์โบ",night_mode:"🌙 โหมดกลางคืน",dnd:"🔕 ห้ามรบกวน",power_save:"🔋 ประหยัดพลังงาน",screen_title:"🖥️ จอแสดงผล",resolution:"📐 ความละเอียด",current_hz:"⚡ Hz ปัจจุบัน",refresh_rate:"อัตราการรีเฟรช",brightness:"☀️ ความสว่าง",battery_title:"🔋 แบตเตอรี่",battery_percent:"🔋 แบตเตอรี่ %",battery_status:"⚡ สถานะ",charge_limit:"🔌 ขีดจำกัดการชาร์จ",battery_health:"สุขภาพ: ⏳ เร็วๆ นี้",keyboard_title:"⌨️ คีย์บอร์ด",effect:"✨ เอฟเฟกต์",kb_warning:"⚠️ อยู่ระหว่างการพัฒนา",kb_bright:"💡 ความสว่าง",settings_title:"⚙️ การตั้งค่า",startup_title:"🔄 เรียกใช้เมื่อเริ่มต้น",startup_desc:"เริ่มต้นอัตโนมัติกับ Windows",theme:"🎨 ธีม",create_theme:"🎨 สร้างธีม",delete_theme:"🗑️ ลบธีม",language:"🌐 ภาษา",sys_info:"💻 ระบบ",support_title:"🆘 การสนับสนุน",report_issue:"🐛 รายงานข้อบกพร่อง (GitHub)",dev_notes:"📝 บันทึกนักพัฒนา",dev_notes_text:"โอเพนซอร์ส<br><br>• สุขภาพแบตเตอรี่<br>• ความเร็วพัดลม<br>• ไฟคีย์บอร์ด<br>• ปิด GPU<br>• ระดับเสียง<br>• โหมดอัจฉริยะ",uptime:"เวลาทำงาน",volume:"🔊 ระดับเสียง",fan_title:"🌡️ พัดลมและอุณหภูมิ",fan_placeholder:"จะถูกเพิ่มโดยนักพัฒนา",support_box_title:"🚀 การสนับสนุนนักพัฒนา",support_box_text:"<span class='highlight'>DarkHeida</span> อยู่<span class='highlight'>ระหว่างการพัฒนา</span><br><br>เร็วๆ นี้ 🙏",smart_title:"🤖 โหมดอัจฉริยะ",smart_desc:"จัดการพีซีอัตโนมัติ",respect:"⚠️ เคารพและได้รับความเคารพ!",tc_title:"🎨 สร้างธีม",tc_name:"ชื่อธีม:",tc_bg:"พื้นหลัง:",tc_card:"การ์ด:",tc_text:"ข้อความ:",tc_accent:"เน้น:",tc_border:"ขอบ:",tc_save:"💾 บันทึก",tc_close:"❌ ปิด",manifesto_title:"📜 แถลงการณ์",manifesto_text:"โปรแกรมนี้สร้างขึ้นเพื่อรวมเป็นหนึ่ง โปรดอย่าลบลายเซ็น :))",notif_saved:"✅ บันทึกแล้ว",notif_deleted:"🗑️ ลบแล้ว",notif_mode:"⚡ โหมด",notif_smart_on:"🤖 สมาร์ท ON",notif_smart_off:"🤖 สมาร์ท OFF",notif_psave_on:"🔋 ประหยัด ON",notif_psave_off:"⚡ ปกติ",notif_dnd_on:"🔕 ห้ามรบกวน ON",notif_dnd_off:"🔔 ห้ามรบกวน OFF",notif_hz:"⚡ ใช้ Hz แล้ว",notif_night:"🌙 โหมดกลางคืน",warn_theme_name:"ต้องระบุชื่อธีม!",warn_default:"ไม่สามารถลบธีมเริ่มต้นได้!",sig_hint:"คลิก",adv_menu:"🛠️ ขั้นสูง",adv_title:"🛠️ การตั้งค่าขั้นสูง",adv_warning:"💡 พื้นที่นี้สำหรับนักพัฒนา",dev_info_title:"หมายเหตุสำหรับนักพัฒนา",dev_info_text:"พื้นที่นี้สำหรับนักพัฒนา <b>DarkHeida</b>",dev_empty_1:"พื้นที่ว่าง - 1",dev_empty_2:"พื้นที่ว่าง - 2",dev_empty_3:"พื้นที่ว่าง - 3",dev_empty_desc:"การ์ดนี้ว่างเปล่า",adv_gpu:"โหมด GPU",adv_power:"การจัดการพลังงาน",adv_standby:"MODERN STANDBY",adv_keys:"การกำหนดปุ่ม",adv_soon:"⏳ เร็วๆ นี้",adv_footer:"⏳ จะถูกเพิ่มโดยนักพัฒนา"},

VI:{cpu:"🧠 CPU",ram:"💾 RAM",disk:"💿 Đĩa",net:"🌐 Mạng",app_title:"DarkHeida",menu_perf:"⚡ Hiệu suất",menu_screen:"🖥️ Màn hình",menu_battery:"🔋 Pin",menu_keyboard:"⌨️ Bàn phím",menu_settings:"⚙️ Cài đặt",menu_support:"🆘 Hỗ trợ",perf_mode:"⚡ CHẾ ĐỘ HIỆU SUẤT",quick_access:"⚡ TRUY CẬP NHANH",btn_windows:"Windows",btn_silent:"Im lặng",btn_performance:"Hiệu suất",btn_turbo:"Turbo",night_mode:"🌙 Chế độ ban đêm",dnd:"🔕 Không làm phiền",power_save:"🔋 Tiết kiệm",screen_title:"🖥️ MÀN HÌNH",resolution:"📐 Độ phân giải",current_hz:"⚡ Hz hiện tại",refresh_rate:"Tần số làm mới",brightness:"☀️ Độ sáng",battery_title:"🔋 PIN",battery_percent:"🔋 Pin %",battery_status:"⚡ Trạng thái",charge_limit:"🔌 Giới hạn sạc",battery_health:"Sức khỏe: ⏳ Sắp có",keyboard_title:"⌨️ BÀN PHÍM",effect:"✨ Hiệu ứng",kb_warning:"⚠️ Đang phát triển.",kb_bright:"💡 Độ sáng",settings_title:"⚙️ CÀI ĐẶT",startup_title:"🔄 Chạy khi khởi động",startup_desc:"Tự động khởi động cùng Windows",theme:"🎨 Chủ đề",create_theme:"🎨 TẠO CHỦ ĐỀ",delete_theme:"🗑️ XÓA CHỦ ĐỀ",language:"🌐 Ngôn ngữ",sys_info:"💻 Hệ thống",support_title:"🆘 HỖ TRỢ",report_issue:"🐛 Báo cáo lỗi (GitHub)",dev_notes:"📝 Ghi chú nhà phát triển",dev_notes_text:"Mã nguồn mở.<br><br>• Sức khỏe pin<br>• Tốc độ quạt<br>• Đèn bàn phím<br>• Vô hiệu hóa GPU<br>• Âm lượng<br>• Chế độ thông minh",uptime:"Thời gian hoạt động",volume:"🔊 Âm lượng",fan_title:"🌡️ QUẠT & NHIỆT ĐỘ",fan_placeholder:"Sẽ được thêm bởi nhà phát triển.",support_box_title:"🚀 HỖ TRỢ NHÀ PHÁT TRIỂN",support_box_text:"<span class='highlight'>DarkHeida</span> đang <span class='highlight'>được phát triển.</span><br><br>Sớm có sẵn. 🙏",smart_title:"🤖 CHẾ ĐỘ THÔNG MINH",smart_desc:"Quản lý PC tự động.",respect:"⚠️ Tôn trọng và được tôn trọng!",tc_title:"🎨 TẠO CHỦ ĐỀ",tc_name:"Tên chủ đề:",tc_bg:"Nền:",tc_card:"Thẻ:",tc_text:"Văn bản:",tc_accent:"Nhấn mạnh:",tc_border:"Viền:",tc_save:"💾 Lưu",tc_close:"❌ Đóng",manifesto_title:"📜 TUYÊN NGÔN",manifesto_text:"Chương trình này được tạo ra để thống nhất, không phải để cạnh tranh. Đừng xóa chữ ký :))",notif_saved:"✅ Đã lưu",notif_deleted:"🗑️ Đã xóa",notif_mode:"⚡ Chế độ",notif_smart_on:"🤖 Thông minh ON",notif_smart_off:"🤖 Thông minh OFF",notif_psave_on:"🔋 Tiết kiệm ON",notif_psave_off:"⚡ Bình thường",notif_dnd_on:"🔕 Không làm phiền ON",notif_dnd_off:"🔔 Không làm phiền OFF",notif_hz:"⚡ Đã áp dụng Hz",notif_night:"🌙 Chế độ ban đêm",warn_theme_name:"Cần tên chủ đề!",warn_default:"Không thể xóa chủ đề mặc định!",sig_hint:"nhấp",adv_menu:"🛠️ Nâng cao",adv_title:"🛠️ CÀI ĐẶT NÂNG CAO",adv_warning:"💡 Khu vực này dành cho nhà phát triển.",dev_info_title:"Ghi chú cho nhà phát triển",dev_info_text:"Khu vực này cho nhà phát triển <b>DarkHeida</b>.",dev_empty_1:"KHU VỰC TRỐNG - 1",dev_empty_2:"KHU VỰC TRỐNG - 2",dev_empty_3:"KHU VỰC TRỐNG - 3",dev_empty_desc:"Thẻ này được để trống.",adv_gpu:"CHẾ ĐỘ GPU",adv_power:"QUẢN LÝ NGUỒN",adv_standby:"MODERN STANDBY",adv_keys:"GÁN PHÍM",adv_soon:"⏳ SẮP CÓ",adv_footer:"⏳ Sẽ được thêm bởi nhà phát triển"}
};

let THEMES = {...DEFAULT_THEMES};
let currentTheme = "dark";
let currentLang = "TR";
let smartOn = false, dndOn = false, pSaveOn = false;

function applyTheme(name){
    const t = THEMES[name]; if(!t) return;
    const r = document.documentElement.style;
    r.setProperty('--bg',t.bg); r.setProperty('--sidebar',t.sidebar||t.card);
    r.setProperty('--card',t.card); r.setProperty('--text',t.text);
    r.setProperty('--muted',t.muted); r.setProperty('--accent',t.accent);
    r.setProperty('--border',t.border);
    currentTheme = name;
    const sel = document.getElementById('theme-select');
    if(sel) sel.value = name;
}

function renderThemes(){
    const sel = document.getElementById('theme-select');
    const labels = THEME_LABELS[currentLang] || THEME_LABELS.TR;
    sel.innerHTML = '';
    Object.keys(THEMES).forEach(k=>{
        const o = document.createElement('option');
        o.value = k;
        o.textContent = labels[k] || k.replace('custom_','⭐ ').replace(/_/g,' ');
        sel.appendChild(o);
    });
    sel.value = currentTheme;
}

function saveTheme(name){ applyTheme(name); callApi('save_config',name,currentLang); }

function applyLang(code){
    const d = DIL[code] || DIL.TR;
    currentLang = code;
    const sT = (id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v;};
    const sH = (id,v)=>{const e=document.getElementById(id);if(e)e.innerHTML=v;};
    sT('menu-perf',d.menu_perf);sT('menu-screen',d.menu_screen);
    sT('menu-battery',d.menu_battery);sT('menu-keyboard',d.menu_keyboard);
    sT('menu-settings',d.menu_settings);sT('menu-support',d.menu_support);
    sT('menu-advanced',d.adv_menu||'🛠️ Gelişmiş');
    sT('cpu-label',d.cpu);sT('ram-label',d.ram);
    sT('disk-label',d.disk);sT('net-label',d.net);
    sT('perf-title',d.perf_mode);sT('quick-title',d.quick_access);
    sT('mode-windows',d.btn_windows);sT('mode-silent',d.btn_silent);
    sT('mode-performance',d.btn_performance);sT('mode-turbo',d.btn_turbo);
    sT('btn-night',d.night_mode);sT('btn-dnd',d.dnd);sT('btn-save',d.power_save);
    sT('screen-title',d.screen_title);sT('res-label',d.resolution);
    sT('hz-title',d.current_hz);sT('hz-label',d.refresh_rate);sT('bright-label',d.brightness);
    sT('battery-title',d.battery_title);sT('batt-pct-label',d.battery_percent);
    sT('batt-status-label',d.battery_status);sT('charge-label',d.charge_limit);
    sT('battery-health',d.battery_health);sT('keyboard-title',d.keyboard_title);
    sT('effect-label',d.effect);sT('kb-warning',d.kb_warning);sT('kb-bright-label',d.kb_bright);
    sT('advanced-title',d.adv_title||'🛠️ GELİŞMİŞ AYARLAR');
    sT('adv-warning',d.adv_warning||'💡 Bu alan geliştiriciler içindir.');
    sT('dev-info-title',d.dev_info_title||'Geliştiriciler İçin Not');
    sH('dev-info-text',d.dev_info_text||'Bu alan geliştiriciler içindir.');
    sT('dev-empty-title-1',d.dev_empty_1||'BOŞ ALAN - 1');
    sT('dev-empty-title-2',d.dev_empty_2||'BOŞ ALAN - 2');
    sT('dev-empty-title-3',d.dev_empty_3||'BOŞ ALAN - 3');
    sT('dev-empty-desc-1',d.dev_empty_desc||'Bu kart boş bırakılmıştır.');
    sT('dev-empty-desc-2',d.dev_empty_desc||'Bu kart boş bırakılmıştır.');
    sT('dev-empty-desc-3',d.dev_empty_desc||'Bu kart boş bırakılmıştır.');
    sT('adv-gpu-title',d.adv_gpu||'GPU MODU');
    sT('adv-power-title',d.adv_power||'GÜÇ YÖNETİMİ');
    sT('adv-standby-title',d.adv_standby||'MODERN STANDBY');
    sT('adv-keys-title',d.adv_keys||'TUŞ ATAMALARI');
    document.querySelectorAll('.adv-badge').forEach(b=>{b.textContent = d.adv_soon||'⏳ YAKINDA';});
    document.querySelectorAll('.adv-footer').forEach(f=>{f.textContent = d.adv_footer||'⏳ Geliştiriciler tarafından eklenecektir';});
    sT('settings-title',d.settings_title);sT('theme-label',d.theme);
    sT('startup-title',d.startup_title);sT('startup-desc',d.startup_desc);
    sT('btn-create-theme',d.create_theme);sT('btn-del-theme',d.delete_theme);
    sT('lang-label',d.language);sT('sys-label',d.sys_info);
    sT('support-title',d.support_title);sT('support-box-title',d.support_box_title);
    sH('support-box-text',d.support_box_text);sT('github-btn',d.report_issue);
    sT('dev-notes-label',d.dev_notes);sH('dev-notes-text',d.dev_notes_text);
    sT('uptime-label',d.uptime);sT('volume-label',d.volume);
    sT('fan-title',d.fan_title);sT('fan-placeholder',d.fan_placeholder);
    sT('smart-mode-title',d.smart_title);sT('smart-mode-desc',d.smart_desc);
    sH('respect-warning',d.respect);
    sT('tc-title',d.tc_title);sT('tc-name-lbl',d.tc_name);sT('tc-bg-lbl',d.tc_bg);
    sT('tc-card-lbl',d.tc_card);sT('tc-text-lbl',d.tc_text);sT('tc-accent-lbl',d.tc_accent);
    sT('tc-border-lbl',d.tc_border);sT('tc-save',d.tc_save);sT('tc-close',d.tc_close);
    sT('manifesto-title',d.manifesto_title);sT('manifesto-text',d.manifesto_text);
    sT('sig-hint',d.sig_hint||'tıkla');
    renderThemes(); renderLangs();
}

function renderLangs(){
    const sel = document.getElementById('lang-select');
    sel.innerHTML = '';
    Object.keys(DIL).forEach(c=>{
        const o = document.createElement('option'); o.value = c; o.textContent = c;
        sel.appendChild(o);
    });
    sel.value = currentLang;
}

function saveLanguage(code){ applyLang(code); callApi('save_config',currentTheme,code); }

function showPage(name,btn){
    document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
    document.querySelectorAll('.menu-btn').forEach(b=>b.classList.remove('active'));
    document.getElementById('page-'+name).classList.add('active');
    if(btn) btn.classList.add('active');
}

let _notifT;
function notify(msg){
    const n = document.getElementById('notification');
    n.textContent = msg; n.style.display = 'block';
    clearTimeout(_notifT);
    _notifT = setTimeout(()=>n.style.display='none',2500);
}

async function callApi(fn,...args){
    try{
        if(!window.pywebview || !pywebview.api) return null;
        return await pywebview.api[fn](...args);
    }catch(e){ console.warn('API:',fn,e); return null; }
}

async function setPowerMode(mode){
    document.querySelectorAll('.mode-btn').forEach(b=>b.classList.remove('selected'));
    const btn = document.getElementById('mode-'+mode);
    if(btn) btn.classList.add('selected');
    const ok = await callApi('change_power_mode',mode);
    notify(ok ? `${DIL[currentLang].notif_mode} ${mode.toUpperCase()}` : '❌');
}
function toggleSmartMode(){
    smartOn = !smartOn;
    document.getElementById('smart-toggle').classList.toggle('active',smartOn);
    notify(smartOn ? DIL[currentLang].notif_smart_on : DIL[currentLang].notif_smart_off);
}
async function toggleStartup(){
    const toggle = document.getElementById('startup-toggle');
    const newState = !toggle.classList.contains('active');
    toggle.classList.toggle('active', newState);
    const ok = await callApi('toggle_startup', newState);
    if(!ok){
        toggle.classList.toggle('active', !newState);
        notify('❌ ' + (currentLang === 'TR' ? 'Değiştirilemedi' : 'Failed'));
    } else {
        notify(newState ? '🔄 ' + DIL[currentLang].startup_title : '🔄 OFF');
    }
}
async function openNightMode(){ await callApi('open_night_mode'); notify(DIL[currentLang].notif_night); }
async function toggleDND(){
    dndOn = !dndOn;
    document.getElementById('btn-dnd').classList.toggle('active',dndOn);
    await callApi('toggle_dnd');
    notify(dndOn ? DIL[currentLang].notif_dnd_on : DIL[currentLang].notif_dnd_off);
}
async function togglePowerSave(){
    pSaveOn = !pSaveOn;
    document.getElementById('btn-save').classList.toggle('active',pSaveOn);
    await callApi('toggle_power_save',pSaveOn);
    notify(pSaveOn ? DIL[currentLang].notif_psave_on : DIL[currentLang].notif_psave_off);
}
async function setRefreshRate(hz){
    const ok = await callApi('change_refresh_rate',hz);
    notify(ok ? DIL[currentLang].notif_hz : '❌');
}
function openGithub(){ callApi('open_github'); }

const _dbT = {};
function debounce(k,fn,delay=150){ clearTimeout(_dbT[k]); _dbT[k]=setTimeout(fn,delay); }

function updateSlider(el){
    const v = el.value;
    const map = {
        'volume-slider':['volume-bg','volume-value','change_volume'],
        'bright-slider':['bright-bg','bright-value','change_brightness'],
        'charge-slider':['charge-bg','charge-value','change_charge_limit'],
        'kb-bright-slider':['kb-bright-bg','kb-bright-value',null]
    };
    const [bg,val,api] = map[el.id]||[];
    if(bg){
        const min = parseFloat(el.min)||0;
        const max = parseFloat(el.max)||100;
        const pct = ((parseFloat(v)-min)/(max-min))*100;
        document.getElementById(bg).style.width = pct + '%';
    }
    if(val) document.getElementById(val).textContent = '%'+v;
    if(api) debounce(el.id,()=>callApi(api,parseInt(v)));
}

async function updateStats(){
    const s = await callApi('get_stats'); if(!s) return;
    const $ = id=>document.getElementById(id);
    $('cpu-value').textContent = '%'+s.cpu; $('cpu-bar').style.width = s.cpu+'%';
    $('ram-value').textContent = '%'+s.ram; $('ram-bar').style.width = s.ram+'%';
    $('disk-value').textContent = '%'+s.disk; $('disk-bar').style.width = s.disk+'%';
    $('net-value').textContent = s.net;
    $('net-bar').style.width = (s.net_pct||0)+'%';
    $('uptime').textContent = s.uptime;
    $('resolution-value').textContent = s.resolution;
    $('hz-value').textContent = s.hz+' Hz';
    $('battery-percent').textContent = s.battery+' %';
    $('battery-status').textContent = s.battery_status;
}

async function loadSysInfo(){
    const info = await callApi('get_system_info'); if(!info) return;
    document.getElementById('sys-info').innerHTML =
        `<b>CPU:</b> ${info.cpu}<br><b>RAM:</b> ${info.ram}<br><b>GPU:</b> ${info.gpu}<br><b>OS:</b> ${info.os}`;
}

async function loadCustomThemes(){
    const c = await callApi('get_custom_themes'); if(!c) return;
    Object.entries(c).forEach(([k,t])=>{ THEMES[k]=t; });
}

async function loadConfig(){
    const cfg = await callApi('get_config'); if(!cfg) return;
    if(cfg.theme && THEMES[cfg.theme]) applyTheme(cfg.theme);
    applyLang(cfg.language || 'TR');
    if(cfg.brightness!=null){const s=document.getElementById('bright-slider');s.value=cfg.brightness;updateSlider(s);}
    if(cfg.volume!=null){const s=document.getElementById('volume-slider');s.value=cfg.volume;updateSlider(s);}
    if(cfg.charge_limit!=null){const s=document.getElementById('charge-slider');s.value=cfg.charge_limit;updateSlider(s);}
    if(cfg.power_save){ pSaveOn = true; document.getElementById('btn-save').classList.add('active'); }
    const startup = await callApi('get_startup_status');
    if(startup) document.getElementById('startup-toggle').classList.add('active');
}

function openThemeCreator(){ document.getElementById('theme-creator').classList.add('show'); }
function closeThemeCreator(){ document.getElementById('theme-creator').classList.remove('show'); }
function openManifesto(){ document.getElementById('manifesto').classList.add('show'); }
function closeManifesto(){ document.getElementById('manifesto').classList.remove('show'); }

document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){ closeManifesto(); closeThemeCreator(); }
});
document.querySelectorAll('.modal-overlay').forEach(ov=>{
    ov.addEventListener('click',e=>{ if(e.target===ov) ov.classList.remove('show'); });
});

async function saveNewTheme(){
    const name = document.getElementById('new-theme-name').value.trim();
    const d = DIL[currentLang];
    if(!name) return alert(d.warn_theme_name);
    const key = 'custom_'+name.toLowerCase().replace(/\s+/g,'_');
    const theme = {
        bg:document.getElementById('new-theme-bg').value,
        sidebar:document.getElementById('new-theme-bg').value,
        card:document.getElementById('new-theme-card').value,
        text:document.getElementById('new-theme-text').value,
        muted:document.getElementById('new-theme-text').value+'AA',
        accent:document.getElementById('new-theme-accent').value,
        border:document.getElementById('new-theme-border').value
    };
    THEMES[key] = theme;
    if(!THEME_LABELS[currentLang]) THEME_LABELS[currentLang] = {};
    THEME_LABELS[currentLang][key] = '⭐ '+name;
    await callApi('save_new_theme',key,theme);
    renderThemes(); applyTheme(key); closeThemeCreator();
    notify(d.notif_saved+': '+name);
}

async function deleteSelectedTheme(){
    const key = document.getElementById('theme-select').value;
    const d = DIL[currentLang];
    if(!key.startsWith('custom_')) return alert(d.warn_default);
    await callApi('delete_custom_theme',key);
    delete THEMES[key];
    if(THEME_LABELS[currentLang]) delete THEME_LABELS[currentLang][key];
    currentTheme = 'dark';
    renderThemes(); applyTheme('dark');
    notify(d.notif_deleted);
}

async function init(){
    applyTheme('dark');
    await loadCustomThemes();
    renderThemes();
    await loadConfig();
    renderLangs();
    await loadSysInfo();
    await updateStats();
    setInterval(updateStats,1000);
}

/* ==== İMZA KORUMASI ==== */
(function(){
    const _fullSig = decodeURIComponent(escape(atob("WWFyZMSxbVNldmVy"))) + atob("ICYgREg=");
    let _t = 0, _w = false;

    function inject(){
        if(!document.querySelector('meta[name="author"]')){
            const m = document.createElement('meta');
            m.name = 'author'; m.content = _fullSig;
            document.head.appendChild(m);
        }
        if(!document.body.getAttribute('data-author'))
            document.body.setAttribute('data-author', _fullSig);
        if(!document.getElementById('_dhsig')){
            const d = document.createElement('div');
            d.id = '_dhsig'; d.style.display = 'none';
            d.textContent = _fullSig;
            document.body.appendChild(d);
        }
    }

    function check(){
        const el = document.getElementById('signature');
        const txt = document.getElementById('signature-text');
        const ok = el && txt && txt.textContent.includes('DH');
        inject();
        if(!ok){
            _t++;
            const sb = document.querySelector('.sidebar');
            if(sb){
                const old = document.getElementById('signature');
                if(old) old.remove();
                const n = document.createElement('div');
                n.className = 'signature';
                n.id = 'signature';
                n.setAttribute('onclick','openManifesto()');
                n.innerHTML = '<div class="signature-text" id="signature-text">⚡ '+_fullSig+'</div>' +
                    '<div class="signature-hint"><span class="hint-text" id="sig-hint">tıkla</span>' +
                    '<span class="hint-arrow">▼</span></div>';
                sb.appendChild(n);
            }
            if(_t >= 3 && !_w){
                _w = true;
                const o = document.createElement('div');
                o.className = 'sig-warning-overlay';
                o.innerHTML = '<div class="sig-warning-box">' +
                    '<div class="sig-warning-icon">⚡</div>' +
                    '<div class="sig-warning-title">İMZAYA DOKUNMA!</div>' +
                    '<p class="sig-warning-text"><strong>DarkHeida</strong> açık kaynak kodludur.<br>Ama <strong>imzayı silmeyeceksiniz.</strong></p>' +
                    '<p class="sig-warning-quote">"Emeğe saygı göster, saygı gör."</p>' +
                    '<p class="sig-warning-sig">⚡ YardımSever &amp; DH</p>' +
                    '<button class="sig-warning-btn" onclick="this.closest(\'.sig-warning-overlay\').remove();window._r();">Tamam, Anladım ✓</button>' +
                    '</div>';
                document.body.appendChild(o);
                _t = 0;
            }
        } else { if(_t > 0) _t--; }
    }

    window._r = function(){ _w = false; _t = 0; };
    setInterval(check, 2000);
    setTimeout(check, 500);
})();

window.addEventListener('pywebviewready',init);
if(window.pywebview && pywebview.api) init();
</script>
</body>
</html>
"""

     #🤖 AKILLI MOD
     #Pil %20'nin altına düştü → Otomatik Sessiz moda geçer -->
     #Şarja takıldı → Otomatik Dengeli moda döner
     #CPU %90'ın üstüne çıktı → Otomatik Performans moduna geçer -->
     #Oyun açıldı → Otomatik Turbo moda geçer
     #Film izleniyor → Otomatik Sessiz moda geçer
     #Gece 23:00 oldu → Otomatik Gece moduna geçer

def create_tray_icon(api):
    """Sistem tepsisinde ikon oluşturur"""
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "darkheida.ico")

    try:
        image = Image.open(icon_path)
    except Exception:
        image = Image.new('RGB', (64, 64), color='#FF0033')

    def show_window(icon, item):
        """Pencereyi göster"""
        try:
            webview.windows[0].show()
            webview.windows[0].restore()
        except Exception:
            pass

    def set_mode(mode):
        """Mod değiştirme handler'ı"""

        def handler(icon, item):
            api.change_power_mode(mode)

        return handler

    def is_mode(mode):
        """Mod kontrolü (radio buton için)"""

        def check(item):
            return api.config.get("power_mode") == mode

        return check

    def toggle_startup(icon, item):
        """Başlangıçta çalıştır toggle"""
        current = api.get_startup_status()
        api.toggle_startup(not current)

    def is_startup(item):
        """Başlangıçta çalıştır kontrolü"""
        return api.get_startup_status()

    def restart_app(icon, item):
        """Programı yeniden başlat"""
        try:
            icon.stop()
            api.stop()
            webview.windows[0].destroy()
            os.execl(sys.executable, sys.executable, *sys.argv)
        except Exception:
            pass

    def open_support_page(icon, item):
        """Program içindeki Destek sayfasına git"""
        try:
            webview.windows[0].show()
            webview.windows[0].restore()
            webview.windows[0].evaluate_js("showPage('support', document.getElementById('menu-support'))")
        except Exception:
            pass

    def open_github_issue(icon, item):
        """GitHub issue sayfasını aç"""
        webbrowser.open("https://github.com/YardimSever/DarkHeida/issues/new")

    def quit_app(icon, item):
        """Programdan çık"""
        try:
            icon.stop()
        except Exception:
            pass
        try:
            api.stop()
        except Exception:
            pass
        try:
            webview.windows[0].destroy()
        except Exception:
            pass

    menu = pystray.Menu(
        pystray.MenuItem('🖥️ Pencereyi Göster', show_window, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('⚡ Hızlı Mod', pystray.Menu(
            pystray.MenuItem('Windows', set_mode('windows'), checked=is_mode('windows'), radio=True),
            pystray.MenuItem('Sessiz', set_mode('silent'), checked=is_mode('silent'), radio=True),
            pystray.MenuItem('Performans', set_mode('performance'), checked=is_mode('performance'), radio=True),
            pystray.MenuItem('Turbo', set_mode('turbo'), checked=is_mode('turbo'), radio=True),
        )),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('🔄 Başlangıçta Çalıştır', toggle_startup, checked=is_startup),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('🔄 Yeniden Başlat', restart_app),
        pystray.MenuItem('🆘 Destek', pystray.Menu(
            pystray.MenuItem('📄 Destek Sayfası', open_support_page),
            pystray.MenuItem('🐛 Hata Bildir', open_github_issue),
        )),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('❌ Çıkış', quit_app),
    )

    icon = pystray.Icon("DarkHeida", image, "DarkHeida", menu)
    icon.run()


def main():
    api = Api()
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "darkheida.ico")

    # Tepside ikon oluştur (ayrı thread'de)
    threading.Thread(target=create_tray_icon, args=(api,), daemon=True).start()

    webview.create_window(
        "DarkHeida",
        html=HTML,
        js_api=api,
        width=1100,
        height=750,
        min_size=(900, 600),
        resizable=True,
    )
    try:
        if os.path.exists(icon_path):
            try:
                webview.start(icon=icon_path)
            except TypeError:
                webview.start()
        else:
            webview.start()
    finally:
        api.stop()


if __name__ == "__main__":
    main()