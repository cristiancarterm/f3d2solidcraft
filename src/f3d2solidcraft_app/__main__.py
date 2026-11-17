"""Entry point: the window, `--worker` (the converter's CLI, in this process) and `--check`."""

import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--worker"]:
        from f3d2solidcraft_app.runner import lead_own_group

        lead_own_group()
        # The window reads the worker's output as UTF-8. On Windows a pipe defaults to the ANSI code
        # page, so a path or a body name with an accent arrived as something else, and a character
        # that code page lacks stopped the worker with UnicodeEncodeError.
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors=stream.errors)
        from f3d2solidcraft.cli import main as cli

        return cli(argv[1:])
    if argv[:1] == ["--check"]:
        from f3d2solidcraft_app.engines import check, prepare_environment, saved_solidcraft

        prepare_environment(saved_solidcraft())  # the same saved choice the window uses
        statuses = check()
        for status in statuses:
            print(f"{status.name}: {status.version or '-'} {status.path or ''} {status.problem or 'ok'}")
        from f3d2solidcraft_app.runner import check_worker

        command, problem = check_worker()
        print(f"worker: {' '.join(command[:-1])} {problem or 'ok'}")
        return 0 if all(s.ok for s in statuses) and problem is None else 1
    from f3d2solidcraft_app.window import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
