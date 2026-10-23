"""`python -m outreach.api` serves the API; `--check` validates the settings and the store, prints `ok`, and exits."""
import argparse
import sys

import live_checkout
from outreach.api.app import create_app
from outreach.api.settings import DEFAULT_PORT, SettingsError, settings_from_env
from outreach.store import StoreError, connect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m outreach.api", description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate settings and open the store once, then exit")
    args = parser.parse_args(argv)
    try:
        settings = settings_from_env()
        if settings.port == DEFAULT_PORT and not live_checkout.is_live():
            raise SettingsError(
                f"port {DEFAULT_PORT} is the live API's port and only runs from the live checkout "
                f"({live_checkout.LIVE_ROOT}); set OUTREACH_API_PORT to another port"
            )
        app = create_app(settings)  # refuses the live store outside the live checkout
        if args.check:
            connect(settings.db_path).close()
            print("ok")
            return 0
    except (SettingsError, StoreError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    import uvicorn  # only a real run needs the server

    uvicorn.run(app, host=settings.host, port=settings.port, proxy_headers=False, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
