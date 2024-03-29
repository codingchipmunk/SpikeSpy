import cProfile
import copy
import sys
from dataclasses import dataclass, field
from datetime import datetime
from os import environ
from pathlib import Path
from typing import Any, List, Optional, Union

import matplotlib
import matplotlib.style as mplstyle
import neo
import numpy as np
import PySide6
import quantities as pq
from matplotlib.widgets import PolygonSelector
from neo.io import NixIO
from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject, Qt,
                            Signal, Slot)
from PySide6.QtGui import QAction, QColor, QShortcut, QKeySequence, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox, QDialog, QFileDialog, QFormLayout,
                               QHBoxLayout, QInputDialog, QMainWindow,
                               QMdiArea, QMdiSubWindow, QMenu, QMenuBar,
                               QPushButton, QSpinBox, QTableView, QVBoxLayout,
                               QWidget, QMessageBox)

from .APTrack_experiment_import import process_folder as open_aptrack
from .mng_file_selector import QNeoSelector
from .MultiTraceView import MultiTraceView
from .NeoSettingsView import NeoSettingsView
from .SingleTraceView import SingleTraceView
from .SpikeGroupTable import SpikeGroupTableView
from .TrackingView import TrackingView
from .UnitView import UnitView
from .ViewerState import ViewerState, prompt_for_neo_file, tracked_neuron_unit
from .EventView import EventView

mplstyle.use('fast')

class MdiView(QMainWindow):
    # signals
    loadFile = Signal(str, str)

    def __init__(
        self,
        parent: PySide6.QtWidgets.QWidget = None,
        state: ViewerState = None,
        **kwargs,
    ) -> None:
        super().__init__(parent)

        self.window_options = {
            # 'TraceAnnotation': TraceView,
            "MultiTrace": MultiTraceView,
            "UnitView": UnitView,
            "SpikeGroupTable": SpikeGroupTableView,
            "SingleTraceView": SingleTraceView,
            "Settings": NeoSettingsView,
            "TrackingView":TrackingView,
            "Data": QNeoSelector,
            "Events": EventView
        }
        self.cur_windows = []

        self.state = state or ViewerState(**kwargs)
        self.loadFile.connect(self.state.loadFile)

        self.mdi = QMdiArea()
        self.setCentralWidget(self.mdi)

        for k in ['MultiTrace','UnitView','SpikeGroupTable','SingleTraceView','TrackingView']:
            v = self.window_options[k]
            w = v(parent=self, state=self.state)
            w = self.mdi.addSubWindow(w)
            self.cur_windows.append(w)

        self.toolbar = self.addToolBar("")
        for k in self.window_options.keys():
            act = self.toolbar.addAction(k)
            act.triggered.connect(lambda *args, k=k: self.newWindow(k))

        # Menu bar
        self.menubar = self.menuBar()

        file_menu = self.menubar.addMenu("&File")
        file_menu.addAction(
            QAction("Open", self, shortcut="Ctrl+O", triggered=self.open)
        )
        file_menu.addAction(
            QAction("Save as nixio", self, shortcut="Ctrl+S", triggered=self.save_as)
        )
        file_menu.addAction(
            QAction("Export as csv", self, shortcut= "Ctrl+E", triggered=self.export_csv )
        )

        # key shortcuts
        self.profiler = cProfile.Profile()
        self.is_profiling = False
        self.shortcut_profile = QShortcut(QKeySequence(Qt.Key_F1),self)
        def profile():
            if self.is_profiling:
                self.profiler.disable()
                self.is_profiling=False
                nom = QFileDialog.getSaveFileName(self, "Export profile")
                if nom is None:

                    
                    return
                self.profiler.dump_stats(nom[0])
                self.profiler = cProfile.Profile() 
                import subprocess
                subprocess.run(["snakeviz",nom[0]]) 
                
            else:
                self.is_profiling=True
                self.profiler.enable()
        self.shortcut_profile.activated.connect(lambda : profile())

        self.shortcut_next = QShortcut(QKeySequence(Qt.Key_Down),self)
        self.shortcut_next.activated.connect(lambda: self.state.setStimNo(self.state.stimno + 1))
        
        self.shortcut_prev = QShortcut(QKeySequence(Qt.Key_Up), self)
        self.shortcut_prev.activated.connect(lambda:self.state.setStimNo(self.state.stimno - 1))

        self.shortcut_del = QShortcut(QKeySequence(Qt.Key_Backspace), self)
        self.shortcut_del.activated.connect(lambda: self.state.setUnit(None))
        
        
        self.move_mode = "snap"
        def move(dist=1):
            cur_point =  (
                    self.state.spike_groups[self.state.cur_spike_group].idx_arr[
                        self.state.stimno
                    ]
                )[0]
            if self.move_mode =="snap":
                from scipy.signal import find_peaks
                dpts = self.state.get_erp()[self.state.stimno]
                pts,_ = find_peaks(dpts)
                pts_down,_ = find_peaks(-1*dpts)
                pts = np.sort(np.hstack([pts,pts_down]).flatten())
                i = pts.searchsorted(cur_point)
                if pts[i] == cur_point:
                    dist = pts[i+dist] - cur_point
                else:
                    dist = pts[i+dist-1] - cur_point

            self.state.setUnit(
               cur_point
                + dist  # TODO: make method
            )
        self.shortcut_left = QShortcut(QKeySequence(Qt.Key_Left), self)
        self.shortcut_left.activated.connect(lambda:move(-1))
      
        self.shortcut_right = QShortcut(QKeySequence(Qt.Key_Right), self)
        self.shortcut_right.activated.connect(lambda:move(1))
        self.shortcut_snap = QShortcut(QKeySequence(Qt.Key_S),self)
        def toggle_snap():
            self.move_mode = None if self.move_mode=="snap" else "snap"
        self.shortcut_snap.activated.connect(toggle_snap)
        

       

    def export_csv(self):
        save_filename = QFileDialog.getSaveFileName(self, "Export")[0]
        from csv import writer
        with open(save_filename, 'w') as f:
            w = writer(f)
            w.writerow(['SpikeID','Stimulus_number','Latency (ms)','Timestamp(ms)']) 
            for i, sg in enumerate(self.state.spike_groups):
                for timestamp in sg.event:
                    stim_no = self.state.event_signal.searchsorted(timestamp)-1
                    latency = (timestamp - self.state.event_signal[stim_no]).rescale(pq.ms)
                    w.writerow([f'{i}', stim_no, latency.base, timestamp.rescale(pq.ms).base ])

        


    def newWindow(self, k):
        w = self.window_options[k](parent=self, state=self.state)
        w = self.mdi.addSubWindow(w)
        self.cur_windows.append(w)
        w.show()

    @Slot()
    def open(self, type=None):
        """
        triggered on file->open
        """
        fname,type = prompt_for_neo_file(type)
        self.loadFile.emit(fname, type)

    @Slot()
    def save_as(
        self,
    ):  # TODO: move out
        """
        triggered on file->save
        """
        fname = QFileDialog.getSaveFileName(self, "Save as", filter=".h5")[0]
        # s = neo.Segment()

        s = self.state.segment or neo.Segment()

        save_file(
            fname,
            self.state.spike_groups,
            s,
            event_signal=self.state.event_signal,
            signal_chan=self.state.analog_signal,
        )


