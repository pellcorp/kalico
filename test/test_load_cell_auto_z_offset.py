from klippy.extras import load_cell_auto_z_offset
from klippy.extras.probe import PrinterProbe


class _CommandError(Exception):
    pass


class _FakeReactor:
    def monotonic(self):
        return 0.0


class _FakeToolhead:
    def __init__(self):
        self.position = [20.0, 30.0, 1.0, 0.0]
        self.moves = []

    def get_status(self, eventtime):
        return {"homed_axes": "xyz"}

    def get_position(self):
        return list(self.position)

    def manual_move(self, position, speed):
        self.moves.append((list(position), speed))
        for axis, value in enumerate(position):
            if value is not None:
                self.position[axis] = value


class _FakeGCode:
    def create_gcode_command(self, command, commandline, params):
        return _FakeOffsetCommand(params)


class _FakeGCodeMove:
    def __init__(self):
        self.offsets = []

    def cmd_SET_GCODE_OFFSET(self, gcmd):
        self.offsets.append(gcmd.params)


class _FakeOffsetCommand:
    def __init__(self, params):
        self.params = params


class _FakeConfigFile:
    def __init__(self):
        self.values = {}

    def set(self, section, option, value):
        self.values[(section, option)] = value


class _FakePrinter:
    command_error = _CommandError

    def __init__(self, objects):
        self.objects = objects

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return _FakeReactor()


class _FakeGCodeCommand:
    error = _CommandError

    def __init__(self, params):
        self.params = params
        self.messages = []

    def get_float(self, name, default=None, **kwargs):
        return float(self.params.get(name, default))

    def get_int(self, name, default=None, **kwargs):
        return int(self.params.get(name, default))

    def respond_info(self, message):
        self.messages.append(message)


def _make_probe(name, z_offset, offsets, result_z, toolhead):
    printer_probe = PrinterProbe.__new__(PrinterProbe)
    printer_probe.name = name
    printer_probe.z_offset = z_offset
    printer_probe.get_offsets = lambda: offsets

    def run_probe(gcmd):
        toolhead.position[2] = result_z
        return [toolhead.position[0], toolhead.position[1], result_z]

    printer_probe.run_probe = run_probe
    return printer_probe


def test_auto_z_offset_saves_bltouch_and_applies_only_correction():
    toolhead = _FakeToolhead()
    primary = _make_probe("bltouch", 1.5, (10.0, 5.0, 1.5), 1.4, toolhead)
    secondary = _make_probe(
        "load_cell_probe", 0.0, (0.0, 0.0, 0.0), -0.6, toolhead
    )
    gcode = _FakeGCode()
    gcode_move = _FakeGCodeMove()
    configfile = _FakeConfigFile()
    printer = _FakePrinter(
        {
            "toolhead": toolhead,
            "probe": primary,
            "load_cell_probe": secondary,
            "configfile": configfile,
        }
    )

    helper = load_cell_auto_z_offset.LoadCellAutoZOffset.__new__(
        load_cell_auto_z_offset.LoadCellAutoZOffset
    )
    helper.printer = printer
    helper.gcode = gcode
    helper.gcode_move = gcode_move
    helper.primary_probe_name = "probe"
    helper.secondary_probe_name = "load_cell_probe"
    helper.center_xy_position = [100.0, 100.0]
    helper.travel_speed = 50.0
    helper.z_hop = 5.0
    helper.z_hop_speed = 15.0
    helper.max_z = 200.0
    helper.offset_adjust = 0.0
    helper.offset_min = 0.0
    helper.offset_max = 10.0
    helper.last_z_offset = None

    gcmd = _FakeGCodeCommand({"APPLY": 1, "SAVE": 1})
    helper.cmd_LOAD_CELL_AUTO_Z_OFFSET(gcmd)

    assert helper.last_z_offset == 2.0
    assert gcode_move.offsets == [{"Z": -0.5}]
    assert configfile.values[("bltouch", "z_offset")] == "2.000"
    assert ([90.0, 95.0, None], 50.0) in toolhead.moves
    assert ([100.0, 100.0, None], 50.0) in toolhead.moves


def test_auto_z_offset_rejects_result_outside_safety_range():
    toolhead = _FakeToolhead()
    primary = _make_probe("bltouch", 1.0, (0.0, 0.0, 1.0), 5.0, toolhead)
    secondary = _make_probe(
        "load_cell_probe", 0.0, (0.0, 0.0, 0.0), 0.0, toolhead
    )
    printer = _FakePrinter(
        {
            "toolhead": toolhead,
            "probe": primary,
            "load_cell_probe": secondary,
        }
    )
    helper = load_cell_auto_z_offset.LoadCellAutoZOffset.__new__(
        load_cell_auto_z_offset.LoadCellAutoZOffset
    )
    helper.printer = printer
    helper.gcode = _FakeGCode()
    helper.gcode_move = _FakeGCodeMove()
    helper.primary_probe_name = "probe"
    helper.secondary_probe_name = "load_cell_probe"
    helper.center_xy_position = [100.0, 100.0]
    helper.travel_speed = 50.0
    helper.z_hop = 5.0
    helper.z_hop_speed = 15.0
    helper.max_z = 200.0
    helper.offset_adjust = 0.0
    helper.offset_min = 0.0
    helper.offset_max = 2.0
    helper.last_z_offset = None

    gcmd = _FakeGCodeCommand({})
    try:
        helper.cmd_LOAD_CELL_AUTO_Z_OFFSET(gcmd)
    except _CommandError as error:
        assert "outside the configured range" in str(error)
    else:
        raise AssertionError("unsafe offset was accepted")
