"""
Whether this process was *deployed* correctly.

That is a different question from whether it works, which the test suite
answers, and it cannot be answered by a test: every problem here is a
value in the environment, correct in development and wrong in production,
and each one fails in a way nobody can diagnose from the outside.

  * a JWT signed with a publicly-known key looks exactly like a real one;
  * a browser blocked by CORS shows a login that silently does nothing;
  * an inbound patient reply whose signature cannot verify is dropped,
    and the endpoint answers 200 by design, so Twilio reports success;
  * every client behind an untrusted proxy shares one rate-limit bucket.

**Everything is reported before anything raises.** A deployer who fixes
CORS and restarts should not then discover the secret key, and again the
public URL -- three restarts to learn what one could have said. That is
the same reasoning as `startup_problems()` in the voice gateway, and it
is why this returns a list rather than raising as it goes.

Deliberately *not* here: the worker-count check. It lives in
`app/instance.py` with the runtime state it also owns (the peer set the
scheduler observes), it reads argv rather than settings, and it has an
escape hatch of its own. Two detectors for one problem is its whole
design; folding half of it in here would split that argument across two
files.
"""

import logging
from typing import NamedTuple

FATAL = "fatal"
WARNING = "warning"


class Problem(NamedTuple):
    severity: str
    setting: str
    message: str


def problems(settings) -> list[Problem]:
    """
    Every deployment problem this process can see in its own configuration.

    Takes `settings` rather than importing it, so a test can ask about a
    deployment other than this one.
    """
    found: list[Problem] = []
    production = settings.is_production

    if settings.uses_default_secret_key:
        if production:
            found.append(Problem(
                FATAL, "SECRET_KEY",
                "SECRET_KEY is still the default placeholder value. Every "
                "session token would be signed with a key published in this "
                "repository, so anyone could mint one. Set a real, random "
                "SECRET_KEY before running with ENVIRONMENT=production.",
            ))
        else:
            found.append(Problem(
                WARNING, "SECRET_KEY",
                "SECRET_KEY is using the default placeholder value - fine for "
                "local development, but set a real random SECRET_KEY before "
                "deploying.",
            ))

    if production and settings.cors_is_local_only:
        found.append(Problem(
            FATAL, "CORS_ORIGINS",
            f"CORS_ORIGINS is still local-only ({settings.cors_origins!r}). "
            "The browser will block every API call from the deployed "
            "dashboard - which presents as a login that does nothing rather "
            "than as an error - and because staff invitations and "
            "password-reset links are built from the FIRST origin here, both "
            "would email a link to localhost. Set it to the URL the dashboard "
            "is actually served from.",
        ))

    if production and not settings.public_base_url.strip():
        found.append(Problem(
            WARNING, "PUBLIC_BASE_URL",
            "PUBLIC_BASE_URL is empty. Inbound patient replies (CONFIRM / "
            "CANCEL / STOP) verify Twilio's signature against the URL this "
            "process sees, which behind a proxy is not the URL Twilio signed "
            "- so every reply fails verification and is dropped, and the "
            "endpoint answers 200 by design so nothing reports it. Set it to "
            "the public https URL of this API.",
        ))

    if production and not settings.trusted_proxy_networks:
        found.append(Problem(
            WARNING, "TRUSTED_PROXY_IPS",
            "TRUSTED_PROXY_IPS is empty. X-Forwarded-For will be ignored and "
            "rate limiting will key on the direct peer address. That is "
            "correct when the API is reached directly; behind a reverse proxy "
            "it puts every client in one bucket. Set it if there is a proxy.",
        ))

    return found


def check(settings, logger: logging.Logger) -> None:
    """
    Report every problem, then refuse to start if any of them is fatal.

    Fatal for the same reason the app refuses a bad database: a process
    that starts in this state is worse than one that does not, because it
    looks like it is working.
    """
    found = problems(settings)
    for p in found:
        if p.severity == FATAL:
            logger.error("%s: %s", p.setting, p.message)
        else:
            logger.warning("%s: %s", p.setting, p.message)

    fatal = [p for p in found if p.severity == FATAL]
    if fatal:
        raise RuntimeError(
            "Refusing to start: "
            + " ".join(f"[{p.setting}] {p.message}" for p in fatal)
        )
