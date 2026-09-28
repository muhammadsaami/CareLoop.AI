"""
CareLoop AI — Red-flag rule vocabulary (Phase 6)

A leaf module on purpose: it imports nothing from `app`, so it can be imported
during settings validation, which happens while `app.core.database` is still
being imported (and therefore while `app.models` is only partially loaded).
The rule *definitions* live in `app.services.red_flag_rules`; only the stable
code vocabulary - the part that has to be shared by config, persistence, and
the rules themselves - lives here.

WHAT BELONGS HERE AND WHY
`rule_code` is written to the `escalations` table, returned by the API, and
accepted from an operator's configuration.  Three very different layers
therefore need to agree on the same set of strings.  Putting that set in the
service layer would mean `app.core.config` has to import `app.services` at
startup, which creates the import cycle described above and made a settings
typo crash the app at import rather than reporting a configuration error.

Defining the codes here and having the service import them means there is still
exactly ONE list, and `app.services.red_flag_rules` asserts that its rules
cover it - so a rule added without being registered here fails the suite.
"""
from __future__ import annotations

#: Bumping the version is a CLINICAL change, not a refactor: escalations
#: already stored keep the version that produced them, so historical decisions
#: stay interpretable against the rules that actually made them.
RULE_SET_VERSION = "checkin-red-flags-v1"

#: The published rule codes, as plain strings.  `app.services.red_flag_rules`
#: builds the `RedFlagRule` objects from these.
RULE_WARNING_SYMPTOM_WORSENED = "WARNING_SYMPTOM_WORSENED"
RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR = "WARNING_SYMPTOM_AT_SEVERITY_FLOOR"

#: Every valid code for `CHECKIN_ENABLED_RULES`.  Frozen so no caller can
#: mutate the vocabulary at runtime.
RULE_CODES: frozenset[str] = frozenset(
    {
        RULE_WARNING_SYMPTOM_WORSENED,
        RULE_WARNING_SYMPTOM_AT_SEVERITY_FLOOR,
    }
)
