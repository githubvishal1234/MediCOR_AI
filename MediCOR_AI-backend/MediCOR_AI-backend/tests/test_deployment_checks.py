"""
The deployment checks (production readiness).

Everything here is a value in the environment that is *correct* in
development and wrong in production, which is why none of it could be
caught by the rest of the suite: the suite runs with
ENVIRONMENT=development, where every one of these values is right.

The reason they are checked at all is that each fails invisibly. A
localhost CORS origin does not error - the browser silently blocks the
call and the dashboard shows a login that does nothing. An empty
PUBLIC_BASE_URL does not error - inbound patient replies fail signature
verification and the endpoint answers 200 by design, so Twilio reports
success and nobody is told that CONFIRM and CANCEL stopped working.

    pytest tests/test_deployment_checks.py -v
"""

import os
import sys

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

import pytest  # noqa: E402

from app import deployment  # noqa: E402
from app.config import DEFAULT_SECRET_KEY, Settings, _host_of  # noqa: E402


def make(**overrides) -> Settings:
    """A deployment described entirely by its settings, not by this machine's."""
    base = dict(
        db_server="unused",
        db_port=1433,
        db_name="unused",
        db_user="unused",
        db_password="unused",
        secret_key="a-real-random-secret",
        environment="production",
        cors_origins="https://app.clinic.example",
        public_base_url="https://api.clinic.example",
        trusted_proxy_ips="10.0.0.0/8",
    )
    base.update(overrides)
    return Settings(**base)


def settings_of(found):
    return {p.setting for p in found}


def fatal_of(found):
    return {p.setting for p in found if p.severity == deployment.FATAL}


# ---------------------------------------------------------------------
# Which origins mean "this machine"
# ---------------------------------------------------------------------


class TestLocalOriginDetection:
    @pytest.mark.parametrize(
        "origins",
        [
            "http://localhost:5173,http://localhost:3000",   # the shipped default
            "http://127.0.0.1:8000",
            "http://0.0.0.0:8000",
            "http://[::1]:8000",
            "localhost:5173",                                # no scheme
            "",                                              # nothing configured
        ],
    )
    def test_local_origins_are_recognised(self, origins):
        assert make(cors_origins=origins).cors_is_local_only is True

    @pytest.mark.parametrize(
        "origins",
        [
            "https://app.clinic.co.in",
            "http://localhost:5173,https://app.clinic.co.in",  # one real one is enough
            "https://10.4.2.9",                                # a LAN address is not this machine
        ],
    )
    def test_a_reachable_origin_is_not_local(self, origins):
        assert make(cors_origins=origins).cors_is_local_only is False

    def test_a_domain_that_merely_contains_localhost_is_not_local(self):
        """
        The check compares the parsed host, never a substring.

        A substring test -- `"//localhost" in origin` -- is the obvious
        way to write this, and it refuses to start a clinic whose domain
        happens to read that way. Refusing to boot a correctly configured
        deployment is a worse failure than the one being prevented.
        """
        s = make(cors_origins="https://localhost-clinic.example.com")
        assert s.cors_is_local_only is False
        assert _host_of("https://localhost-clinic.example.com") == "localhost-clinic.example.com"

    def test_the_host_is_parsed_not_pattern_matched(self):
        assert _host_of("http://localhost:5173") == "localhost"
        assert _host_of("localhost:5173") == "localhost"
        assert _host_of("https://user:pw@0.0.0.0:9/path") == "0.0.0.0"
        assert _host_of("http://[::1]:8000") == "[::1]"


# ---------------------------------------------------------------------
# What is fatal, what is a warning, and what is silent
# ---------------------------------------------------------------------


