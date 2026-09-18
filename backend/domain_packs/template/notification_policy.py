"""Neutral notification policy for the template pack."""


def title(kind):
    return "处理状态已更新"


def permitted(db, user, event):
    return bool(user and user.active)
