"""
Waffle switches for ilmX (pakx) LMS overrides.
"""


from openedx.core.djangoapps.waffle_utils import WaffleSwitchNamespace

WAFFLE_NAMESPACE = u'ilmx'
WAFFLE_SWITCHES = WaffleSwitchNamespace(name=WAFFLE_NAMESPACE)

# When ON, enrolling a learner (any path: self-enroll, admin panel, instructor
# dashboard) creates an approved ManualVerification record and upgrades an audit
# enrollment to verified, so verified-track certificates can be issued. A
# "Verify Enrollment" admin-panel action creates the record on demand.
# When OFF (default) the whole flow is a no-op: no record is auto-created, no
# mode change happens, and certificate availability is not gated on manual
# verification. Manual verification records expire after
# settings.VERIFY_STUDENT["DAYS_GOOD_FOR"] (365 days), so keeping the gate off
# avoids certificates silently lapsing when a record expires.
MANUAL_VERIFICATION_CERT_GATE = u'manual_verification_cert_gate'


def manual_verification_cert_gate_enabled():
    """Return True when the ilmx.manual_verification_cert_gate switch is on."""
    return WAFFLE_SWITCHES.is_enabled(MANUAL_VERIFICATION_CERT_GATE)


# When ON, visits to registered course links (alias slugs / utm sources) are counted.
# When OFF (default) nothing is counted and no cookie is set; registered aliases still serve their page.
COURSE_LINK_TRACKING = u'course_link_tracking'


def course_link_tracking_enabled():
    """Return True when the ilmx.course_link_tracking switch is on."""
    return WAFFLE_SWITCHES.is_enabled(COURSE_LINK_TRACKING)