def save_file(
    filename,
    spike_groups,
    data=None,
    metadata=None,
    event_signal=None,
    signal_chan=None,
):
    """
    Saves a file containing the spike groups
    """
    if metadata is None:
        metadata = {
            "current_user": environ.get("USERNAME", environ.get("USER")),
            "date": datetime.now(),
        }

    def create_event_signals():
        events = []

        # 1. create timestamps from them
        for i, sg in enumerate(spike_groups):
            ts = []
            for e, s in zip(event_signal, sg.idx_arr):
                if s is None:
                    continue
                s_in_sec = s[0] / signal_chan.sampling_rate
                ts.append(e + s_in_sec)
            events.append(
                neo.Event(
                    np.array(ts),
                    name=f"unit_{i}",
                    annotations=metadata,
                    units="s",
                )
            )  # TODO: add more details about the events

        return events

    from copy import deepcopy

    if data is not None:
        data2 = deepcopy(data)

        # remove all 'nix_names' which prevent saving the file
        for x in [*data2.analogsignals, *data2.events, data2]:
            if "nix_name" in x.annotations:
                del x.annotations["nix_name"]
        
        data2.analogsignals = [x.rescale("mV") for x in data2.analogsignals]
        

        # remove previous unit annotations
        data2.events = [x for x in data2.events if not x.name.startswith("unit_")]
    else:
        data2 = neo.Segment()

    for x in create_event_signals():
        data2.events.append(x)

    blk = neo.Block(name="main")
    blk.segments.append(data2)
    if Path(filename).exists():
        Path(filename).unlink()
    n = NixIO(filename, mode="rw")
    n.write_block(blk)
    n.close()


class EventHistoryView(QWidget):
    """
    #TODO: should show the recent history of the selected spike (in table?)
    """

    pass


def align_spikegroup(spikegroup, erp_arr):
    """given a spike group attempt to align using convolution - note this should go outside of the UI component"""
    # 1. get spike events as 2d matrix
    window = 200
    arr = [
        erp_arr[i, x[0] - window : x[0] + window]
        for i, x in enumerate(spikegroup.idx_arr)
    ]

    # 2. correlate event signals with eachother
    np.convolve(arr, arr, axis=-1)
    # 3. take n-best overlaps & create template

    # 4. align all other signals to this template.
    # TODO
    pass


def run():
    app = QApplication(sys.argv)
    icon_path = Path(sys.modules[__name__].__file__ ).parent.joinpath("ui/icon.svg")
    app.setWindowIcon(QIcon(QPixmap(str(icon_path))))
    # data, signal_chan, event_signal, spike_groups = load_file(
    # )
    # w = TraceView(
    #     analog_signal=signal_chan, event_signal=event_signal, spike_groups=spike_groups
    # )
    w = MdiView()
    if len(sys.argv) > 1:
        w.state.loadFile(sys.argv[1])
    # w = SpikeGroupView()
    w.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
