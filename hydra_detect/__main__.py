"""Entry point: python -m hydra_detect [--config config.ini]"""

import argparse
import os

from .pipeline import Pipeline


def main():
    parser = argparse.ArgumentParser(description="Hydra Detect v2.0")
    parser.add_argument(
        "-c", "--config",
        default="config.ini",
        help="Path to config.ini (default: config.ini)",
    )
    parser.add_argument(
        "--vehicle",
        default=os.environ.get("HYDRA_VEHICLE"),
        help="Vehicle profile (e.g. drone, usv, ugv). "
             "Overrides base config with [vehicle.<name>] sections. "
             "Can also be set via HYDRA_VEHICLE env var.",
    )
    args = parser.parse_args()

    pipeline = Pipeline(config_path=args.config, vehicle=args.vehicle)
    pipeline.start()

    # Hard exit to prevent "terminate called without an active exception"
    # from CUDA/PyTorch/OpenCV daemon thread cleanup races on Jetson.
    os._exit(0)


if __name__ == "__main__":
    main()
