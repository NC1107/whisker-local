import logging

import uvicorn

from .api import create_app_from_env


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Bound inside the container only. compose.yaml decides which host address reaches it.
    uvicorn.run(create_app_from_env(), host="0.0.0.0", port=8080, log_level="warning")  # noqa: S104


if __name__ == "__main__":
    main()
