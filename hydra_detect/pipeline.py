"""Compatibility shim for source-path based tests.

Runtime imports resolve `hydra_detect.pipeline` to the package directory
(`hydra_detect/pipeline/`), not this file. This shim exists so tools/tests that
read `hydra_detect/pipeline.py` by filesystem path continue to work.
"""

# Intentionally no runtime logic here.
