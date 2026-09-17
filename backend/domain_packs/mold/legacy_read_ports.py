"""Temporary adapters for mold read services not yet moved into this pack.

Keeping the migration debt in one module makes the dependency direction
visible.  Application services must depend on these named ports, not reach
back into arbitrary ``app`` modules themselves.
"""


def business_subject_data(db, user, subject):
    from app.domains import data

    return data(db, user, subject)


def purchase_order_data(db, user, order):
    from app.procurement import order_data

    return order_data(db, user, order)


def contact_case_permitted(db, user, action, case):
    from app.contacts import permitted

    return permitted(db, user, action, case)
