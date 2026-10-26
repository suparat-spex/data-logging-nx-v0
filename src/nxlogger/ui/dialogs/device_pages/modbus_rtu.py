"""Modbus RTU: RS-485 / RS-232 serial devices. The serial port box lists the ports found
on this computer (pyserial) and still takes any name (COM12, /dev/ttyUSB0 ...)."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QToolButton, QWidget

from nxlogger.ui.dialogs.device_pages.base import ConnectionPage
from nxlogger.ui.dialogs.device_pages.modbus_tcp import MODBUS_SECTION
from nxlogger.ui.theme.ui_kit import T, make_icon


def serial_ports() -> list[tuple[str, str]]:
    """(device, description) of the serial ports present; [] without pyserial."""
    try:
        from serial.tools import list_ports
        return [(p.device, p.description or "") for p in list_ports.comports()]
    except Exception:
        return []


class ModbusRtuPage(ConnectionPage):
    DRIVER = "modbus_rtu"
    SECTIONS = [("Serial port", ["port"]), ("Line", ["baudrate", "bytesize", "parity", "stopbits"]), MODBUS_SECTION]
    LABELS = {"port": "Port", "baudrate": "Baud rate (bit/s)"}
    CHOICES = {"parity": {"N": "None (N)", "E": "Even (E)", "O": "Odd (O)"}}

    def editor(self, f, value):
        if f["key"] != "port":
            return super().editor(f, value)
        box = QWidget(); h = QHBoxLayout(box); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
        self.port = QComboBox(); self.port.setEditable(True)
        self.port.setToolTip("Ports found on this computer - or type any port name")
        self._fill_ports(str(value))
        self.port.currentTextChanged.connect(lambda _: self.changed.emit())
        again = QToolButton(); again.setAutoRaise(True); again.setToolTip("Look for serial ports again")
        again.setIcon(make_icon("refresh", T["text"]))
        again.clicked.connect(lambda: self._fill_ports(self.port.currentText()))
        h.addWidget(self.port, 1); h.addWidget(again)
        self.port.row_widget = box                  # the form shows the combo + refresh button
        return "editable", self.port

    def _fill_ports(self, current: str):
        self.port.blockSignals(True)
        self.port.clear()
        for dev, desc in serial_ports():
            self.port.addItem(f"{dev}" + (f"  -  {desc}" if desc and desc != "n/a" else ""), dev)
        self.port.setEditText(current)
        self.port.blockSignals(False)

    def values(self):
        vals = super().values()
        i = self.port.findText(vals["port"])          # a listed entry: its device name only
        if i >= 0 and self.port.itemData(i):
            vals["port"] = self.port.itemData(i)
        return vals

    def summary(self, values):
        return (f"{values['port']} · {values['baudrate']} {values['bytesize']}{values['parity']}"
                f"{values['stopbits']} · unit {values['unit']}")

    def validate(self, values):
        return "" if values["port"] else "Choose or type the serial port."
