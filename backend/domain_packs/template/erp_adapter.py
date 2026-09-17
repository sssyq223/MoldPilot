"""ERP integration seam for a newly created business pack."""


class ERPClient:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("The active business pack has not configured an ERP adapter")


def encrypt(value):
    raise RuntimeError("The active business pack has not configured ERP credentials")


def decrypt(value):
    raise RuntimeError("The active business pack has not configured ERP credentials")
