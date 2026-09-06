# Automatic Z offset calibration with a primary probe and a load cell probe
#
# Copyright (C) 2026  Kalico contributors
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations

from klippy.configfile import ConfigWrapper
from klippy.extras.probe import PrinterProbe
from klippy.gcode import GCodeCommand


class LoadCellAutoZOffset:
    def __init__(self, config: ConfigWrapper):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.gcode_move = self.printer.load_object(config, "gcode_move")
        self.primary_probe_name = config.get("primary_probe", "probe")
        self.secondary_probe_name = config.get(
            "secondary_probe", "load_cell_probe"
        )
        self.center_xy_position = config.getfloatlist(
            "center_xy_position", count=2
        )
        self.travel_speed = config.getfloat("speed", 50.0, above=0.0)
        self.z_hop = config.getfloat("z_hop", 5.0, minval=0.0)
        self.z_hop_speed = config.getfloat("z_hop_speed", 15.0, above=0.0)
        self.offset_adjust = config.getfloat("offset_adjust", 0.0)
        self.offset_min = config.getfloat("offset_min", 0.0)
        self.offset_max = config.getfloat(
            "offset_max", 10.0, above=self.offset_min
        )
        zconfig = config.getsection("stepper_z")
        self.max_z = zconfig.getfloat("position_max", note_valid=False)
        self.last_z_offset = None
        self.gcode.register_command(
            "LOAD_CELL_AUTO_Z_OFFSET",
            self.cmd_LOAD_CELL_AUTO_Z_OFFSET,
            desc=self.cmd_LOAD_CELL_AUTO_Z_OFFSET_help,
        )

    def _get_probe(self, name: str) -> PrinterProbe:
        probe_object = self.printer.lookup_object(name, None)
        if probe_object is None:
            raise self.printer.command_error(
                "Unknown probe object '%s'" % (name,)
            )
        get_printer_probe = getattr(probe_object, "get_printer_probe", None)
        if get_printer_probe is not None:
            probe_object = get_printer_probe()
        if not isinstance(probe_object, PrinterProbe):
            raise self.printer.command_error(
                "Object '%s' is not a compatible probe" % (name,)
            )
        return probe_object

    def _move_probe_to_position(
        self, probe: PrinterProbe, position: list[float]
    ):
        x_offset, y_offset, unused_z_offset = probe.get_offsets()
        self.printer.lookup_object("toolhead").manual_move(
            [position[0] - x_offset, position[1] - y_offset, None],
            self.travel_speed,
        )

    def _hop(self):
        if not self.z_hop:
            return
        toolhead = self.printer.lookup_object("toolhead")
        current_z = toolhead.get_position()[2]
        target_z = min(current_z + self.z_hop, self.max_z)
        if target_z > current_z:
            toolhead.manual_move([None, None, target_z], self.z_hop_speed)

    def _apply_offset(self, primary_probe: PrinterProbe, z_offset: float):
        # A configured probe offset that is too small reports the nozzle below
        # Z=0 at contact. Apply the inverse difference as an absolute G-Code
        # homing origin so repeated calibration commands are idempotent.
        correction = primary_probe.z_offset - z_offset
        offset_gcmd = self.gcode.create_gcode_command(
            "SET_GCODE_OFFSET",
            "SET_GCODE_OFFSET",
            {"Z": correction},
        )
        self.gcode_move.cmd_SET_GCODE_OFFSET(offset_gcmd)

    cmd_LOAD_CELL_AUTO_Z_OFFSET_help = (
        "Calibrate the primary probe Z offset using nozzle contact"
    )

    def cmd_LOAD_CELL_AUTO_Z_OFFSET(self, gcmd: GCodeCommand):
        toolhead = self.printer.lookup_object("toolhead")
        eventtime = self.printer.get_reactor().monotonic()
        homed_axes = toolhead.get_status(eventtime)["homed_axes"]
        if any(axis not in homed_axes for axis in "xyz"):
            raise gcmd.error("Must home X, Y, and Z before calibrating")

        primary_probe = self._get_probe(self.primary_probe_name)
        secondary_probe = self._get_probe(self.secondary_probe_name)
        if primary_probe is secondary_probe:
            raise gcmd.error("Primary and secondary probes must be different")

        position = [
            gcmd.get_float("X", self.center_xy_position[0]),
            gcmd.get_float("Y", self.center_xy_position[1]),
        ]
        offset_adjust = gcmd.get_float("OFFSET_ADJUST", self.offset_adjust)
        apply_offset = gcmd.get_int("APPLY", 0, minval=0, maxval=1)
        save_offset = gcmd.get_int("SAVE", 0, minval=0, maxval=1)

        gcmd.respond_info("Probing with primary probe...")
        self._hop()
        self._move_probe_to_position(primary_probe, position)
        primary_position = primary_probe.run_probe(gcmd)

        gcmd.respond_info("Probing nozzle contact with load cell...")
        self._hop()
        self._move_probe_to_position(secondary_probe, position)
        secondary_position = secondary_probe.run_probe(gcmd)
        self._hop()

        # Account for a non-zero secondary offset so this also works with a
        # generic contact probe. A nozzle load cell normally has z_offset=0.
        secondary_z_offset = secondary_probe.get_offsets()[2]
        z_offset = round(
            primary_position[2]
            - secondary_position[2]
            + secondary_z_offset
            + offset_adjust,
            3,
        )
        if not self.offset_min <= z_offset <= self.offset_max:
            raise gcmd.error(
                "Calculated Z offset %.3f is outside the configured range "
                "%.3f to %.3f" % (z_offset, self.offset_min, self.offset_max)
            )
        self.last_z_offset = z_offset
        gcmd.respond_info(
            "Primary trigger: %.6f\n"
            "Nozzle contact: %.6f\n"
            "Calculated %s z_offset: %.3f"
            % (
                primary_position[2],
                secondary_position[2],
                primary_probe.name,
                z_offset,
            )
        )

        if apply_offset:
            self._apply_offset(primary_probe, z_offset)
            gcmd.respond_info("Applied Z offset for the current session")
        if save_offset:
            configfile = self.printer.lookup_object("configfile")
            configfile.set(primary_probe.name, "z_offset", "%.3f" % z_offset)
            gcmd.respond_info(
                "%s: z_offset: %.3f\n"
                "Run SAVE_CONFIG to save the value and restart."
                % (primary_probe.name, z_offset)
            )

    def get_status(self, eventtime):
        return {"last_z_offset": self.last_z_offset}


def load_config(config):
    return LoadCellAutoZOffset(config)
