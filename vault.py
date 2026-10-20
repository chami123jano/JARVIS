"""Secrets JARVIS holds for you -- the Gmail app password (Day 9), the calendar's secret
address -- kept in Windows Credential Manager under "JARVIS/...", never in a file, a log or
the database. Readable only by programs running as you.
"""


class Missing(Exception):
    pass


def save(name, user, secret, comment=''):
    import win32cred
    win32cred.CredWrite({'Type': win32cred.CRED_TYPE_GENERIC, 'TargetName': f'JARVIS/{name}',
                         'UserName': user, 'CredentialBlob': secret, 'Comment': comment,
                         'Persist': win32cred.CRED_PERSIST_LOCAL_MACHINE}, 0)


def read(name):
    """(user, secret), or raises Missing."""
    try:
        import win32cred
        found = win32cred.CredRead(f'JARVIS/{name}', win32cred.CRED_TYPE_GENERIC, 0)
    except Exception as error:
        raise Missing(name) from error
    blob = found['CredentialBlob']
    return found['UserName'], blob.decode('utf-16-le') if isinstance(blob, bytes) else str(blob)


def forget(name):
    import win32cred
    try:
        win32cred.CredDelete(f'JARVIS/{name}', win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        pass
