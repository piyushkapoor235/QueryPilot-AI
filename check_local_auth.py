"""Validate local OIDC configuration without displaying credentials."""

import os
from pathlib import Path
import sys
import tomllib

from dotenv import load_dotenv

from authentication import AuthenticationError, policy_from_settings


def main():
    root = Path(__file__).resolve().parent
    load_dotenv(root / '.env')
    environment = os.getenv('APP_ENV', 'production')
    bypass = os.getenv('DEV_AUTH_BYPASS', 'false').lower() == 'true'
    print('Development environment:', environment == 'development')
    print('Authentication bypass enabled:', bypass)
    if bypass:
        print('FAIL: Disable DEV_AUTH_BYPASS for real login verification.')
        return 1
    try:
        with (root / '.streamlit/secrets.toml').open('rb') as file:
            settings = tomllib.load(file)
    except FileNotFoundError:
        print('FAIL: Local .streamlit/secrets.toml is missing.')
        return 1
    except tomllib.TOMLDecodeError:
        print('FAIL: Local secrets TOML syntax is invalid; check it privately.')
        return 1
    try:
        policy_from_settings(settings, environment)
    except AuthenticationError as error:
        # Policy errors contain only fixed messages and setting names.
        print('FAIL:', error)
        return 1
    print('PASS: Local authentication policy validates. Browser login is still required.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
