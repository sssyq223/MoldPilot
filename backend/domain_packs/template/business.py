"""Template transaction component.

A real business pack replaces these operations with its own approval resource
adapter and confirmation handlers.
"""
from app.errors import DomainError


def _unavailable(*args, **kwargs):
    raise DomainError("BUSINESS_PACK_INCOMPLETE", "当前业务包未安装事务处理组件", 501)


approval_detail = _unavailable
create_intent = _unavailable
confirm_intent = _unavailable
