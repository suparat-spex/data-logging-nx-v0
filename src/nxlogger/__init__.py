"""NX Logger - data logger for tags from Modbus TCP / RTU, MQTT, OPC UA and simulated devices
(and ESP32 waveform nodes): live views, alarms, statistics and recording."""
import os

APP_NAME = "NX Logger"
ORG_NAME = "SPEX"
__version__ = "2.0.0"

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")  # before any pyqtgraph import: stop it picking PyQt6
