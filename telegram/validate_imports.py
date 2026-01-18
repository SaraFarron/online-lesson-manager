# validate_imports.py
import sys
import os

def main():
    try:
        from src.core.base import getenv
        print("[SUCCESS] Imports work!")
        return 0
    except Exception as e:
        print("[FAILURE] Import test failed!")
        print(f"Error: {e}")
        print("\nPython path:")
        print("\n".join(sys.path))
        print("\nDirectory contents:")
        print("\n".join(os.listdir(".")))
        print("\nSrc contents:")
        print("\n".join(os.listdir("src")))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())