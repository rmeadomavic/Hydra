"""Smoke tests for detector base classes."""

from hydra_detect.detectors.base import Detection, DetectionResult


def test_detection_properties():
    d = Detection(x1=10, y1=20, x2=50, y2=80, confidence=0.9, class_id=0, label="person")
    assert d.bbox == (10, 20, 50, 80)
    assert d.center == (30.0, 50.0)
    assert d.area == 40 * 60


def test_detection_result_iterable():
    dr = DetectionResult(
        detections=[
            Detection(0, 0, 10, 10, 0.5, 0),
            Detection(20, 20, 30, 30, 0.8, 1),
        ],
        inference_ms=12.3,
    )
    assert len(dr) == 2
    labels = [d.class_id for d in dr]
    assert labels == [0, 1]


class TestGetClassNames:
    def test_returns_empty_when_no_model(self):
        from hydra_detect.detectors.yolo_detector import YOLODetector
        det = YOLODetector()
        assert det.get_class_names() == []

    def test_returns_names_from_model(self):
        from unittest.mock import MagicMock
        from hydra_detect.detectors.yolo_detector import YOLODetector
        det = YOLODetector()
        det._model = MagicMock()
        det._model.names = {0: "person", 1: "car", 2: "truck"}
        assert det.get_class_names() == ["person", "car", "truck"]


class TestSetClasses:
    def test_set_classes_updates_filter(self):
        from hydra_detect.detectors.yolo_detector import YOLODetector
        det = YOLODetector(model_path="yolov8n.pt", confidence=0.5)
        assert det._classes is None
        det.set_classes([0, 2, 7])
        assert det._classes == [0, 2, 7]

    def test_set_classes_none_clears_filter(self):
        from hydra_detect.detectors.yolo_detector import YOLODetector
        det = YOLODetector(model_path="yolov8n.pt", confidence=0.5, classes=[0, 1])
        det.set_classes(None)
        assert det._classes is None


class TestApplyDevicePlacement:
    """`.to("cuda:0")` is PyTorch-only. TRT (.engine) and ONNX (.onnx) backends
    reject it. Make sure the gate skips non-.pt model paths."""

    def test_engine_path_skips_to_call(self):
        from unittest.mock import MagicMock
        from hydra_detect.detectors.yolo_detector import YOLODetector
        det = YOLODetector()
        model = MagicMock()
        det._apply_device_placement(model, "yolov8n.engine")
        model.to.assert_not_called()

    def test_onnx_path_skips_to_call(self):
        from unittest.mock import MagicMock
        from hydra_detect.detectors.yolo_detector import YOLODetector
        det = YOLODetector()
        model = MagicMock()
        det._apply_device_placement(model, "models/yolov8n.onnx")
        model.to.assert_not_called()

    def test_torchscript_path_skips_to_call(self):
        from unittest.mock import MagicMock
        from hydra_detect.detectors.yolo_detector import YOLODetector
        det = YOLODetector()
        model = MagicMock()
        det._apply_device_placement(model, "models/yolov8n.torchscript")
        model.to.assert_not_called()

    def test_pt_path_calls_to_when_cuda_available(self):
        # Only meaningful when torch is importable (Jetson runtime / CI with torch).
        # Local laptop runs without torch — skip cleanly.
        import pytest
        pytest.importorskip("torch")
        from unittest.mock import MagicMock, patch
        from hydra_detect.detectors.yolo_detector import YOLODetector
        det = YOLODetector()
        model = MagicMock()
        with patch("torch.cuda.is_available", return_value=True):
            det._apply_device_placement(model, "yolov8n.pt")
        model.to.assert_called_once_with("cuda:0")

    def test_pt_path_skips_to_when_cuda_unavailable(self):
        import pytest
        pytest.importorskip("torch")
        from unittest.mock import MagicMock, patch
        from hydra_detect.detectors.yolo_detector import YOLODetector
        det = YOLODetector()
        model = MagicMock()
        with patch("torch.cuda.is_available", return_value=False):
            det._apply_device_placement(model, "yolov8n.pt")
        model.to.assert_not_called()
