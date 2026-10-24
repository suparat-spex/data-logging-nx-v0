"""
app.py - start the NX Logger GUI: application names, settings, config defaults, icon.

QSettings live under SPEX / "NX Logger". On the first run the settings of MQDAQ v6
(SPEX / MqttDaq6 for the window, SPEX / MqttWaveDAQ for the broker form) are copied
over once, so the window layout, last project and broker stay as they were.
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

from nxlogger import APP_NAME, ORG_NAME, config
from nxlogger.ui.icons import app_icon

OLD_SETTINGS = [(ORG_NAME, "MqttDaq6"), (ORG_NAME, "MqttWaveDAQ")]


def migrate_settings(s: QSettings):
    if s.value("migrated_from_mqdaq", False, type=bool):
        return
    for org, app in OLD_SETTINGS:
        old = QSettings(org, app)
        for k in old.allKeys():
            if not s.contains(k):
                s.setValue(k, old.value(k))
    s.setValue("migrated_from_mqdaq", True)


def apply_config_defaults(s: QSettings, cfg: config.Config):
    """config.ini gives the defaults; a value already chosen in the app wins."""
    for key in ("theme",):
        if not s.contains(key) and cfg.app.get(key):
            s.setValue(key, cfg.app[key])


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    s = QSettings()
    migrate_settings(s)
    apply_config_defaults(s, config.get())
    from nxlogger.ui.main_window import DaqWindow     # after QApplication: widgets need it
    w = DaqWindow()
    w.show()
    return app.exec()