class TestWhatStopsAStart:
    def test_a_correctly_configured_production_deployment_has_no_problems(self):
        assert deployment.problems(make()) == []

    def test_a_default_secret_key_is_fatal_in_production(self):
        found = deployment.problems(make(secret_key=DEFAULT_SECRET_KEY))
        assert fatal_of(found) == {"SECRET_KEY"}

    def test_local_only_cors_is_fatal_in_production(self):
        """
        Fatal rather than a warning, because it breaks two things at once
        and one of them leaves the building: the dashboard cannot reach
        the API at all, and staff invitations and password-reset emails
        are built from the first origin in this list, so they would carry
        a link to localhost in the clinic's name.
        """
        found = deployment.problems(make(cors_origins="http://localhost:5173"))
        assert fatal_of(found) == {"CORS_ORIGINS"}

    def test_an_empty_public_base_url_warns_but_does_not_stop_the_start(self):
        """
        Not fatal: a deployment that never sends patient messages never
        receives a reply, and an API that refuses to serve calls over
        something it does not use would be the worse failure.
        """
        found = deployment.problems(make(public_base_url=""))
        assert settings_of(found) == {"PUBLIC_BASE_URL"}
        assert fatal_of(found) == set()

    def test_an_empty_trusted_proxy_list_warns_but_does_not_stop_the_start(self):
        """Running the API directly, with no proxy, is a valid deployment."""
        found = deployment.problems(make(trusted_proxy_ips=""))
        assert settings_of(found) == {"TRUSTED_PROXY_IPS"}
        assert fatal_of(found) == set()

    def test_every_problem_is_reported_before_any_of_them_raises(self):
        """
        The point of collecting rather than raising as we go. A deployer
        who fixes CORS and restarts must not then discover the secret key,
        and restart again to discover the public URL.
        """
        found = deployment.problems(
            make(
                secret_key=DEFAULT_SECRET_KEY,
                cors_origins="http://localhost:5173",
                public_base_url="",
                trusted_proxy_ips="",
            )
        )
        assert settings_of(found) == {
            "SECRET_KEY", "CORS_ORIGINS", "PUBLIC_BASE_URL", "TRUSTED_PROXY_IPS",
        }


class TestDevelopmentIsUnaffected:
    """
    The whole suite, and every developer's laptop, runs like this. If any
    of these checks fired here they would be deleted within the day.
    """

    def test_localhost_origins_are_fine_in_development(self):
        found = deployment.problems(
            make(environment="development", cors_origins="http://localhost:5173")
        )
        assert settings_of(found) == set()

    def test_an_empty_public_base_url_is_fine_in_development(self):
        found = deployment.problems(make(environment="development", public_base_url=""))
        assert settings_of(found) == set()

    def test_the_default_secret_key_still_warns_in_development(self):
        """
        The one check that speaks up either way, because this is the value
        that must not travel to production, and the warning is what a
        developer sees on every single run.
        """
        found = deployment.problems(
            make(environment="development", secret_key=DEFAULT_SECRET_KEY)
        )
        assert settings_of(found) == {"SECRET_KEY"}
        assert fatal_of(found) == set()

    def test_this_machines_own_settings_start_cleanly(self):
        """
        The suite imports `app.main`, which calls `deployment.check`. If
        the live configuration ever became fatal, every test in the suite
        would fail at collection with the same unhelpful error -- so
        assert it directly and fail here instead.
        """
        from app.config import settings as live

        assert [p for p in deployment.problems(live) if p.severity == deployment.FATAL] == []


class TestCheckRaisesAndLogs:
    def test_check_raises_on_a_fatal_problem_and_names_the_setting(self):
        with pytest.raises(RuntimeError) as excinfo:
            deployment.check(
                make(cors_origins="http://localhost:5173"), logging.getLogger("test")
            )
        assert "CORS_ORIGINS" in str(excinfo.value)

    def test_check_returns_quietly_when_only_warnings_are_present(self, caplog):
        with caplog.at_level(logging.WARNING, logger="test"):
            deployment.check(make(public_base_url=""), logging.getLogger("test"))
        assert "PUBLIC_BASE_URL" in caplog.text

    def test_a_fatal_problem_is_logged_as_well_as_raised(self, caplog):
        """
        Raising alone loses the other problems: the RuntimeError names the
        fatal ones, and a deployer restarting to fix them should already
        have been told about the warnings too.
        """
        with caplog.at_level(logging.WARNING, logger="test"):
            with pytest.raises(RuntimeError):
                deployment.check(
                    make(cors_origins="http://localhost:5173", public_base_url=""),
                    logging.getLogger("test"),
                )
        assert "PUBLIC_BASE_URL" in caplog.text
        assert "CORS_ORIGINS" in caplog.text
