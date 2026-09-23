"""Known-GOOD fixtures. Every one of these is a legitimate idiom drawn from real, well-maintained
code. agentaudit must NOT flag any of them. These are the false positives it used to produce."""
import contextlib


def is_a_tty(stream):
    """Whether this stream is a terminal.

    `stream` is None under pythonw and in some frozen interpreters, and it can be closed or
    replaced by the time this runs, so a raising check means "not a terminal".
    """
    try:
        return stream is not None and stream.isatty()
    except Exception:
        return False


def _same_value(left, right):
    """Whether two values are interchangeable, treating an unusable `__eq__` as "no"."""
    try:
        return bool(left == right)
    except Exception:
        # A field whose __eq__ raises is not something we can merge.
        return False


def clear_scope(context):
    """Best-effort removal of a cache attribute that may not be present."""
    try:
        delattr(context, "_scope")
    except Exception:
        return


def query(conn):
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1")
    finally:
        cursor.close()


def is_url(source):
    """Whether this string parses as a URL. Pure computation: a string that will not parse is
    not a URL, so False is the true answer and there is no third state."""
    try:
        parsed = urlparse(source)
        return bool(parsed.scheme and parsed.netloc)
    except Exception:
        return False


def validate_connection(service, logger):
    """External call, but it logs - someone can see why it failed."""
    try:
        return len(service.embed_text("test")) > 0
    except Exception as e:
        logger.error(f"Connection validation failed: {e}")
        return False


from urllib.parse import urlparse


def has_permission(user, action, acl):
    """Fail closed. Denying on an error is the correct answer for an authorization check, even
    though the shape matches the bug - the safe direction is the opposite one here."""
    try:
        return acl.check(user, action)
    except Exception:
        return False


async def verify_token(validator, token, audience):
    """Fail closed over the network. A token that cannot be validated is rejected, which is the
    correct answer - flagging this would push a maintainer toward failing open."""
    try:
        return validator.validate_token(token, audience)
    except Exception:
        return None
