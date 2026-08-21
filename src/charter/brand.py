BRAND = "foretop"
"""The insulation rule (NEXT_STEPS.md): until the trademark search clears, the brand name lives
in exactly this one constant per app — package prefix, CLI namespace, user-facing strings — and
nowhere else. A rename is then a find-replace, a re-publish, and a GitHub org rename, not a week
of hunting down hardcoded strings. Duplicated per app on purpose, not imported from ebb/telltale:
each product is deliberately standalone."""
