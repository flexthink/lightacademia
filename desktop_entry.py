"""PyInstaller entry point for the desktop application."""
from multiprocessing import freeze_support
from lightacademia.desktop import main

if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
