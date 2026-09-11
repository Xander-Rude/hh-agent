import apply_dispatcher
import apply_worker


def test_extra_hh_success_markers_are_registered():
    for marker in apply_dispatcher.EXTRA_HH_SUCCESS_MARKERS:
        assert marker in apply_worker.SUCCESS_MARKERS
        assert marker in apply_worker.ALREADY_APPLIED_MARKERS
