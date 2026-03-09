"""Entry point: python -m hydra_detect [--config config.ini]"""

import argparse

from .pipeline import Pipeline


def main():
    parser = argparse.ArgumentParser(description="Hydra Detect v2.0")
    parser.add_argument(
        "-c", "--config",
        default="config.ini",
        help="Path to config.ini (default: config.ini)",
    )
    args = parser.parse_args()

    pipeline = Pipeline(config_path=args.config)
    pipeline.start()


if __name__ == "__main__":
    main()
