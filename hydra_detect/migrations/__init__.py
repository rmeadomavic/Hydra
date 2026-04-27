"""Config schema migrations package.

Each migration is a Python module named NNN_description.py that exports:
    from_version: int
    to_version: int
    def migrate(cfg: configparser.ConfigParser) -> None: ...

The runner in config_migrate.py discovers and applies these in from_version order.
"""
