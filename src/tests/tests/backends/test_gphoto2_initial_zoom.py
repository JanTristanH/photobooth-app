from unittest.mock import Mock, call

import pytest

try:
    from photobooth.services.backends.gphoto2 import Gphoto2Backend, GpWidgets
except (ImportError, ModuleNotFoundError):
    pytest.skip(reason="gphoto2 not available", allow_module_level=True)

from photobooth.services.config.groups.cameras import GroupCameraGphoto2


def _backend(config: GroupCameraGphoto2, camera: Mock) -> Gphoto2Backend:
    backend = object.__new__(Gphoto2Backend)
    backend._config = config
    backend._camera = camera
    return backend


def _camera_with_zoom(*, minimum: float = 16.0, maximum: float = 50.0, actual: float = 23.0):
    wide_widget = Mock()
    wide_widget.get_type.return_value = GpWidgets.GP_WIDGET_RANGE.value
    wide_widget.get_name.return_value = "zoom"
    wide_widget.get_readonly.return_value = False
    wide_widget.get_value.return_value = 35.0
    wide_widget.get_range.return_value = (minimum, maximum, 1.0)

    target_widget = Mock()
    target_widget.get_type.return_value = GpWidgets.GP_WIDGET_RANGE.value
    target_widget.get_name.return_value = "zoom"
    target_widget.get_readonly.return_value = False
    target_widget.get_value.return_value = minimum

    actual_widget = Mock()
    actual_widget.get_value.return_value = actual

    configs = []
    for widget in (wide_widget, target_widget, actual_widget):
        camera_config = Mock()
        camera_config.get_child_by_name.return_value = widget
        configs.append(camera_config)

    camera = Mock()
    camera.get_config.side_effect = configs
    return camera, wide_widget, target_widget, configs


def test_initial_zoom_is_disabled_by_default():
    camera = Mock()
    backend = _backend(GroupCameraGphoto2(), camera)

    backend._initialize_zoom()

    camera.get_config.assert_not_called()


def test_initial_zoom_moves_to_wide_limit_then_target():
    camera, wide_widget, target_widget, configs = _camera_with_zoom()
    backend = _backend(GroupCameraGphoto2(initial_zoom_enabled=True, initial_zoom_target_mm=23), camera)

    backend._initialize_zoom()

    wide_widget.set_value.assert_called_once_with(16.0)
    target_widget.set_value.assert_called_once_with(23.0)
    assert camera.set_config.call_args_list == [call(configs[0]), call(configs[1])]


def test_initial_zoom_failure_does_not_prevent_camera_startup(caplog):
    camera = Mock()
    camera.get_config.side_effect = RuntimeError("zoom unsupported")
    backend = _backend(GroupCameraGphoto2(initial_zoom_enabled=True), camera)

    backend._initialize_zoom()

    assert "initial zoom failed and will be ignored" in caplog.text


def test_initial_zoom_rejects_target_outside_camera_range(caplog):
    camera, _, _, _ = _camera_with_zoom(maximum=20.0)
    backend = _backend(GroupCameraGphoto2(initial_zoom_enabled=True, initial_zoom_target_mm=23), camera)

    backend._initialize_zoom()

    camera.set_config.assert_not_called()
    assert "outside the camera range" in caplog.text


def test_setup_resource_marks_each_new_connection_for_device_init(monkeypatch):
    camera = Mock()
    monkeypatch.setattr("photobooth.services.backends.gphoto2.gp.Camera", Mock(return_value=camera))

    backend = _backend(GroupCameraGphoto2(), Mock())
    backend._mode_machine = Mock(active_mode="video")

    backend.setup_resource()

    camera.init.assert_called_once_with()
    assert backend._mode_machine.active_mode is None
