"""支持 `python -m knowdiskdrive` 运行方式。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
