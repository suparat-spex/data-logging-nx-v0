"""NX Logger - MQTT data logger: live waveforms, alarms and recording from ESP32 DAQ nodes."""
import os

APP_NAME = "NX Logger"
ORG_NAME = "SPEX"
__version__ = "1.0.0"

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")  # before any pyqtgraph import: stop it picking PyQt6
